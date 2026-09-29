"""
SOURCE 2 — WEB SCRAPING: official pollution episodes (Airparif)
================================================================

Why web scraping?
    Airparif publishes the list of days when the information and alert
    thresholds were exceeded, but only:
      - as an HTML TABLE for the current year (no file to download);
      - as PDF reports ("Bilan des épisodes") for past years.
    There is no ready-made CSV: the information has to be extracted from the
    web page and from the PDFs.

Output:
    data/raw/airparif_scraping/episodes_airparif.csv — one row per (date, pollutant):
      date, pollutant, level, source, forecast, observed, forecast_result,
      criterion_population, criterion_area

Steps:
    1. Check robots.txt (are we allowed to visit these pages?)
    2. Download the HTML page of the current year and extract its table
    3. Download the PDF reports of past years and extract their rows
    4. Merge, clean, remove duplicates, save as CSV
"""
import re
import time
from urllib import robotparser

import pandas as pd
import pdfplumber
import requests
from bs4 import BeautifulSoup

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

SITE_URL = "https://www.airparif.fr"
EPISODES_PAGE_URL = SITE_URL + "/historique-des-episodes-de-pollution"

# PDF report URLs checked by hand (September 2026).
# The naming follows the pattern Bilan_Episodes_Web_<year>.pdf.
PDF_URLS = {
    2024: SITE_URL + "/sites/default/files/Bilan_Episodes_Web_2024.pdf",
    2025: SITE_URL + "/sites/default/files/Bilan_Episodes_Web_2025.pdf",
}
# Older years: we TRY the same URL pattern. If the file does not exist
# (error 404), the script says so and carries on.
YEARS_TO_TRY = [2018, 2019, 2020, 2021, 2022, 2023]

POLLUTANTS = ["PM10", "PM2.5", "O3", "NO2", "SO2"]
DATE_REGEX = re.compile(r"\d{2}/\d{2}/\d{4}")

# The PDF reports are in French: their values are translated into English.
FORECAST_RESULTS = {"Bonne détection": "correct_detection",
                    "Détection manquée": "missed_detection",
                    "Fausse détection": "false_alarm"}


# -------------------------------------------------------------------------------
# Step 1: respect robots.txt
# -------------------------------------------------------------------------------
_ROBOTS = None  # robots.txt is read once, then kept in memory


def allowed_by_robots(url: str) -> bool:
    """Return True if the website's robots.txt allows our User-Agent to visit the URL."""
    global _ROBOTS
    if _ROBOTS is None:
        _ROBOTS = robotparser.RobotFileParser()
        # robots.txt is downloaded with requests and OUR User-Agent: the
        # .read() method of the standard library uses a generic Python
        # User-Agent, sometimes refused, and would then wrongly conclude
        # that the whole website is forbidden.
        try:
            response = requests.get(SITE_URL + "/robots.txt",
                                    headers={"User-Agent": config.USER_AGENT},
                                    timeout=config.TIMEOUT_SECONDS)
            _ROBOTS.parse(response.text.splitlines() if response.status_code == 200 else [])
            print(f"  robots.txt read (HTTP {response.status_code})")
        except requests.RequestException as e:
            print(f"  [!] robots.txt unreadable ({e}) — check it by hand.")
            _ROBOTS.parse([])  # no known rule
    return _ROBOTS.can_fetch(config.USER_AGENT, url)


def download(url: str) -> requests.Response | None:
    """Download a URL politely: explicit User-Agent, timeout, pause."""
    if not allowed_by_robots(url):
        print(f"  [x] Forbidden by robots.txt: {url}")
        return None
    response = requests.get(
        url,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.TIMEOUT_SECONDS,
    )
    time.sleep(config.PAUSE_SECONDS)  # do not overload the server
    if response.status_code != 200:
        print(f"  [!] {url} -> HTTP {response.status_code}")
        return None
    return response


