"""
SOURCE 4 — RELATIONAL DATABASE: loading into MySQL
==================================================

Why load from Python (SQLAlchemy) rather than with the MySQL Workbench
import wizard?
    The wizard struggles with large files, dates, accents and empty values.
    With pandas + SQLAlchemy the types are under control, and the loading is
    reproducible (just run the script again) and documented.

Steps:
    1. Prepare the tables in pandas: numeric identifiers (station_id,
       pollutant_id) replace the codes in the fact table (NORMALISATION).
    2. Create the database if it does not exist.
    3. Create the tables with sql/schema.sql (primary keys, foreign keys,
       indexes), so that MySQL Workbench can draw the entity-relationship
       diagram by reverse engineering.
    4. Insert the data: dimensions first, then facts (a foreign key must
       point to a row that already exists).
    5. Check that MySQL contains as many rows as the DataFrames.

Run:       python src/load_mysql.py   (or run this file in Jupyter)
Password:  line MYSQL_PASSWORD= of secrets/keys.env (never in the code)
Requires:  pip install sqlalchemy pymysql ; the collection steps done.
"""
import pandas as pd

# --- To be able to run this file in Jupyter Notebook too -----------------------
# Python looks for modules (such as config.py) in the folders listed in
# sys.path. In a terminal, the script's folder (src/) is already there; in a
# notebook it is not. So we walk up from the current folder to the project,
# then add src/ to that list.
import sys
from pathlib import Path

folder = Path.cwd()
while not (folder / "src" / "config.py").exists() and folder != folder.parent:
    folder = folder.parent
if str(folder / "src") not in sys.path:
    sys.path.append(str(folder / "src"))
# -------------------------------------------------------------------------------

import config
from episodes import combine_episodes

SCHEMA_FILE = config.ROOT / "sql" / "schema.sql"

# Same names as the columns produced by api_meteofrance.py
WEATHER_COLUMNS = [
    "date", "temp_mean_c", "temp_min_c", "temp_max_c", "humidity_mean_pct",
    "pressure_mean_hpa", "rain_mm", "wind_mean_ms", "wind_max_ms", "wind_dir_deg",
]
EPISODE_COLUMNS = ["date", "pollutant_id", "level", "source", "forecast", "observed",
                   "forecast_result", "criterion_population", "criterion_area"]
# Order of insertion: a table is inserted after the tables it points to
TABLE_ORDER = ["station", "pollutant", "calendar", "weather_day",
               "daily_measurement", "episode"]


def read_if_exists(path: Path, **kwargs):
    """Read a CSV if it exists; otherwise return None and say so."""
    if path.exists():
        return pd.read_csv(path, **kwargs)
    print(f"   [!] {path.name} missing: the corresponding table will stay empty")
    return None


