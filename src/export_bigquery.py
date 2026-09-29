"""
SOURCE 5 — BIG DATA SYSTEM: a denormalised table for Google BigQuery
=====================================================================

Why BigQuery?
    BigQuery is Google's cloud data warehouse, built for very large volumes
    and analytical queries. The course asks to load a DENORMALISED version of
    the relational database into it, with partitioning and clustering.

Denormalised = one single wide table: each row already contains the station
name and type, the pollutant name and unit, the calendar and the weather of
the day. No JOIN is needed any more. This is the opposite of MySQL, and on
purpose: in a data warehouse, storage is cheap but joins on large tables
are expensive, so the data are stored "ready to query".

What this script does:
    1. Rebuild the MySQL tables in pandas (same function as load_mysql.py,
       so both databases contain exactly the same data).
    2. Join them into one table (one row = one station x pollutant x day).
    3. Save it as CSV + the table schema (JSON) to paste into the console.

Output (data/processed/bigquery/):
    daily_measurements_denorm.csv   — to upload in the BigQuery console
    bigquery_schema.json            — column names and types ("Edit as text")

Partitioning and clustering (chosen in the console, see README):
    - PARTITION by the integer column `year` (range 2018 -> 2027, step 1).
      Why not by `date`, the usual choice? In the free BigQuery sandbox,
      date partitions older than 60 days are deleted automatically: all our
      data (2018-2026) would disappear. An integer-range partition on the
      year avoids this, and our queries filter by year anyway.
    - CLUSTER by pollutant_code, station_code: inside each partition, rows
      are sorted by these columns, so a filter on PM25 / PA01H reads less data.

Run:  python src/export_bigquery.py   (or in Jupyter)
"""
import json

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
from load_mysql import prepare_tables

WHO_PM25_THRESHOLD = 15
MAX_UPLOAD_MB = 100          # size limit of a file uploaded from the browser

# Column name -> BigQuery type, in the order of the final table
SCHEMA = {
    "date": "DATE", "year": "INTEGER", "month": "INTEGER", "day_of_week": "INTEGER",
    "is_weekend": "INTEGER", "is_public_holiday": "INTEGER", "is_school_holiday": "INTEGER",
    "station_code": "STRING", "station_name": "STRING", "station_type": "STRING",
    "pollutant_code": "STRING", "pollutant_name": "STRING", "unit": "STRING",
    "daily_mean": "FLOAT", "hourly_max": "FLOAT", "n_hours": "INTEGER", "above_who_pm25": "INTEGER",
    "temp_mean_c": "FLOAT", "temp_min_c": "FLOAT", "temp_max_c": "FLOAT",
    "humidity_mean_pct": "FLOAT", "pressure_mean_hpa": "FLOAT", "rain_mm": "FLOAT",
    "wind_mean_ms": "FLOAT", "wind_max_ms": "FLOAT", "wind_dir_deg": "FLOAT",
    "episode_level": "STRING",
}


def build_denormalised_table() -> pd.DataFrame:
    tables = prepare_tables()

    print("2) Joining all the tables into one")
    df = (tables["daily_measurement"]
          .merge(tables["station"], on="station_id", how="left")
          .merge(tables["pollutant"], on="pollutant_id", how="left")
          .merge(tables["calendar"], on="date", how="left")
          .merge(tables["weather_day"], on="date", how="left"))

    # Official episode of the SAME pollutant on the same day (information / alert), if any
    episodes = tables["episode"][["date", "pollutant_id", "level"]].rename(columns={"level": "episode_level"})
    df = df.merge(episodes, on=["date", "pollutant_id"], how="left")

    df = df.rename(columns={"mean": "daily_mean", "max": "hourly_max"})
    # WHO exceedance: only meaningful for PM2.5, left empty for the other pollutants
    df["above_who_pm25"] = pd.NA
    is_pm25 = df["pollutant_code"] == "PM25"
    df.loc[is_pm25, "above_who_pm25"] = (df.loc[is_pm25, "daily_mean"] > WHO_PM25_THRESHOLD).astype(int)

    # Keep the schema columns, in order (a column missing from the sources stays empty)
    df = df.reindex(columns=list(SCHEMA))
    # Integer columns with empty values: "Int64" writes 1 instead of 1.0 in the CSV
    for column, bq_type in SCHEMA.items():
        if bq_type == "INTEGER":
            df[column] = df[column].astype("Int64")
    return df.sort_values(["date", "station_code", "pollutant_code"]).reset_index(drop=True)


def main():
    print("1) Rebuilding the MySQL tables in pandas")
    df = build_denormalised_table()

    output = config.DATA_PROCESSED / "bigquery"
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "daily_measurements_denorm.csv"
    df.to_csv(csv_path, index=False)

    schema = [{"name": name, "type": bq_type, "mode": "NULLABLE"} for name, bq_type in SCHEMA.items()]
    (output / "bigquery_schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")

    size_mb = csv_path.stat().st_size / 1_000_000
    print(f"\n3) {len(df)} rows x {df.shape[1]} columns -> {csv_path} ({size_mb:.0f} MB)")
    print(f"   period: {df['date'].min()} -> {df['date'].max()}")
    print("   schema to paste in the console (Edit as text):", output / "bigquery_schema.json")
    if size_mb > MAX_UPLOAD_MB:
        print(f"   [!] file larger than {MAX_UPLOAD_MB} MB: too big for a browser upload.")
    else:
        print(f"   OK: under the {MAX_UPLOAD_MB} MB limit of a browser upload.")


if __name__ == "__main__":
    main()