# -------------------------------------------------------------------------------
# Step 2: extract the HTML table
# -------------------------------------------------------------------------------
def parse_html_page(html: str) -> pd.DataFrame:
    """
    Extract the rows of the exceedance table.

    Observed structure of a row (6 <td> cells):
        Level | Pollutant | Date | Population criterion | Area criterion | Details
    The two criteria contain no text: a "check.png" image means the criterion
    is met. So we test whether the cell contains an <img>.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue  # header row or empty row
        texts = [c.get_text(strip=True) for c in cells]
        # Keep only real data rows: a date + a known pollutant
        if not DATE_REGEX.fullmatch(texts[2]) or texts[1] not in POLLUTANTS:
            continue
        rows.append({
            "date": texts[2],
            "pollutant": texts[1],
            "level": texts[0],                          # "Informations" or "Alerte"
            "criterion_population": cells[3].find("img") is not None,
            "criterion_area": cells[4].find("img") is not None,
            "source": "html_page",
        })
    return pd.DataFrame(rows)


# -------------------------------------------------------------------------------
# Step 3: extract the PDFs
# -------------------------------------------------------------------------------
# A row of a PDF report looks like:
#   "13/01/2024 PM10 Information OUI NON Fausse détection"
# (OUI/NON = yes/no for "forecast" then "observed").
# PDF text extraction is imperfect: we sometimes get "O3Information" (words
# stuck together) or "O3\nInformation" (line break). Hence "\s*" (zero or
# more spaces/line breaks) between fields.
# In the real reports, subscript digits (the "10" of PM10, the "3" of O3,
# the "2" of NO2) are smaller and slightly lower: when extracted, they fall
# on another line and only "PM", "O" or "NO" remains. So we also accept these
# short forms, then complete them (FULL_NAMES).
PDF_ROW_REGEX = re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<pollutant>PM10|PM2\.5|O3|NO2|SO2|PM|NO|O)\s*"
    r"(?P<level>Information|Alerte)\s*"
    r"(?P<forecast>OUI|NON)\s*"
    r"(?P<observed>OUI|NON)\s*"
    r"(?P<result>Bonne détection|Détection manquée|Fausse détection)?"
)

FULL_NAMES = {"PM": "PM10", "O": "O3", "NO": "NO2"}   # only pollutants of the procedures


def parse_pdf_text(text: str) -> pd.DataFrame:
    """Apply the regular expression to the whole text of the PDF."""
    rows = []
    for m in PDF_ROW_REGEX.finditer(text):
        rows.append({
            "date": m["date"],
            "pollutant": FULL_NAMES.get(m["pollutant"], m["pollutant"]),
            "level": m["level"],
            "forecast": m["forecast"] == "OUI",
            "observed": m["observed"] == "OUI",
            "forecast_result": FORECAST_RESULTS.get(m["result"]),
            "source": "pdf_report",
        })
    return pd.DataFrame(rows)


def text_by_lines(page, y_tolerance: float = 2.0, column_gap: float = 6.0) -> str:
    """
    Rebuild the text lines from the POSITION of each character.

    Why? In the Airparif reports, the characters of the table are flagged as
    "not upright" (upright=False): the standard page.extract_text() method
    takes them for vertical text and reads them column by column, giving
    "1 4 / 0" instead of "14/01/2025". Their coordinates are correct though:
      - characters at (almost) the same height are grouped -> one line;
      - they are sorted from left to right;
      - a space is added when the horizontal gap is large -> new column.
    """
    lines = {}
    for c in page.chars:
        key = round(c["top"] / y_tolerance)          # same "level" = same line
        lines.setdefault(key, []).append(c)
    result = []
    for key in sorted(lines):
        characters = sorted(lines[key], key=lambda c: c["x0"])
        text, previous_end = "", None
        for c in characters:
            if previous_end is not None and c["x0"] - previous_end > column_gap:
                text += " "
            text += c["text"]
            previous_end = c["x1"]
        result.append(text)
    return "\n".join(result)


def extract_pdf_text(path) -> str:
    """Open the PDF file and return all its text, line by line."""
    with pdfplumber.open(path) as pdf:
        return "\n".join(text_by_lines(page) for page in pdf.pages)


def find_local_pdf(year: int) -> Path | None:
    """PDF already downloaded (by this script or by hand), under one of its usual names."""
    folder = config.raw(config.SOURCE_SCRAPING)
    for name in [f"Bilan_Episodes_Web_{year}.pdf", f"bilan_episodes_{year}.pdf"]:
        if (folder / name).exists():
            return folder / name
    return None


# -------------------------------------------------------------------------------
# Step 4: clean and merge
# -------------------------------------------------------------------------------
def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Harmonise formats and remove duplicates."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], format="%d/%m/%Y")
    # "Informations" (HTML) and "Information" (PDF) mean the same thing
    df["level"] = df["level"].str.lower().str.rstrip("s").map(
        {"information": "information", "alerte": "alert"}
    )
    # The HTML page only lists OBSERVED exceedances
    if "observed" not in df:
        df["observed"] = pd.NA
    df.loc[df["source"] == "html_page", "observed"] = True
    # If the same (date, pollutant) appears twice, keep the PDF row, which is
    # more complete (it also gives the quality of the forecast).
    df["priority"] = (df["source"] == "pdf_report").astype(int)
    df = (df.sort_values("priority", ascending=False)
            .drop_duplicates(subset=["date", "pollutant"])
            .drop(columns="priority")
            .sort_values(["date", "pollutant"])
            .reset_index(drop=True))
    columns = ["date", "pollutant", "level", "source", "forecast", "observed",
               "forecast_result", "criterion_population", "criterion_area"]
    return df.reindex(columns=columns)


def main():
    pieces = []

    print("1) HTML page of the current year")
    response = download(EPISODES_PAGE_URL)
    if response is not None:
        df_html = parse_html_page(response.text)
        print(f"   {len(df_html)} rows extracted")
        pieces.append(df_html)

    print("2) PDF reports of past years")
    urls = dict(PDF_URLS)
    for year in YEARS_TO_TRY:
        urls.setdefault(year, f"{SITE_URL}/sites/default/files/Bilan_Episodes_Web_{year}.pdf")
    for year, url in sorted(urls.items()):
        # If the PDF was already downloaded (by this script or by hand in
        # data/raw/airparif_scraping/), it is reused without any request.
        local = find_local_pdf(year)
        if local is not None:
            origin = "local file"
        else:
            response = download(url)
            if response is None:
                print(f"   {year}: no PDF at this address (to look for by hand)")
                continue
            local = config.raw(config.SOURCE_SCRAPING) / f"Bilan_Episodes_Web_{year}.pdf"
            local.write_bytes(response.content)  # keep the raw PDF
            origin = "downloaded"
        df_pdf = parse_pdf_text(extract_pdf_text(local))
        print(f"   {year}: {len(df_pdf)} rows extracted ({origin})")
        pieces.append(df_pdf)

    if not pieces:
        print("No data retrieved.")
        return
    episodes = clean(pd.concat(pieces, ignore_index=True))
    path = config.raw(config.SOURCE_SCRAPING) / "episodes_airparif.csv"
    episodes.to_csv(path, index=False)
    print(f"\n{len(episodes)} episodes saved to {path}")
    print(episodes.groupby([episodes["date"].dt.year, "pollutant"]).size().unstack(fill_value=0))


if __name__ == "__main__":
    main()
