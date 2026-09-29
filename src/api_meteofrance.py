"""
SOURCE 3 — API: Météo-France climatological data (DPClim) + public holidays
============================================================================

Why this API?
    Weather is the main factor that makes pollution vary from one day to the
    next (wind disperses pollutants, rain washes the air, cold weather
    increases wood heating). Météo-France is the OFFICIAL source: we use the
    daily observations of the Paris reference station, PARIS-MONTSOURIS.

Documentation: https://confluence-meteofrance.atlassian.net/wiki/spaces/OpenDataMeteoFrance/pages/854261785/API+Donn+es+Climatologiques
Portal:        https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesClimatologie
               (create an account, subscribe to "Données Publiques Climatologie").
Authentication, one of the two, written in secrets/keys.env (ignored by git;
NEVER in the code, it would be pushed to GitHub):
    - an API key          -> METEOFRANCE_API_KEY
    - or an Application ID -> METEOFRANCE_APPLICATION_ID; the script then
      exchanges it for a temporary token (valid for 1 hour).
Quota: 100 requests per minute (the script sends about twenty in total).

ASYNCHRONOUS workflow (two steps):
    1. ORDER the data of a station over a period
       -> HTTP 202 answer with an order number.
    2. FETCH the file with this number
       -> HTTP 204 = not ready yet (wait and try again),
          HTTP 201 = CSV file ready.
    A daily order covers at most 1 year: one order per year (2018 ... 2026).

Output (same column names as the rest of the project):
    data/raw/meteofrance/meteofrance_<year>.csv       — raw file of each year
    data/raw/meteofrance/meteofrance_raw_daily.csv    — all years together
    data/raw/meteofrance/weather_daily.csv            — renamed columns, ready to use
    data/raw/calendar/public_holidays.csv             — public holidays (api.gouv.fr)

TO CHECK AT THE FIRST RUN (the real API could not be tested beforehand):
    - the BASE_URL address and the "apikey" header name;
    - the station identifier (75114001 = Paris-Montsouris);
    - the CSV column names (the script prints the columns received).
"""
import os
import time

import pandas as pd
import requests

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

BASE_URL = "https://public-api.meteofrance.fr/public/DPClim/v1"
HOLIDAYS_URL = "https://calendrier.api.gouv.fr/jours-feries/metropole/{year}.json"
TOKEN_URL = "https://portail-api.meteofrance.fr/token"
STATION_ID = "75114001"          # PARIS-MONTSOURIS
WAIT_SECONDS = 10                # pause between two attempts to fetch a file
MAX_ATTEMPTS = 30                # 30 x 10 s = 5 minutes maximum per file
PAUSE_BETWEEN_ORDERS = 3         # politeness towards the server

# Columns of the Météo-France daily file -> names used in the project.
# (Each measurement is followed by a quality-code column, e.g. TM then QTM.)
COLUMN_MAPPING = {
    "TM": "temp_mean_c",         # mean temperature (°C)
    "TN": "temp_min_c",          # minimum temperature (°C)
    "TX": "temp_max_c",          # maximum temperature (°C)
    "UM": "humidity_mean_pct",   # mean relative humidity (%)
    "PMERM": "pressure_mean_hpa",  # mean sea-level pressure (hPa)
    "RR": "rain_mm",             # precipitation (mm)
    "FFM": "wind_mean_ms",       # mean wind speed at 10 m (m/s)
    "FXY": "wind_max_ms",        # maximum 10-minute mean wind speed (m/s)
    "DXY": "wind_dir_deg",       # direction of that maximum wind (°)
}


def authentication_header() -> dict:
    """
    Return the HTTP authentication header.
    Case 1: API key -> "apikey" header.
    Case 2: Application ID -> request an OAuth2 token ("client_credentials"),
            then "Authorization: Bearer <token>" header.
    """
    # Read the secrets file again: a key added after the kernel started is
    # then taken into account without restarting it.
    config.load_secrets()
    key = os.getenv("METEOFRANCE_API_KEY", "").strip()                 # read from secrets/keys.env
    application_id = os.getenv("METEOFRANCE_APPLICATION_ID", "").strip()
    if not key and not application_id:
        if not config.SECRETS_FILE.exists():
            found = [p.name for p in config.SECRETS_FILE.parent.glob("*")] if config.SECRETS_FILE.parent.exists() else []
            raise FileNotFoundError(f"{config.SECRETS_FILE} does not exist. Files found in the "
                                    f"secrets folder: {found} (a name like 'keys.env.txt' must be renamed)")
        raise ValueError("The file exists but METEOFRANCE_API_KEY is empty: fill in the line "
                         f"METEOFRANCE_API_KEY=... in {config.SECRETS_FILE} and save it (Ctrl+S)")
    if key:
        return {"apikey": key}
    response = requests.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                             headers={"Authorization": f"Basic {application_id}"},
                             timeout=config.TIMEOUT_SECONDS)
    response.raise_for_status()
    print("   OAuth2 token obtained (valid for 1 hour)")
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# -------------------------------------------------------------------------------
# Step 1: place an order
# -------------------------------------------------------------------------------
def place_order(session: requests.Session, start: str, end: str) -> str:
    """Order the daily data of the station; return the order number."""
    response = session.get(
        f"{BASE_URL}/commande-station/quotidienne",
        params={
            "id-station": STATION_ID,
            "date-deb-periode": f"{start}T00:00:00Z",   # ISO 8601 format, UTC
            "date-fin-periode": f"{end}T00:00:00Z",
        },
        timeout=config.TIMEOUT_SECONDS,
    )
    if response.status_code in (401, 403):
        raise PermissionError(f"Access denied (HTTP {response.status_code}): check the key or the "
                              "Application ID, and the subscription to the API")
    if response.status_code != 202:
        raise RuntimeError(f"Order refused (HTTP {response.status_code}): {response.text[:300]}")
    # Expected answer: {"elaboreProduitAvecDemandeResponse": {"return": "<number>"}}
    return str(response.json()["elaboreProduitAvecDemandeResponse"]["return"])


