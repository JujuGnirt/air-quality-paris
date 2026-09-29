"""
REST API with Flask
-------------------
Reads the MySQL database (filled by load_mysql.py) and sends the data as JSON.

Endpoints (2 resources, 4 endpoints):
    GET /stations                  list of stations (filters: type, name)
    GET /stations/<station_code>   one station + stats per pollutant
    GET /measurements              list of daily measurements
                                   (filters: station, pollutant, start, end, min_value)
    GET /measurements/<id>         one measurement + station, pollutant, weather, episodes
    GET /                          documentation page (public)
    GET /openapi.yaml              OpenAPI specification (public)

Authentication: the data endpoints need the API key (line API_KEY= in
secrets/keys.env). Send it in the header X-API-Key, or for a quick test in the
browser add ?api_key=... at the end of the URL. No key or wrong key -> 401.

Run: python src/api_flask.py  then open http://127.0.0.1:5000
(in Jupyter: %run ../src/api_flask.py, stop with "Interrupt kernel")
"""
import math
import os
import sys
from datetime import date
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, url_for
from sqlalchemy import text

# so that "import config" also works from a notebook (not only from the terminal):
# go up the folders until we find src/config.py and add src/ to sys.path
folder = Path.cwd()
while not (folder / "src" / "config.py").exists() and folder != folder.parent:
    folder = folder.parent
if str(folder / "src") not in sys.path:
    sys.path.append(str(folder / "src"))

import config  # this also reads secrets/keys.env
from load_mysql import connect

PER_PAGE_DEFAULT = 20
PER_PAGE_MAX = 100   # to never send thousands of rows at once
WHO_LIMIT = 15       # WHO daily guideline for PM2.5 (µg/m³)
STATION_TYPES = ["urban_background", "traffic", "rural", "observation"]

app = Flask(__name__)
app.json.sort_keys = False   # keep the keys in my order in the JSON

# connection to MySQL, created at the first request
engine = None


def run_query(sql, params=None):
    # runs a SELECT and returns a list of dicts
    global engine
    if engine is None:
        engine = connect()
    if params is None:
        params = {}
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    result = []
    for row in rows:
        result.append(clean_row(row))
    return result


def clean_row(row):
    # dates -> "YYYY-MM-DD", floats rounded, Decimal (from AVG in MySQL) -> float
    new_row = {}
    for key, value in dict(row).items():
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, float):
            value = round(value, 2)
        elif value is not None and not isinstance(value, (int, str)):
            value = float(value)
        new_row[key] = value
    return new_row


def get_page_params():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", PER_PAGE_DEFAULT, type=int)
    if page < 1 or per_page < 1:
        abort(400, description="page and per_page must be positive integers")
    if per_page > PER_PAGE_MAX:
        per_page = PER_PAGE_MAX
    return page, per_page


