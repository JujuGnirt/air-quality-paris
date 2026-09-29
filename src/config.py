"""
Shared settings for every script in the project.

Everything that may change (folders, dates, MySQL connection) lives here:
we edit one line instead of searching through every script.

Works in both cases:
    - run or imported from a .py script   (python src/...)
    - run or imported from a Jupyter notebook
"""
import os
from pathlib import Path


# --- Project root folder ------------------------------------------------------
def find_root() -> Path:
    """
    Return the project root folder (the one that contains "data").

    - In a .py file, Python creates the variable __file__ (path of the file):
      config.py is in src/, so the root is two levels up.
    - In a notebook, __file__ does not exist (NameError): we start from the
      folder where the notebook is open (Path.cwd()) and walk up the parent
      folders until we find the one that contains "data".
    """
    try:
        return Path(__file__).resolve().parent.parent
    except NameError:
        folder = Path.cwd()
        while not (folder / "data").exists() and folder != folder.parent:
            folder = folder.parent
        return folder


ROOT = find_root()
DATA_RAW = ROOT / "data" / "raw"                   # raw data, never modified
DATA_PROCESSED = ROOT / "data" / "processed"       # cleaned data
DATA_RAW.mkdir(parents=True, exist_ok=True)
DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

# data/raw is organised as ONE SUB-FOLDER PER SOURCE (not per year: the
# scripts process all years together and the year is already in each file
# name). Adding a year = dropping the file into the right folder.
SOURCE_MEASUREMENTS = "airparif_measurements"   # flat file: 2018_PM25.csv, ..., ring road
SOURCE_INDICES = "airparif_indices"             # flat file: indices_QA_commune_IDF_2014.csv ...
SOURCE_ALERTS = "airparif_alerts"               # flat file: alrt_idf.csv (Open Data)
SOURCE_SCRAPING = "airparif_scraping"           # web scraping: PDF reports + episodes_airparif.csv
SOURCE_WEATHER = "meteofrance"                  # Météo-France API: weather_daily.csv ...
SOURCE_CALENDAR = "calendar"                    # school_calendar.csv, public_holidays.csv

# Former (French) folder names: if one of them exists and the English folder
# does not, it is renamed automatically, so nothing has to be moved by hand.
FORMER_FOLDER_NAMES = {
    SOURCE_MEASUREMENTS: ["airparif_mesures"],
    SOURCE_ALERTS: ["airparif_alertes"],
    SOURCE_CALENDAR: ["calendrier", "date"],
    SOURCE_WEATHER: ["meteo_france"],
}


def raw(source: str) -> Path:
    """Path of the data/raw sub-folder of a source (created if it does not exist)."""
    folder = DATA_RAW / source
    if not folder.exists():
        for former_name in FORMER_FOLDER_NAMES.get(source, []):
            former = DATA_RAW / former_name
            if former.exists():
                former.rename(folder)
                print(f"   folder data/raw/{former_name} renamed to data/raw/{source}")
                break
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --- Study period ---------------------------------------------------------------
# Period of the Airparif MEASUREMENTS (2018 -> September 2026). Weather, public
# holidays and episodes are collected over the same period.
# (The 2014-2017 indices are a separate context table: no weather needed.)
START_DATE = "2018-01-01"
END_DATE = "2026-09-21"

TIMEZONE = "Europe/Paris"          # time zone used to split days


# --- Polite scraping ------------------------------------------------------------
# An explicit User-Agent tells the website who is sending the requests and why.
# This line is published on GitHub: put a public contact (e.g. the repo URL).
USER_AGENT = "StudentProject-AirQualityParis/1.0 (Data Analytics bootcamp; contact: GitHub repository)"
PAUSE_SECONDS = 2                  # wait between two requests to the same website
TIMEOUT_SECONDS = 30               # maximum time to wait for an answer


# --- Secrets (API keys, MySQL password) -----------------------------------------
# Secrets are NEVER written in the code. They are stored in a separate file,
# secrets/keys.env, which git ignores (see .gitignore): it cannot be pushed
# to GitHub by mistake. Template to copy: keys.env.example (project root).
SECRETS_FILE = ROOT / "secrets" / "keys.env"
if not SECRETS_FILE.exists() and (ROOT / "secrets" / "cles.env").exists():
    SECRETS_FILE = ROOT / "secrets" / "cles.env"   # former French file name


def load_secrets(path: Path = SECRETS_FILE) -> None:
    """
    Read the NAME=value lines of the secrets file and store them in the
    environment variables (os.environ), where the scripts look for them.
    Empty lines and comments (#) are ignored. Values are never printed.
    """
    if not path.exists():
        return  # no file: secret() will print a clear message if a key is missing
    # "utf-8-sig": also reads files saved by Windows Notepad, which adds an
    # invisible character (BOM) at the start of the file.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value != "":
            os.environ[name.strip()] = value


def secret(name: str) -> str:
    """
    Return the value of a secret (e.g. "METEOFRANCE_API_KEY") read from
    secrets/keys.env. If it is missing, stop with a clear message.
    """
    value = os.getenv(name, "").strip()
    if value == "":
        raise ValueError(f"{name} is missing: fill in the line {name}= in {SECRETS_FILE}")
    return value


load_secrets()


# --- MySQL connection --------------------------------------------------------------
# Values read from secrets/keys.env; otherwise the defaults below.
# The password is read when connecting: secret("MYSQL_PASSWORD").
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "air_quality_paris")