# -------------------------------------------------------------------------------
# Step 2: fetch the file (when it is ready)
# -------------------------------------------------------------------------------
def fetch_file(session: requests.Session, order_id: str) -> str:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = session.get(f"{BASE_URL}/commande/fichier",
                               params={"id-cmde": order_id},
                               timeout=config.TIMEOUT_SECONDS)
        if response.status_code == 201:          # file ready
            return response.content.decode("utf-8", errors="replace")
        if response.status_code == 204:          # still being prepared
            print(f"      file not ready yet (attempt {attempt}), waiting {WAIT_SECONDS} s")
            time.sleep(WAIT_SECONDS)
            continue
        raise RuntimeError(f"Cannot fetch the file (HTTP {response.status_code}): {response.text[:300]}")
    raise TimeoutError(f"Order {order_id} still not ready after {MAX_ATTEMPTS} attempts")


# -------------------------------------------------------------------------------
# Step 3: read and harmonise the CSV
# -------------------------------------------------------------------------------
def read_meteofrance_csv(path) -> pd.DataFrame:
    """Météo-France CSV: ';' separator, decimal comma, DATE as YYYYMMDD."""
    df = pd.read_csv(path, sep=";", decimal=",", dtype={"POSTE": str})
    df.columns = [c.strip() for c in df.columns]
    return df


def harmonise(source: pd.DataFrame) -> pd.DataFrame:
    df = source.copy()
    df["date"] = pd.to_datetime(df["DATE"].astype(str), format="%Y%m%d")
    missing = [c for c in COLUMN_MAPPING if c not in df.columns]
    if missing:
        print(f"   [!] columns missing from the file (left empty): {missing}")
    output = pd.DataFrame({"date": df["date"]})
    for code, name in COLUMN_MAPPING.items():
        output[name] = pd.to_numeric(df[code], errors="coerce") if code in df.columns else pd.NA
    output = output.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    # Check: no gap between the first and the last date
    expected = pd.date_range(output["date"].min(), output["date"].max())
    missing_days = len(expected) - len(output)
    print(f"   {len(output)} days, {missing_days} missing day(s) in the period")
    missing_values = output.drop(columns="date").isna().sum()
    print("   missing values per variable:", missing_values[missing_values > 0].to_dict() or "none")
    return output


# -------------------------------------------------------------------------------
# Step 4: public holidays (official API calendrier.api.gouv.fr, no key)
# -------------------------------------------------------------------------------
def download_public_holidays() -> pd.DataFrame:
    """The API returns {"2024-01-01": "1er janvier", ...} for a given year."""
    rows = []
    for year in range(int(config.START_DATE[:4]), int(config.END_DATE[:4]) + 1):
        response = requests.get(HOLIDAYS_URL.format(year=year), timeout=config.TIMEOUT_SECONDS)
        response.raise_for_status()
        for date, name in response.json().items():
            rows.append({"date": date, "holiday_name": name})
        time.sleep(0.5)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def main():
    session = requests.Session()
    session.headers.update({**authentication_header(), "User-Agent": config.USER_AGENT})

    print(f"1) Yearly orders for station {STATION_ID} (Paris-Montsouris)")
    pieces = []
    for year in range(int(config.START_DATE[:4]), int(config.END_DATE[:4]) + 1):
        start = max(f"{year}-01-01", config.START_DATE)
        end = min(f"{year}-12-31", config.END_DATE)
        order_id = place_order(session, start, end)
        print(f"   {year}: order no. {order_id}")
        text = fetch_file(session, order_id)
        # Keep the raw file of each year (trace), then read it with pandas
        year_path = config.raw(config.SOURCE_WEATHER) / f"meteofrance_{year}.csv"
        year_path.write_text(text, encoding="utf-8")
        df = read_meteofrance_csv(year_path)
        print(f"      {len(df)} rows received")
        pieces.append(df)
        time.sleep(PAUSE_BETWEEN_ORDERS)

    source = pd.concat(pieces, ignore_index=True)
    source.to_csv(config.raw(config.SOURCE_WEATHER) / "meteofrance_raw_daily.csv", index=False)
    print(f"   columns received: {list(source.columns)}")

    print("2) Harmonising the columns")
    weather = harmonise(source)
    weather.to_csv(config.raw(config.SOURCE_WEATHER) / "weather_daily.csv", index=False)

    print("3) Public holidays (api.gouv.fr)")
    holidays = download_public_holidays()
    holidays.to_csv(config.raw(config.SOURCE_CALENDAR) / "public_holidays.csv", index=False)
    print(f"   {len(holidays)} public holidays")


if __name__ == "__main__":
    main()