# -------------------------------------------------------------------------------
# Step 1: prepare the tables in pandas
# -------------------------------------------------------------------------------
def prepare_tables() -> dict[str, pd.DataFrame]:
    P = config.DATA_PROCESSED
    tables = {}

    # --- Station and pollutant dimensions: create a numeric identifier.
    # Why an id rather than the code (PA01H) as key? An integer is smaller and
    # faster to join, and the code can change without breaking the links.
    station = pd.read_csv(P / "stations.csv")
    station.insert(0, "station_id", range(1, len(station) + 1))
    tables["station"] = station[["station_id", "station_code", "station_name", "station_type"]]

    pollutant = pd.read_csv(P / "pollutants.csv")
    pollutant.insert(0, "pollutant_id", range(1, len(pollutant) + 1))
    tables["pollutant"] = pollutant[["pollutant_id", "pollutant_code", "pollutant_name", "unit"]]

    # Lookup dictionaries code -> id
    station_ids = dict(zip(station["station_code"], station["station_id"]))
    pollutant_ids = dict(zip(pollutant["pollutant_code"], pollutant["pollutant_id"]))

    # --- Facts: replace the codes by the identifiers
    measures = pd.read_csv(P / "daily_measurements.csv", parse_dates=["date"])
    measures["station_id"] = measures["station_code"].map(station_ids)
    measures["pollutant_id"] = measures["pollutant_code"].map(pollutant_ids)
    assert measures[["station_id", "pollutant_id"]].notna().all().all(), "code without a match"
    tables["daily_measurement"] = measures[["date", "station_id", "pollutant_id",
                                            "mean", "max", "n_hours"]]

    # --- Calendar dimension: every day of the measurement period
    days = pd.date_range(measures["date"].min(), measures["date"].max(), freq="D")
    cal = pd.DataFrame({"date": days})
    cal["year"] = cal["date"].dt.year
    cal["month"] = cal["date"].dt.month
    cal["day_of_week"] = cal["date"].dt.dayofweek + 1          # 1 = Monday ... 7 = Sunday
    cal["is_weekend"] = (cal["day_of_week"] >= 6).astype(int)

    holidays = read_if_exists(config.raw(config.SOURCE_CALENDAR) / "public_holidays.csv",
                              parse_dates=["date"])
    if holidays is not None:
        cal = cal.merge(holidays[["date", "holiday_name"]], on="date", how="left")
        cal["is_public_holiday"] = cal["holiday_name"].notna().astype(int)
    else:  # unknown information -> NULL (and not 0, which would mean "not a holiday")
        cal["is_public_holiday"], cal["holiday_name"] = pd.NA, pd.NA

    school = read_if_exists(P / "school_holidays.csv", parse_dates=["date"])
    if school is not None:
        school = school.rename(columns={"holiday_name": "school_holiday_name"})
        cal = cal.merge(school[["date", "is_school_holiday", "school_holiday_name"]],
                        on="date", how="left")
    else:
        cal["is_school_holiday"], cal["school_holiday_name"] = pd.NA, pd.NA
    tables["calendar"] = cal[["date", "year", "month", "day_of_week", "is_weekend",
                              "is_public_holiday", "holiday_name",
                              "is_school_holiday", "school_holiday_name"]]

    # --- Weather (API): keep the schema columns, within the period
    weather = read_if_exists(config.raw(config.SOURCE_WEATHER) / "weather_daily.csv",
                             parse_dates=["date"])
    if weather is not None:
        weather = weather[weather["date"].isin(days)]
        tables["weather_day"] = weather.reindex(columns=WEATHER_COLUMNS)  # missing column -> NULL
    else:
        tables["weather_day"] = pd.DataFrame(columns=WEATHER_COLUMNS)

    # --- Episodes: web scraping + Open Data file, merged (see episodes.py)
    episodes = combine_episodes()
    if episodes is not None:
        # "PM2.5" in the episodes, "PM25" in the pollutant table
        codes = episodes["pollutant"].str.replace(".", "", regex=False)
        episodes["pollutant_id"] = codes.map(pollutant_ids)
        unknown = episodes["pollutant_id"].isna()
        if unknown.any():
            print(f"   [!] {unknown.sum()} episode(s) of a pollutant missing from the pollutant table: skipped")
        episodes = episodes[~unknown & episodes["date"].isin(days)].copy()
        # The true/false columns of the CSV are read as text "True"/"False" -> 1/0 (or NULL)
        for c in ["forecast", "observed", "criterion_population", "criterion_area"]:
            episodes[c] = episodes[c].map({True: 1, False: 0, "True": 1, "False": 0})
        tables["episode"] = episodes[EPISODE_COLUMNS]
    else:
        tables["episode"] = pd.DataFrame(columns=EPISODE_COLUMNS)

    # MySQL expects dates (DATE), not date + time
    # (.copy() makes each table an independent DataFrame, not a "view" of
    # another one: this avoids pandas' SettingWithCopyWarning)
    for name, t in tables.items():
        t = t.copy()
        if "date" in t and len(t):
            t["date"] = pd.to_datetime(t["date"]).dt.date
        tables[name] = t
    return tables


# -------------------------------------------------------------------------------
# Steps 2 to 5: database, schema, insertion, check
# -------------------------------------------------------------------------------
def connect(with_database: bool = True):
    from sqlalchemy import create_engine
    password = config.secret("MYSQL_PASSWORD")          # read from secrets/keys.env
    database = config.MYSQL_DATABASE if with_database else ""
    url = (f"mysql+pymysql://{config.MYSQL_USER}:{password}"
           f"@{config.MYSQL_HOST}:{config.MYSQL_PORT}/{database}?charset=utf8mb4")
    return create_engine(url)


def run_sql_script(engine, path: Path):
    """Run a .sql file statement by statement (separated by ';')."""
    from sqlalchemy import text
    content = path.read_text(encoding="utf-8")
    # Remove the "-- ..." comment lines, then split on the semicolons
    lines = [line for line in content.splitlines() if not line.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
    print(f"   {len(statements)} statements run from {path.name}")


def main():
    from sqlalchemy import text

    print("1) Preparing the tables in pandas")
    tables = prepare_tables()
    for name, df in tables.items():
        print(f"   {name:<18} {len(df):>8} rows")

    print("2) Creating the database (if needed)")
    server = connect(with_database=False)
    with server.begin() as connection:
        connection.execute(text(f"CREATE DATABASE IF NOT EXISTS {config.MYSQL_DATABASE} "
                                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    engine = connect()

    print("3) Creating the tables (sql/schema.sql)")
    run_sql_script(engine, SCHEMA_FILE)

    print("4) Inserting: dimensions first, facts next")
    for name in TABLE_ORDER:
        df = tables[name]
        if len(df) == 0:
            print(f"   {name:<18} empty, skipped")
            continue
        # if_exists="append": fill the tables created by the schema
        # (with "replace", pandas would recreate them WITHOUT foreign keys).
        df.to_sql(name, engine, if_exists="append", index=False, chunksize=5000, method="multi")
        print(f"   {name:<18} {len(df):>8} rows inserted")

    print("5) Checking the row counts")
    with engine.connect() as connection:
        for name in TABLE_ORDER:
            n = connection.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
            status = "OK" if n == len(tables[name]) else "MISMATCH!"
            print(f"   {name:<18} MySQL = {n:>8}   pandas = {len(tables[name]):>8}   {status}")

    print(f"\nDone. Database '{config.MYSQL_DATABASE}' is ready.")
    print("Diagram: MySQL Workbench > Database > Reverse Engineer...")


if __name__ == "__main__":
    main()
