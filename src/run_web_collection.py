"""
Run the collection steps that need the Internet, in order:
    1. Airparif web scraping (pollution episodes) -> data/raw/airparif_scraping/
    2. Météo-France API + public holidays         -> data/raw/meteofrance/, data/raw/calendar/
then rebuild the final daily table with these new columns.

Run from the project folder:   python src/run_web_collection.py
Expected duration: a few minutes (deliberate pauses between requests).
"""
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

import api_meteofrance
import build_dataset
import scrape_airparif

steps = [
    ("WEB SCRAPING — Airparif episodes", scrape_airparif.main),
    ("API — Météo-France weather (Paris-Montsouris) and public holidays", api_meteofrance.main),
    ("ASSEMBLY — daily table", build_dataset.main),
]

if __name__ == "__main__":
    for title, function in steps:
        print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)
        try:
            function()
        except Exception as error:
            # A failing step does not stop the next ones: we print the type
            # and message of the error so that it can be fixed.
            print(f"\n[FAILED] {title}: {type(error).__name__} — {error}")
