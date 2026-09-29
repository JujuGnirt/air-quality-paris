"""
SOURCE 1 — FLAT FILES (CSV): Airparif measurements, indices, alerts, school calendar
=====================================================================================

Why these files?
    - Airparif is the official air-quality monitoring body for the Paris region.
      Its CSV exports give the HOURLY measurements of every station: they
      provide our TARGET VARIABLE (PM2.5) and other pollutants useful as
      explanatory variables (NO2, O3, NOx, CO).
    - The French Ministry of Education publishes the school-holiday calendar
      as a CSV. Holidays change traffic, hence emissions.

Expected files (data/raw/<source>/):
    airparif_measurements/  <year>_<POLLUTANT>.csv, e.g. 2026_PM25.csv, 2026_NO2.csv
                            <year>_<POLLUTANT>_boulevard_p_riph_rique.csv (ring road)
    airparif_indices/       indices_QA_commune_IDF_<year>.csv (2014-2017)
    airparif_alerts/        alrt_idf.csv (Airparif Open Data)
    calendar/               school_calendar.csv (export from data.education.gouv.fr)
    -> To add years, just drop 2025_PM25.csv, etc. into the folder.

FORMAT OF THE STATION FILES (observed in the Airparif exports):
    6 header lines, then one line per hour and one column per station.
        line 1: PA01H:PM25             (station code : pollutant code)
        line 2: PARIS 1er Les Halles   (station name)
        line 3: PA01H                  (station code)
        line 4: PM 2,5 particules      (long pollutant name)
        line 5: PM25                   (pollutant code)
        line 6: microg/m3              (unit)
        then:   2026-01-01 01:00:00Z,60.3,94.3,...
    This is a "WIDE" format. For a database and for pandas, we turn it into a
    "LONG" format: one row = one measurement (timestamp, station, pollutant, value).

Output (data/processed/):
    stations.csv              — station dimension (code, name, type)
    pollutants.csv            — pollutant dimension (code, name, unit)
    hourly_measurements.csv   — one row per hour x station x pollutant
    daily_measurements.csv    — daily means (18-hours-out-of-24 rule)
    ring_road_daily.csv       — ring-road segments, daily means
    aqi_indices_daily.csv     — 2014-2017 air-quality sub-indices per municipality
    alerts_opendata.csv       — official episodes 2019-2025 (Open Data)
    school_holidays.csv       — date, is_school_holiday (Paris, zone C)
"""
import re

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

N_HEADER_LINES = 6
MIN_HOURS_PER_DAY = 18     # a daily mean requires >= 75 % of the 24 hours

# Accepted file names: "2018_PM25.csv", "2018 PM25.csv" or "2018-PM25.csv"
# ([ _-] = a space, an underscore or a hyphen: Windows sometimes replaces the
# underscore with a space when downloading).
STATION_FILE_REGEX = re.compile(r"(?P<year>\d{4})[ _-](?P<pollutant>[A-Z0-9]+)\.csv$", re.IGNORECASE)
RING_ROAD_FILE_REGEX = re.compile(r"(?P<year>\d{4})[ _-](?P<pollutant>[A-Z0-9]+)[ _-]boulevard.*\.csv$", re.IGNORECASE)


