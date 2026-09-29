"""
FINAL COLLECTION STEP: build a single daily table
=================================================

The sources are joined on a common key: the DATE.
    PM2.5 (target) + other pollutants of the same station + weather
    + public holidays + school holidays + official episodes

Output:
    data/processed/dataset_daily.csv — one row per day, ready for the
    exploratory analysis (EDA). Lagged variables (D-1, D-2...) for the model
    are created at the next step (feature engineering).

Target station: PA01H — PARIS 1er Les Halles (PA04C "Paris Centre" in 2018-2019).
    - URBAN BACKGROUND station: it measures the "ambient" air breathed by
      Parisians, not the air next to a road (traffic stations);
    - the most complete one (99 % of hours measured);
    - it also measures NO2, NOx, O3 and CO, used as explanatory variables.
    Why a single station rather than an average? An average over stations that
    break down at different times changes composition from one day to the
    next, which creates false jumps in the series.
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

# Target station, by order of priority. Until September 2019, central Paris
# was measured by PA04C ("Paris Centre", 4th arr.); since then by PA01H
# (Les Halles, 1st arr.), about 1 km away. Both are urban background stations:
# they are joined into a single "central Paris" series, keeping track of the
# station of origin of each day (column source_station). Assumption to report.
TARGET_STATIONS = ["PA01H", "PA04C"]
WHO_PM25_THRESHOLD = 15    # µg/m³ daily mean (WHO 2021 guidelines)


def read_if_exists(path, **kwargs):
    if path.exists():
        return pd.read_csv(path, **kwargs)
    print(f"   [!] {path.name} missing: corresponding columns skipped")
    return None


def main():
    P = config.DATA_PROCESSED
    daily = pd.read_csv(P / "daily_measurements.csv", parse_dates=["date"])

    print(f"1) Pollutants measured in central Paris ({' then '.join(TARGET_STATIONS)})")
    station = daily[daily["station_code"].isin(TARGET_STATIONS)].copy()
    # If two stations measure the same pollutant on the same day, keep the
    # first one of the priority list.
    station["priority"] = station["station_code"].map({c: i for i, c in enumerate(TARGET_STATIONS)})
    station = (station.sort_values("priority")
                      .drop_duplicates(subset=["date", "pollutant_code"])
                      .drop(columns="priority"))
    pm25_source = station[station["pollutant_code"] == "PM25"].set_index("date")["station_code"]
    print("   PM2.5 days per station:", pm25_source.value_counts().to_dict())
    # Long -> wide format: one column per pollutant
    pollutants = station.pivot_table(index="date", columns="pollutant_code", values="mean")
    pollutants.columns = [c.lower() for c in pollutants.columns]      # PM25 -> pm25
    # For ozone, the afternoon peak matters more than the mean
    if "O3" in station["pollutant_code"].unique():
        pollutants["o3_max"] = station[station["pollutant_code"] == "O3"].set_index("date")["max"]
    print(f"   columns: {list(pollutants.columns)}")

    print("2) Complete calendar (every day, even without measurement)")
    # Starting from a complete calendar makes gaps VISIBLE (NaN)
    # instead of silently removing them.
    start, end = daily["date"].min(), daily["date"].max()
    df = pd.DataFrame({"date": pd.date_range(start, end)})
    df = df.merge(pollutants.reset_index(), on="date", how="left")
    df["source_station"] = df["date"].map(pm25_source)
    # Make years missing from the files visible
    years = df.groupby(df["date"].dt.year)["pm25"].count()
    missing_years = years[years == 0].index.tolist()
    if missing_years:
        print(f"   [!] no PM2.5 measurement for the years: {missing_years}")

    print("3) Weather")
    weather = read_if_exists(config.raw(config.SOURCE_WEATHER) / "weather_daily.csv", parse_dates=["date"])
    if weather is not None:
        df = df.merge(weather, on="date", how="left")

    print("4) Calendar variables")
    df["day_of_week"] = df["date"].dt.dayofweek          # 0 = Monday
    df["month"] = df["date"].dt.month
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    public_holidays = read_if_exists(config.raw(config.SOURCE_CALENDAR) / "public_holidays.csv", parse_dates=["date"])
    if public_holidays is not None:
        df["is_public_holiday"] = df["date"].isin(public_holidays["date"]).astype(int)
    school_holidays = read_if_exists(P / "school_holidays.csv", parse_dates=["date"])
    if school_holidays is not None:
        df = df.merge(school_holidays[["date", "is_school_holiday"]], on="date", how="left")

    print("5) Official Airparif episodes (scraping + Open Data), one flag per pollutant")
    # episode_<pollutant> = 1 if an information or alert procedure was
    # triggered that day OR an exceedance was observed (see episodes.py).
    episodes = combine_episodes()
    if episodes is not None:
        for pollutant in ["PM10", "O3", "NO2"]:
            episode_days = episodes.loc[episodes["pollutant"] == pollutant, "date"]
            df[f"episode_{pollutant.lower()}"] = df["date"].isin(episode_days).astype(int)
        print("   episode days per pollutant:",
              {c: int(df[c].sum()) for c in df.columns if c.startswith("episode_")})

    print("6) Binary target: exceedance of the WHO threshold")
    df["above_who"] = (df["pm25"] > WHO_PM25_THRESHOLD).astype("Int64")
    df.loc[df["pm25"].isna(), "above_who"] = pd.NA

    path = P / "dataset_daily.csv"
    df.to_csv(path, index=False)
    print(f"\n{len(df)} days ({start:%d/%m/%Y} -> {end:%d/%m/%Y}) -> {path}")
    print(f"Missing PM2.5: {df['pm25'].isna().sum()} days ({df['pm25'].isna().mean():.1%})")
    print(f"Days above the WHO threshold: {int(df['above_who'].sum())} "
          f"({df['above_who'].mean():.1%} of measured days)")


if __name__ == "__main__":
    main()