def get_date_param(name):
    value = request.args.get(name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        abort(400, description=f"{name} must be a date in the format YYYY-MM-DD")


def make_page_answer(endpoint, rows, total, page, per_page, filters):
    # same structure for the 2 list endpoints
    total_pages = max(1, math.ceil(total / per_page))

    # only keep the filters that were used
    used_filters = {}
    for key, value in filters.items():
        if value is not None:
            used_filters[key] = value

    next_page = None
    if page < total_pages:
        next_page = url_for(endpoint, page=page + 1, per_page=per_page, **used_filters)
    previous_page = None
    if page > 1:
        previous_page = url_for(endpoint, page=page - 1, per_page=per_page, **used_filters)
    # note: the api key is not put in these links, the client sends it again

    return {
        "page": page,
        "per_page": per_page,
        "total_items": total,
        "total_pages": total_pages,
        "next_page": next_page,
        "previous_page": previous_page,
        "filters": used_filters,
        "data": rows,
    }


# ---------------------------------------------------------------
# API key check (before each request)
# ---------------------------------------------------------------
@app.before_request
def check_api_key():
    # the doc pages are public (no data in them)
    if request.endpoint in ["documentation", "openapi_file", "static"] or request.endpoint is None:
        return None

    # key expected: API_KEY in keys.env (or app.config if set)
    good_key = app.config.get("API_KEY") or os.getenv("API_KEY", "").strip()
    if good_key == "":
        abort(503, description="API_KEY is missing: add the line API_KEY=... to secrets/keys.env")

    key = request.headers.get("X-API-Key")
    if key is None:
        key = request.args.get("api_key")   # browser test
    if key != good_key:
        abort(401, description="missing or invalid API key: send the header X-API-Key")
    return None


# errors also in JSON
@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(503)
def json_error(error):
    return jsonify({"error": error.code, "message": error.description}), error.code


# ---------------------------------------------------------------
# Resource 1: stations
# ---------------------------------------------------------------
@app.route("/stations")
def list_stations():
    page, per_page = get_page_params()
    station_type = request.args.get("type")
    name = request.args.get("name")
    if station_type is not None and station_type not in STATION_TYPES:
        abort(400, description=f"type must be one of {STATION_TYPES}")

    # WHERE built with fixed text; the values go in params (no SQL injection)
    conditions = []
    params = {}
    if station_type:
        conditions.append("station_type = :type")
        params["type"] = station_type
    if name:
        conditions.append("LOWER(station_name) LIKE :name")
        params["name"] = "%" + name.lower() + "%"
    where = ""
    if len(conditions) > 0:
        where = "WHERE " + " AND ".join(conditions)

    total = run_query("SELECT COUNT(*) AS n FROM station " + where, params)[0]["n"]

    params["limit"] = per_page
    params["offset"] = (page - 1) * per_page
    rows = run_query("SELECT station_code, station_name, station_type FROM station " + where +
                     " ORDER BY station_code LIMIT :limit OFFSET :offset", params)
    for row in rows:
        row["url"] = url_for("get_station", station_code=row["station_code"])

    filters = {"type": station_type, "name": name}
    return jsonify(make_page_answer("list_stations", rows, total, page, per_page, filters))


@app.route("/stations/<station_code>")
def get_station(station_code):
    found = run_query("SELECT station_id, station_code, station_name, station_type "
                      "FROM station WHERE station_code = :code",
                      {"code": station_code.upper()})
    if len(found) == 0:
        abort(404, description=f"station {station_code} not found")
    station = found[0]

    # nested details: one line per pollutant measured at this station
    station["pollutants"] = run_query("""
        SELECT p.pollutant_code, p.unit,
               COUNT(*) AS days_measured,
               MIN(m.date) AS first_day,
               MAX(m.date) AS last_day,
               AVG(m.mean) AS overall_mean,
               SUM(CASE WHEN p.pollutant_code = 'PM25' AND m.mean > :who THEN 1 ELSE 0 END)
                   AS days_above_who_pm25
        FROM daily_measurement m
        JOIN pollutant p ON p.pollutant_id = m.pollutant_id
        WHERE m.station_id = :station_id
        GROUP BY p.pollutant_code, p.unit
        ORDER BY p.pollutant_code""",
        {"station_id": station["station_id"], "who": WHO_LIMIT})

    for pol in station["pollutants"]:
        if pol["pollutant_code"] != "PM25":
            del pol["days_above_who_pm25"]   # the WHO limit used here is only for PM2.5
        pol["measurements_url"] = url_for("list_measurements", station=station["station_code"],
                                          pollutant=pol["pollutant_code"])
    del station["station_id"]   # internal id, not useful for the user
    return jsonify(station)


# ---------------------------------------------------------------
# Resource 2: measurements
# ---------------------------------------------------------------
@app.route("/measurements")
def list_measurements():
    page, per_page = get_page_params()
    filters = {
        "station": request.args.get("station"),
        "pollutant": request.args.get("pollutant"),
        "start": get_date_param("start"),
        "end": get_date_param("end"),
        "min_value": request.args.get("min_value", type=float),
    }

    conditions = []
    params = {}
    if filters["station"]:
        conditions.append("s.station_code = :station")
        params["station"] = filters["station"].upper()
    if filters["pollutant"]:
        conditions.append("p.pollutant_code = :pollutant")
        params["pollutant"] = filters["pollutant"].upper()
    if filters["start"]:
        conditions.append("m.date >= :start")
        params["start"] = filters["start"]
    if filters["end"]:
        conditions.append("m.date <= :end")
        params["end"] = filters["end"]
    if filters["min_value"] is not None:
        conditions.append("m.mean >= :min_value")
        params["min_value"] = filters["min_value"]
    where = ""
    if len(conditions) > 0:
        where = "WHERE " + " AND ".join(conditions)

    joins = """ FROM daily_measurement m
        JOIN station s ON s.station_id = m.station_id
        JOIN pollutant p ON p.pollutant_id = m.pollutant_id """

    total = run_query("SELECT COUNT(*) AS n" + joins + where, params)[0]["n"]

    params["limit"] = per_page
    params["offset"] = (page - 1) * per_page
    rows = run_query("SELECT m.measurement_id, m.date, s.station_code, p.pollutant_code, "
                     "m.mean, m.max, m.n_hours, p.unit" + joins + where +
                     " ORDER BY m.date DESC, s.station_code, p.pollutant_code"
                     " LIMIT :limit OFFSET :offset", params)
    for row in rows:
        row["url"] = url_for("get_measurement", measurement_id=row["measurement_id"])

    return jsonify(make_page_answer("list_measurements", rows, total, page, per_page, filters))


@app.route("/measurements/<int:measurement_id>")
def get_measurement(measurement_id):
    found = run_query("""
        SELECT m.measurement_id, m.date, m.mean, m.max, m.n_hours,
               s.station_code, s.station_name, s.station_type,
               p.pollutant_code, p.pollutant_name, p.unit
        FROM daily_measurement m
        JOIN station s ON s.station_id = m.station_id
        JOIN pollutant p ON p.pollutant_id = m.pollutant_id
        WHERE m.measurement_id = :id""", {"id": measurement_id})
    if len(found) == 0:
        abort(404, description=f"measurement {measurement_id} not found")
    row = found[0]

    # context of the day, from the other tables
    weather = run_query("SELECT temp_mean_c, rain_mm, wind_mean_ms, pressure_mean_hpa "
                        "FROM weather_day WHERE date = :date", {"date": row["date"]})
    episodes = run_query("SELECT p.pollutant_code, e.level, e.source, e.forecast_result "
                         "FROM episode e JOIN pollutant p ON p.pollutant_id = e.pollutant_id "
                         "WHERE e.date = :date", {"date": row["date"]})

    weather_of_the_day = None
    if len(weather) > 0:
        weather_of_the_day = weather[0]

    answer = {
        "measurement_id": row["measurement_id"],
        "date": row["date"],
        "daily_mean": row["mean"],
        "hourly_max": row["max"],
        "hours_measured": row["n_hours"],
        "station": {
            "code": row["station_code"],
            "name": row["station_name"],
            "type": row["station_type"],
            "url": url_for("get_station", station_code=row["station_code"]),
        },
        "pollutant": {
            "code": row["pollutant_code"],
            "name": row["pollutant_name"],
            "unit": row["unit"],
        },
        "weather_of_the_day": weather_of_the_day,
        "episodes_of_the_day": episodes,
    }
    if row["pollutant_code"] == "PM25":
        answer["above_who_guideline"] = row["mean"] > WHO_LIMIT
    return jsonify(answer)


# ---------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------
@app.route("/openapi.yaml")
def openapi_file():
    return send_file(config.ROOT / "docs" / "openapi.yaml", mimetype="application/yaml")


# method, endpoint, description, parameters, example
DOC = [
    ["GET", "/stations", "List of stations, paginated.",
     "type (urban_background, traffic, rural, observation), name (part of the name), page, per_page",
     "/stations?type=traffic&page=1&per_page=5"],
    ["GET", "/stations/&lt;station_code&gt;",
     "One station, with a summary per pollutant (days measured, period, mean, days above the WHO PM2.5 guideline).",
     "—", "/stations/PA01H"],
    ["GET", "/measurements", "Daily measurements, paginated, most recent first.",
     "station, pollutant (PM25, NO2, O3, PM10, NOX, CO), start and end (YYYY-MM-DD), min_value, page, per_page",
     "/measurements?station=PA01H&pollutant=PM25&start=2025-01-01&min_value=15"],
    ["GET", "/measurements/&lt;id&gt;",
     "One daily measurement, with the station, the pollutant, the weather of the day and the official episodes of the day.",
     "—", "/measurements/1"],
]


@app.route("/")
def documentation():
    table_rows = ""
    for method, path, what, params, example in DOC:
        table_rows += (f"<tr><td><code>{method}</code></td><td><code>{path}</code></td>"
                       f"<td>{what}</td><td>{params}</td><td><a href='{example}'>{example}</a></td></tr>")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Air Quality Paris API</title>
<style>
 body {{ font-family: Arial, sans-serif; margin: 40px; color: #222; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
 th {{ background: #f0f0f0; }}
</style></head><body>
<h1>Air Quality Paris — REST API</h1>
<p>Daily air-quality measurements from Airparif stations (2018-2026), stored in MySQL.
All answers are JSON. Lists are paginated (default {PER_PAGE_DEFAULT}, maximum {PER_PAGE_MAX}
items per page) and give links to the next and previous pages.
Errors return JSON with a code (400 = invalid parameter, 401 = missing or invalid API key,
404 = not found, 503 = API key not configured on the server) and a message.</p>
<h2>Authentication</h2>
<p>Every data endpoint requires an API key, sent in the HTTP header <code>X-API-Key</code>.
Example: <code>curl -H "X-API-Key: YOUR_KEY" http://127.0.0.1:5000/stations</code>.
For a quick test in a browser only, add <code>?api_key=YOUR_KEY</code> to the URL (less safe:
URLs are stored in the browser history). Without a valid key the answer is <code>401</code>.
This page and the <a href="/openapi.yaml">OpenAPI 3 specification</a> are public: they contain no data.</p>
<h2>Endpoints</h2>
<table><tr><th>Method</th><th>Endpoint</th><th>Description</th><th>Parameters</th><th>Example</th></tr>
{table_rows}</table>
<p>Sources: Airparif (ODbL licence), Météo-France (Licence Ouverte).</p>
</body></html>"""


if __name__ == "__main__":
    # debug=False because the auto-reload of debug mode does not work in a notebook
    app.run(host="127.0.0.1", port=5000, debug=False)
