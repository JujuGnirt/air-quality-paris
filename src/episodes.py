"""
Merging the two sources of official pollution episodes
======================================================

    1. Web scraping of Airparif (scrape_airparif.py)
       -> data/raw/airparif_scraping/episodes_airparif.csv
       HTML page (current year, OBSERVED exceedances) and PDF reports
       (2024, 2025: forecast AND observed, quality of the forecast).
    2. Airparif Open Data flat file (alrt_idf.csv)
       -> data/processed/alerts_opendata.csv
       Procedures triggered on FORECAST, 2019 -> March 2025.

The two complement each other: the Open Data file covers 2019-2023, where the
scraping finds nothing; the scraping adds 2025-2026 and, thanks to the PDFs,
the episodes observed but not forecast.

When the same day and pollutant appear in several sources, we keep the most
complete one: PDF report > HTML page > Open Data file.
If the Open Data file and a PDF report cover the same day, the PDF confirms
"forecast = yes" and adds "observed".

Used by build_dataset.py and load_mysql.py.
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

SOURCE_PRIORITY = {"pdf_report": 0, "html_page": 1, "opendata_csv": 2}
COLUMNS = ["date", "pollutant", "level", "source", "forecast", "observed",
           "forecast_result", "criterion_population", "criterion_area"]


def combine_episodes() -> pd.DataFrame | None:
    pieces = []
    for path in [config.raw(config.SOURCE_SCRAPING) / "episodes_airparif.csv",
                 config.DATA_PROCESSED / "alerts_opendata.csv"]:
        if path.exists():
            pieces.append(pd.read_csv(path, parse_dates=["date"]))
        else:
            print(f"   [!] {path.name} missing: episodes incomplete")
    if not pieces:
        return None
    ep = pd.concat(pieces, ignore_index=True).reindex(columns=COLUMNS)
    ep["priority"] = ep["source"].map(SOURCE_PRIORITY).fillna(9)
    ep = (ep.sort_values(["date", "pollutant", "priority"])
            .drop_duplicates(subset=["date", "pollutant"], keep="first")
            .drop(columns="priority")
            .reset_index(drop=True))
    return ep


if __name__ == "__main__":
    episodes = combine_episodes()
    if episodes is not None:
        print(f"{len(episodes)} episodes; per source: {episodes['source'].value_counts().to_dict()}")