# -------------------------------------------------------------------------------
# 1. Reading one station file (wide format -> long format)
# -------------------------------------------------------------------------------
def read_station_file(path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (measurements in long format, column metadata)."""
    # Safeguard: exports from other years could have a different format.
    # We check that line 7 starts with a date; otherwise we stop with a clear
    # message rather than producing wrong data.
    with open(path, encoding="utf-8-sig") as f:
        lines = [f.readline() for _ in range(N_HEADER_LINES + 1)]
    if not re.match(r"\d{4}-\d{2}-\d{2}", lines[-1]):
        raise ValueError(
            f"{path.name}: unexpected format (line {N_HEADER_LINES + 1} is not a date).\n"
            f"Start of the file:\n{''.join(lines)}"
        )
    # a) The 6 header lines -> a metadata table (one row per column)
    header = pd.read_csv(path, header=None, nrows=N_HEADER_LINES, dtype=str)
    meta = pd.DataFrame({
        "station_code": header.iloc[2, 1:].str.strip().values,
        "station_name": header.iloc[1, 1:].str.strip().values,
        "pollutant_name": header.iloc[3, 1:].str.strip().values,
        "pollutant_code": header.iloc[4, 1:].str.strip().values,
        "unit": header.iloc[5, 1:].str.strip().values,
    })

    # b) The data, without header. Columns are named by their POSITION
    #    because the same station code can appear twice (e.g. RD934 in 2026).
    data = pd.read_csv(path, header=None, skiprows=N_HEADER_LINES)
    data.columns = ["timestamp"] + list(range(len(meta)))

    # c) Long format: one row per (hour, column)
    long = data.melt(id_vars="timestamp", var_name="column_number", value_name="value")
    long = long.dropna(subset=["value"])                  # empty cell = no measurement
    long = long.merge(meta, left_on="column_number", right_index=True).drop(columns="column_number")
    return long, meta


def convert_timestamp(series: pd.Series) -> pd.Series:
    """
    Timestamps are in UTC (suffix Z) and the first line is 01:00Z: they mark
    the END of the measured hour (01:00Z = measurement from 00:00 to 01:00).
    We subtract 1 hour to get the START of the hour, then convert to Paris
    time so that "a day" matches the day as lived in Paris.
    (Reasonable assumption, to be confirmed in the Airparif documentation.)
    """
    utc = pd.to_datetime(series, utc=True) - pd.Timedelta(hours=1)
    return utc.dt.tz_convert(config.TIMEZONE).dt.tz_localize(None)


def station_type(name: str) -> str:
    """
    The file does not give the station type: we infer it from the name.
    "Traffic" stations are named after a road (boulevard, avenue, motorway...).
    Heuristic to check against the official list of Airparif stations.
    """
    n = name.lower()
    if "rural" in n or "rambouillet" in n:
        return "rural"
    if "tour eiffel" in n:
        return "observation"
    # \b = start of a word: "RN20" is a road, but "Champigny-sur-Ma-rn-e" is not
    if re.search(r"\b(boulevard|avenue|place|rue|quai|autoroute|route|rn\s?\d|rd\s?\d)", n):
        return "traffic"
    return "urban_background"


def load_station_measurements() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    pieces, metas = [], []
    # rglob: also looks into sub-folders (e.g. one folder per year)
    for path in sorted(config.raw(config.SOURCE_MEASUREMENTS).rglob("*.csv")):
        m = STATION_FILE_REGEX.search(path.name)
        if not m:
            continue
        long, meta = read_station_file(path)
        # Fully empty columns = stations without any measurement over the year
        empty = sorted(set(meta["station_code"]) - set(long["station_code"]))
        print(f"   {path.name:<22} {len(meta):>3} columns, {len(long):>8} measurements"
              + (f", no data at all: {empty}" if empty else ""))
        pieces.append(long)
        metas.append(meta)
    if not pieces:
        raise FileNotFoundError(f"No <year>_<POLLUTANT>.csv file in data/raw/{config.SOURCE_MEASUREMENTS}/")
    measurements = pd.concat(pieces, ignore_index=True)
    meta = pd.concat(metas, ignore_index=True)

    # --- Cleaning -------------------------------------------------------------
    measurements["timestamp"] = convert_timestamp(measurements["timestamp"])
    measurements["value"] = pd.to_numeric(measurements["value"], errors="coerce")
    measurements = measurements.dropna(subset=["value"])

    # Slightly negative values: instrument noise when the concentration is
    # close to zero (down to about -3 µg/m³). A concentration cannot be
    # negative: we set them to 0 and count how many were fixed (for the report).
    negative = measurements["value"] < 0
    print(f"   negative values set to 0: {negative.sum()} "
          f"({negative.mean():.2%}), minimum observed {measurements['value'].min()}")
    measurements.loc[negative, "value"] = 0.0

    # Exact duplicates (same hour, station, pollutant): keep only one
    before = len(measurements)
    measurements = measurements.drop_duplicates(subset=["timestamp", "station_code", "pollutant_code"])
    print(f"   duplicates removed: {before - len(measurements)}")

    # --- Dimension tables ---------------------------------------------------------
    stations = (meta[["station_code", "station_name"]].drop_duplicates("station_code")
                .sort_values("station_code").reset_index(drop=True))
    stations["station_type"] = stations["station_name"].map(station_type)
    # Keep only the stations with at least one measurement
    stations = stations[stations["station_code"].isin(measurements["station_code"])]

    pollutants = (meta[["pollutant_code", "pollutant_name", "unit"]]
                  .drop_duplicates("pollutant_code").reset_index(drop=True))

    measurements = measurements[["timestamp", "station_code", "pollutant_code", "value"]]
    return stations, pollutants, measurements.sort_values(["pollutant_code", "station_code", "timestamp"])


# -------------------------------------------------------------------------------
# 2. Daily aggregation
# -------------------------------------------------------------------------------
def aggregate_daily(measurements: pd.DataFrame) -> pd.DataFrame:
    """
    Daily mean per station and pollutant, only if at least 18 hours out of 24
    were measured (usual 75 % completeness rule). Without this rule, a "mean"
    computed on 3 hours would count as much as a real day. We also keep the
    hourly maximum (useful for ozone, whose afternoon peaks matter more than
    the mean).
    """
    m = measurements.copy()
    m["date"] = m["timestamp"].dt.normalize()
    daily = (m.groupby(["date", "station_code", "pollutant_code"])["value"]
               .agg(mean="mean", max="max", n_hours="count").reset_index())
    rejected = daily["n_hours"] < MIN_HOURS_PER_DAY
    print(f"   days rejected (fewer than {MIN_HOURS_PER_DAY} hours measured): {rejected.sum()} out of {len(daily)}")
    daily = daily[~rejected]
    daily[["mean", "max"]] = daily[["mean", "max"]].round(2)
    return daily.reset_index(drop=True)


# -------------------------------------------------------------------------------
# 3. Ring-road (boulevard périphérique) files: a different format
# -------------------------------------------------------------------------------
def load_ring_road() -> pd.DataFrame:
    """
    Different format: simple header "time,Chap-Bagn,Bagn-Berc,..." with one
    column per SEGMENT of the ring road (Porte de la Chapelle -> Bagnolet, ...).
    Hours without "Z" (time zone not given), integer values, ends on 13/09.
    These features suggest MODELLED values rather than measurements: to be
    checked. Kept apart, as context, never as the target.
    """
    pieces = []
    for path in sorted(config.raw(config.SOURCE_MEASUREMENTS).rglob("*boulevard*.csv")):
        m = RING_ROAD_FILE_REGEX.search(path.name)
        if not m:
            continue
        df = pd.read_csv(path, parse_dates=["time"])
        long = df.melt(id_vars="time", var_name="segment", value_name="value").dropna()
        long["pollutant_code"] = m["pollutant"].upper()
        pieces.append(long)
    if not pieces:
        return pd.DataFrame()
    r = pd.concat(pieces, ignore_index=True)
    r["date"] = r["time"].dt.normalize()
    daily = (r.groupby(["date", "segment", "pollutant_code"])["value"]
               .agg(mean="mean", n_hours="count").reset_index())
    daily["mean"] = daily["mean"].round(2)
    return daily[daily["n_hours"] >= MIN_HOURS_PER_DAY].reset_index(drop=True)


# -------------------------------------------------------------------------------
# 4. Air-quality indices per municipality (2014-2017)
# -------------------------------------------------------------------------------
def load_indices() -> pd.DataFrame:
    """
    Files indices_QA_commune_IDF_<year>.csv: date, ninsee, no2, o3, pm10.
    WARNING: these are NOT concentrations in µg/m³ but SUB-INDICES (public
    information scale), modelled for each municipality. Airparif states that
    these indices are meant to inform the public and must not be used for
    statistical analyses of pollution trends.
    -> Kept in a separate table (geographic context, descriptive EDA), never
       mixed with the station measurements.

    The "ninsee" code mixes three geographic levels:
        0 = whole Paris region; 75, 77... = department; 75101... = municipality
    """
    # Any CSV whose name starts with "indices" (underscore or space, no matter)
    files = sorted(f for f in config.raw(config.SOURCE_INDICES).rglob("*.csv")
                   if f.name.lower().startswith("indices"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], format="%d/%m/%Y")
    df = df.rename(columns={"ninsee": "insee_code", "no2": "index_no2",
                            "o3": "index_o3", "pm10": "index_pm10"})
    length = df["insee_code"].astype(str).str.len()
    df["geo_level"] = "municipality"
    df.loc[length <= 2, "geo_level"] = "department"
    df.loc[df["insee_code"] == 0, "geo_level"] = "region"
    before = len(df)
    df = df.drop_duplicates(subset=["date", "insee_code"])
    print(f"   {len(files)} file(s), {len(df)} rows ({before - len(df)} duplicates), "
          f"{df['insee_code'].nunique()} areas, {df['date'].min():%Y} -> {df['date'].max():%Y}")
    return df


# -------------------------------------------------------------------------------
# 5. History of alerts (Airparif Open Data, 2019 -> March 2025)
# -------------------------------------------------------------------------------
def load_opendata_alerts() -> pd.DataFrame:
    """
    File alrt_idf.csv (Airparif Open Data portal): one row per day with an
    information or alert procedure in the Paris region.

    What this file contains (checked against the 2024-2025 PDF reports): the
    procedures TRIGGERED ON FORECAST. All "forecast" episodes of the reports
    are in it, false alarms included; episodes "observed but not forecast"
    are not. So it is recorded as forecast = 1 and observed = unknown.

    Cleaning:
      - date_ech is in UTC ("2019/01/20 23:00:00+00"): converted to Paris time
        it gives midnight on 21/01/2019, the actual date of the episode;
      - lib_pol has 4 spellings for 2 pollutants ("PM10", "Particules PM10",
        "O3", "Ozone"): harmonised to PM10 and O3;
      - etat -> level "information" or "alert", as for the scraping.
    """
    # The file is named alrt_idf.csv (or "alrt idf.csv"): take the first CSV of the folder
    candidates = sorted(config.raw(config.SOURCE_ALERTS).glob("*.csv"))
    if not candidates:
        return pd.DataFrame()
    source = pd.read_csv(candidates[0])
    df = pd.DataFrame()
    utc = pd.to_datetime(source["date_ech"], format="%Y/%m/%d %H:%M:%S+00", utc=True)
    df["date"] = utc.dt.tz_convert(config.TIMEZONE).dt.normalize().dt.tz_localize(None)
    df["pollutant"] = (source["lib_pol"].str.replace("Particules ", "", regex=False)
                                        .replace({"Ozone": "O3"}))
    df["level"] = source["etat"].str.upper().map(
        {"INFORMATION ET RECOMMANDATION": "information", "ALERTE": "alert"})
    df["source"] = "opendata_csv"
    df["forecast"] = True
    df["observed"] = pd.NA
    known = df["pollutant"].isin(["PM10", "O3", "NO2"]) & df["level"].notna()
    if (~known).any():
        print(f"   [!] {(~known).sum()} row(s) with an unknown pollutant or level: dropped")
    df = df[known].drop_duplicates(["date", "pollutant"]).sort_values("date").reset_index(drop=True)
    print(f"   {len(df)} episodes, {df['date'].min():%d/%m/%Y} -> {df['date'].max():%d/%m/%Y}")
    return df


# -------------------------------------------------------------------------------
# 6. School holidays
# -------------------------------------------------------------------------------
def normalise(name: str) -> str:
    """'Date de début' -> 'date_de_debut' (lower case, no accents, no spaces)."""
    accents = {"é": "e", "è": "e", "ê": "e", "à": "a", "â": "a", "ô": "o", "û": "u", "î": "i", "ç": "c"}
    name = str(name).lower()
    for letter, replacement in accents.items():
        name = name.replace(letter, replacement)
    return name.strip().replace("'", "_").replace(" ", "_")


def find_column(df: pd.DataFrame, candidates: list[str], required=True):
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise KeyError(f"None of the columns {candidates}; columns present: {list(df.columns)}")
    return None


def prepare_school_holidays(path, start_date, end_date) -> pd.DataFrame:
    """
    The file has one row per holiday period and per académie (school district).
    We want one row per DAY with a 0/1 flag for Paris (zone C).
    """
    source = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    source.columns = [normalise(c) for c in source.columns]
    c_place = find_column(source, ["location", "academies", "academie"])
    c_start = find_column(source, ["start_date", "date_de_debut"])
    c_end = find_column(source, ["end_date", "date_de_fin"])
    c_pop = find_column(source, ["population"], required=False)
    c_desc = find_column(source, ["description"], required=False)

    df = source[source[c_place].astype(str).str.strip() == "Paris"].copy()
    if c_pop:  # "Enseignants" (teachers) rows duplicate the pupils' rows
        df = df[df[c_pop].astype(str) != "Enseignants"]
    # Dates stored with a time zone (e.g. 2023-10-20T22:00:00+00:00) -> Paris time
    for c in (c_start, c_end):
        df[c] = pd.to_datetime(df[c], utc=True).dt.tz_convert(config.TIMEZONE).dt.tz_localize(None).dt.normalize()

    days = []
    for _, period in df.iterrows():
        # end excluded: it is the day school starts again
        for d in pd.date_range(period[c_start], period[c_end] - pd.Timedelta(days=1)):
            days.append({"date": d, "holiday_name": period[c_desc] if c_desc else None})
    holidays = pd.DataFrame(days, columns=["date", "holiday_name"]).drop_duplicates("date")

    calendar = pd.DataFrame({"date": pd.date_range(start_date, end_date)})
    calendar = calendar.merge(holidays, on="date", how="left")
    calendar["is_school_holiday"] = calendar["holiday_name"].notna().astype(int)
    return calendar


def find_school_calendar_file():
    """school_calendar.csv (or its former French name calendrier_scolaire.csv)."""
    folder = config.raw(config.SOURCE_CALENDAR)
    for name in ["school_calendar.csv", "calendrier_scolaire.csv"]:
        if (folder / name).exists():
            return folder / name
    return None


# -------------------------------------------------------------------------------
def main():
    P = config.DATA_PROCESSED
    print("1) Airparif station files")
    stations, pollutants, measurements = load_station_measurements()
    stations.to_csv(P / "stations.csv", index=False)
    pollutants.to_csv(P / "pollutants.csv", index=False)
    measurements.to_csv(P / "hourly_measurements.csv", index=False)
    print(f"   {len(stations)} stations, {len(pollutants)} pollutants, {len(measurements)} hourly measurements")
    print(f"   period: {measurements['timestamp'].min()} -> {measurements['timestamp'].max()}")

    print("2) Daily means")
    daily = aggregate_daily(measurements)
    daily.to_csv(P / "daily_measurements.csv", index=False)

    print("3) Ring road (boulevard périphérique)")
    ring_road = load_ring_road()
    if len(ring_road):
        ring_road.to_csv(P / "ring_road_daily.csv", index=False)
        print(f"   {ring_road['segment'].nunique()} segments, {len(ring_road)} rows")

    print("4) Air-quality indices per municipality")
    indices = load_indices()
    if len(indices):
        indices.to_csv(P / "aqi_indices_daily.csv", index=False)

    print("5) History of alerts (Airparif Open Data)")
    alerts = load_opendata_alerts()
    if len(alerts):
        alerts.to_csv(P / "alerts_opendata.csv", index=False)
    else:
        print(f"   [!] data/raw/{config.SOURCE_ALERTS}/alrt_idf.csv missing")

    print("6) School holidays")
    path = find_school_calendar_file()
    if path is not None:
        holidays = prepare_school_holidays(path, daily["date"].min(), daily["date"].max())
        holidays.to_csv(P / "school_holidays.csv", index=False)
        print(f"   {holidays['is_school_holiday'].sum()} holiday days out of {len(holidays)}")
    else:
        print(f"   [!] data/raw/{config.SOURCE_CALENDAR}/school_calendar.csv missing — to download (see README)")


if __name__ == "__main__":
    main()
