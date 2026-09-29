# Forecasting fine-particle (PM2.5) episodes in Paris — Data collection

This part of the project gathers all the data needed by the next-day (D+1) forecasting model:
the **target** (measured PM2.5), the **explanatory variables** (weather, calendar)
and an **external reference** (official pollution episodes).

## Overview of the sources

| # | Required type | Source | Data | Role in the project |
|---|---|---|---|---|
| 1 | **Web scraping** | airparif.fr (HTML page + PDF reports) | Official pollution episodes | External validation of the model, context |
| 2 | **API** | Météo-France, Climatological Data API (with key) | Daily weather observed at Paris-Montsouris | Main explanatory variables of the model |
| 2b | **API** | calendrier.api.gouv.fr | Public holidays | Calendar variable |
| 3 | **Flat file (CSV)** | Airparif (exports per pollutant) | Hourly PM2.5, NO₂, NOx, O₃, CO, 52 stations | **Target variable** + explanatory pollutants |
| 3b | **Flat file (CSV)** | Airparif Open Data (`alrt_idf.csv`) | Information and alert procedures, 2019 to March 2025 | Episode history, completed by the scraping |
| 3c | **Flat file (CSV)** | data.education.gouv.fr | School holidays | Calendar variable |
| 4 | Relational database | MySQL | The tables above, normalised | See step 5 |
| 5 | Big Data | BigQuery | Denormalised version | Next step |

## Secrets: API keys and MySQL password

Secrets are **never written in the code**. They are stored in a separate file,
`secrets/keys.env`, which git ignores: it cannot be pushed to GitHub by mistake.

1. Create the `secrets/` folder at the root of the project.
2. Copy `keys.env.example` into it, under the name `keys.env`.
3. Fill in the values: Météo-France key or Application ID, MySQL password.

All the scripts read this file automatically (`src/config.py`). If a value is missing, the
script stops with a message saying which line to fill in. (An existing `secrets/cles.env` file,
its former French name, is still read.)

**Protection:** `.gitignore` excludes `secrets/` and every `*.env` file. Only the template
`keys.env.example`, which contains no real value, goes to GitHub.

**Check before the first commit:** run `git status`. `secrets/keys.env` must **not** appear in
the list. You can also run `git check-ignore -v secrets/keys.env`: if the file is ignored, git
prints the `.gitignore` rule that excludes it.

## File organisation

In `data/raw/` there is **one folder per source**, not per year: the year is already in the file
names, and the scripts process all the years together.

```
data/
├── raw/
│   ├── airparif_measurements/  📥 2018_CO.csv … 2026_PM25.csv, *_boulevard_p_riph_rique.csv
│   ├── airparif_indices/       📥 indices_QA_commune_IDF_2014.csv … 2017
│   ├── airparif_alerts/        📥 alrt_idf.csv
│   ├── airparif_scraping/      ⚙️ PDF reports + episodes_airparif.csv (PDFs can be downloaded by hand)
│   ├── meteofrance/            ⚙️ weather_daily.csv
│   └── calendar/               📥 school_calendar.csv   ⚙️ public_holidays.csv
└── processed/                  ⚙️ cleaned tables (do not put anything here)
secrets/keys.env                📥 API keys and MySQL password (never on GitHub)
```

📥 = files you put there; ⚙️ = files created by the scripts. Keep the file names
(`2018_PM25.csv` or `2018 PM25.csv`: underscore, space and hyphen are accepted) and the folder
names exact. A report downloaded by hand is named `Bilan_Episodes_Web_<year>.pdf`.

Former French folder names (`airparif_mesures`, `airparif_alertes`, `calendrier`, `date`,
`meteo_france`) are **renamed automatically** the first time a script runs.

## Order of execution

All commands are run **from the project folder**.

| # | Command | Role | Requirements |
|---|---|---|---|
| 0 | `pip install -r requirements.txt` | Install the libraries | once |
| 1 | `python src/collect_flat_files.py` | **Flat files**: clean the measurements, indices, alerts and school holidays | 📥 files in `data/raw/` |
| 2 | `python src/run_web_collection.py` | **Web scraping** + **Météo-France API**, then the final table | Internet, `secrets/keys.env` filled in |
| 3 | `python src/load_mysql.py` | **MySQL database**: create the tables and load the data | MySQL running, steps 1 and 2 done |
| 4 | `python src/api_flask.py` | **Flask API** on http://127.0.0.1:5000 | step 3 done |
| 5 | `python src/export_bigquery.py` | **BigQuery**: denormalised CSV to upload in the console | steps 1 and 2 done |
| 6 | `notebooks/02_eda.ipynb`, then `notebooks/03_machine_learning.ipynb` | **EDA** and **machine learning** (charts saved in `reports/figures/`) | step 2 done |

**From Jupyter Notebook**, three options, which all work:
- open `notebooks/00_run_collection.ipynb` and run the cells in order (**the simplest**);
- in a cell, type `%run ../src/collect_flat_files.py` (adapt the path);
- paste the content of a script into a cell.

Each script starts with a small block that adds `src/` to `sys.path`, the list of folders where
Python looks for modules. This is what makes `import config` work in a notebook.

Step 1 takes about 1 minute, step 2 a few minutes. If one source of step 2 fails, the others
carry on. Each step can be run again safely: the files are rewritten and the database is
recreated.

**Explanatory notebook:** `notebooks/01_cleaning_airparif_measurements.ipynb` does the same
processing as `collect_flat_files.py` for the Airparif measurements, step by step, with the
results displayed, a control chart and a summary table of the cleaning choices. The script
re-runs everything automatically; the notebook is there to understand and present.

`run_web_collection.py` chains three scripts, which can also be run separately:
`scrape_airparif.py`, `api_meteofrance.py` and `build_dataset.py`.

`data/raw/` contains the **raw data, never modified**. `data/processed/` contains the cleaned
data. Keeping them apart means everything can be reproduced from the raw sources.

---

## Step 1 — Web scraping: Airparif pollution episodes

**Why this source?** It is the official list of the days when the regulatory thresholds were
exceeded in the Paris region. It does not exist as a CSV: the current year is shown in an HTML
table, past years in PDF reports. Scraping is therefore justified.

**What the script does (`scrape_airparif.py`)**

1. **Check `robots.txt`** with `urllib.robotparser`: it says which pages the website allows
   robots to visit.
2. **Download politely**: a User-Agent that identifies the project, a timeout and a 2-second
   pause between two requests.
3. **Parse the HTML** with BeautifulSoup. Each table row has 6 cells: level, pollutant, date,
   population criterion, area criterion, details. The criteria contain no text but a
   **"check" image**, so we test for an `<img>` tag, not for text. Header rows are skipped:
   only rows with a valid date and a known pollutant are kept.
4. **Extract the PDFs** with pdfplumber, then a **regular expression**. Two traps, found on the
   real 2025 report:
   - the characters of the table are flagged "not upright": pdfplumber's standard reading takes
     them for vertical text and returns `1 4 / 0` instead of `14/01/2025`. The script therefore
     **rebuilds the lines from the position of each character** (same height = same line,
     sorted from left to right);
   - subscript digits (the "10" of PM₁₀, the "3" of O₃) are written lower and fall on another
     line. Only "PM" or "O" remains, which the script completes into PM10 and O3.

   The regular expression also tolerates stuck or split words (`\s*` between fields). The real
   2025 PDF gives 15 episodes (5 PM10, 10 O3).
   **If a PDF was downloaded by hand** into `data/raw/airparif_scraping/`, the script uses it
   without downloading it again.
5. **Clean**:
   - convert the dates;
   - harmonise `Informations` / `Information` and translate the French values
     (`Alerte` → `alert`, `Bonne détection` → `correct_detection`…);
   - remove duplicates. If an episode is in both the HTML and the PDF, the PDF row is kept,
     as it is more complete: it also says whether the episode had been forecast.

**Bonus for the analysis:** the PDFs say whether each episode had been *forecast* by Airparif
(`correct_detection`, `missed_detection`, `false_alarm`). You can compare the detection rate of
your model with the official one, carefully: Airparif forecasts at regional level, your model at
one station.

**Known limits (to write in the report)**
- The page's `?year=` parameter does not return older years. The PDFs checked are those of
  **2024 and 2025**. For 2019-2023, the script tries the same URL pattern and reports missing
  files; the Open Data file (below) covers these years.
- Official episodes concern **PM10, O₃ and NO₂**, not PM2.5. They are an external reference,
  not the target.
- A scraper depends on the page layout: if the website changes, the parser must be adapted.

## Step 2 — Météo-France API: climatological data and public holidays

**Why Météo-France?** It is the **official** source of weather data in France. We use the daily
observations of **Paris-Montsouris**, the reference station of Paris. Weather is the main factor
that makes pollution vary from one day to the next: wind disperses pollutants, rain washes the
air, and cold weather increases wood heating.

**Getting access.** Create an account, then subscribe to the "Données Publiques Climatologie"
API on https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesClimatologie.
Two authentication modes are accepted:
- an **API key**, on the line `METEOFRANCE_API_KEY=` of `secrets/keys.env`;
- or an **Application ID**, on the line `METEOFRANCE_APPLICATION_ID=`. The script then exchanges
  it for a temporary token, valid for 1 hour.

The quota is 100 requests per minute; the script sends about twenty in total.

**What the script does (`api_meteofrance.py`)**

1. **Place one order per year** (2018 to 2026). The API is **asynchronous**: we order the data
   of a station over a period, and it answers with an order number (HTTP 202). A daily order
   covers at most 1 year.
2. **Fetch the file** with this number. While the server answers HTTP 204 ("not ready yet"),
   the script waits 10 s and tries again, for up to 5 minutes. HTTP 201 means the file is ready.
3. **Read the CSV**: `;` separator, decimal comma, dates as `YYYYMMDD`. Each measurement is
   followed by its quality code (e.g. `TM`, then `QTM`).
4. **Rename the columns** with the project names:

| Météo-France code | Name in the project | Content |
|---|---|---|
| `TM`, `TN`, `TX` | `temp_mean_c`, `temp_min_c`, `temp_max_c` | Mean, minimum, maximum temperature |
| `UM` | `humidity_mean_pct` | Mean humidity |
| `PMERM` | `pressure_mean_hpa` | Mean sea-level pressure |
| `RR` | `rain_mm` | Precipitation |
| `FFM` | `wind_mean_ms` | Mean wind speed |
| `FXY` | `wind_max_ms` | Maximum wind speed |
| `DXY` | `wind_dir_deg` | Direction of the maximum wind |

5. **Download the public holidays** from the official API `calendrier.api.gouv.fr`.

**About these data.** They are **real observations** measured at Paris-Montsouris, not model
outputs. The wind direction given is that of the day's **maximum** wind, not the dominant
direction.

*To check at the first run, as the real API could not be tested beforehand: the API address,
the station identifier 75114001 (Paris-Montsouris) and the column names. The script prints the
columns received.*

## Step 3 — Flat files: Airparif measurements and school holidays

**Why Airparif?** It is the official body accredited to monitor air quality in the Paris region.
Its exports give the **hourly** measurements of each station. They are the reference data, the
source of the target variable (PM2.5) and of other pollutants used as explanatory variables.

**Available files**

| Years | Files | Nature | Use |
|---|---|---|---|
| 2018 to 2026 (until 21/09) | `<year>_PM25.csv`, `_NO2`, `_NOX`, `_O3`, `_CO`; `_PM10` from 2018 to 2022; no `2021_NO2.csv` | **Hourly measurements** per station | **Target** (PM2.5) and explanatory variables |
| 2024, 2025, 2026 | `…_boulevard_p_riph_rique.csv` | 8 segments of the ring road | Context (see the caveat below) |
| 2014 to 2017 | `indices_QA_commune_IDF_<year>.csv` | Daily **indices** per municipality, not measurements | Separate table (see below) |

**Format of the station files.** Each file has 6 header rows: `code:pollutant`, station name,
station code, pollutant name, pollutant code, unit. Then one row per hour and **one column per
station** ("wide" format), with hours in UTC (`2026-01-01 01:00:00Z`).

**What the script does (`collect_flat_files.py`)**

1. **Read the 6 header rows separately** to build a metadata table: one row per column, with
   code, name, pollutant and unit.
2. **Go from wide to long format** with `melt`: one row per measurement (hour, station,
   pollutant, value), the format suited to a database and to pandas group-bys. Columns are
   identified by their **position**, not their name, because station RD934 appears twice in
   the PM2.5 file (one full column, one empty).
3. **Convert the time.** The first row is `01:00Z`: the timestamp marks the **end** of the
   measured hour. We subtract 1 h, then convert from UTC to Paris time, so that a "day" is the
   day lived in Paris. *Reasonable assumption, to be confirmed in the Airparif documentation.*
4. **Fix negative values.** There are 3,113 out of 9.3 million (0.03 %), the lowest being
   −3.0 µg/m³. This is device noise when the concentration is close to zero. As a concentration
   cannot be negative, these values are set to 0 and their number is printed.
5. **Drop stations without any measurement** in the file's year (list printed by the script,
   e.g. SOULT for NO₂ in 2025 and 2026). Each yearly file also contains the first hour of the
   next year: these 965 duplicates between files are removed.
6. **Infer the station type from its name**, as the file does not give it:
   - `traffic`: the name is a road (boulevard, avenue, RN20…);
   - `rural`;
   - `urban_background`: all the others.

   Matching is done on **whole words**. Otherwise "Champigny-sur-Ma**rn**e" would be classified
   as a national road: this error was spotted and fixed. *Heuristic to check against the
   official list of Airparif stations.*
7. **Compute the daily mean** per station and pollutant, only if **at least 18 hours out of 24**
   are measured (usual 75 % completeness rule). This rule rejects 7,177 days out of 393,738.
   The highest hourly value is also kept, useful for ozone.
8. **School holidays**: we keep the Paris academy (zone C) and remove the duplicated "Teachers"
   rows. Dates, stored in UTC, are converted to Paris time, then each period is unfolded into
   one row per day. The file must be downloaded from https://data.education.gouv.fr (dataset
   "Calendrier scolaire" → CSV export), then saved as `data/raw/calendar/school_calendar.csv`.

**Tables produced** (they directly prepare the MySQL database)

| Table | Rows | Role in the database |
|---|---|---|
| `stations.csv` | 52 stations: 29 urban background, 14 traffic, 8 rural, 1 observation | Dimension |
| `pollutants.csv` | 6 pollutants (CO, NO₂, NOx, O₃, PM10, PM2.5), with their unit | Dimension |
| `hourly_measurements.csv` | 9,302,210 | Facts (BigQuery candidate) |
| `daily_measurements.csv` | 386,561 | Aggregated facts (MySQL) |
| `ring_road_daily.csv` | 9,824 (8 segments) | Context |
| `aqi_indices_daily.csv` | 1,909,849 (1,309 areas × 4 years) | Geographic context |

**Alert history (`alrt_idf.csv`, Airparif Open Data).** This file contains 67 episode days
between 21/01/2019 and 05/03/2025: 35 for PM10 particles, 32 for ozone, including 2 alerts
during the July 2019 heatwave.

**What it contains, checked against the 2024-2025 PDF reports:** the **procedures triggered on
forecast**.
- The 9 "forecast" episodes of the reports are all there, false alarms included.
- The 3 "not forecast but observed" episodes are not.

It is therefore recorded as `forecast = 1` and `observed` unknown.

**Cleaning:**
- conversion from UTC to Paris time: `2019/01/20 23:00:00+00` becomes midnight on 21/01/2019,
  the actual date of the episode;
- harmonisation of the 4 pollutant spellings (`PM10`, `Particules PM10`, `O3`, `Ozone`);
- levels become `information` and `alert`.

**Merge with the web scraping (`src/episodes.py`).** The file covers 2019-2023, where the
scraping finds nothing. The scraping adds 2025-2026 and, thanks to the PDFs, the episodes
observed but not forecast. When the same day appears in several sources, the most complete one
is kept: PDF report, then HTML page, then Open Data file. In the final table,
`episode_pm10 = 1` means "procedure triggered or exceedance observed that day".

**First finding:** the **35 PM10 episode days** from 2019 to 2025 are **all** above the WHO
PM2.5 guideline in central Paris. Their mean is 37.7 µg/m³, against 11.4 µg/m³ on days without
an episode.

**Caveat on the ring-road files.** Their format is different: no "Z" for the time zone, integer
values, segments instead of stations, and an end on 13 September. This suggests they are
**modelled values** from Airparif, not measurements. *I am not certain: to check on the Airparif
website before presenting them as measurements.*

**2014-2017 indices: a different kind of data.** These files (`date, ninsee, no2, o3, pm10`) do
**not contain concentrations in µg/m³**. They are public-information **sub-indices**, computed
by modelling for each municipality. Airparif states that its indices are meant to inform the
public and **must not be used for statistical analyses of pollution trends**. They contain no
PM2.5 either. They are therefore kept in a separate table, `aqi_indices_daily.csv`, never joined
to the measurements. The `ninsee` code mixes three geographic levels (column `geo_level`):
- `0`: the whole Paris region (`region`);
- `75`, `77`…: departments (`department`);
- `75101`…: municipalities (`municipality`).

Possible uses: a descriptive map in the EDA, a volume of nearly 2 million rows for BigQuery.

**Station change in central Paris.** PM2.5 in central Paris is measured by **PA04C ("Paris
Centre", 4th arr.)** until **22/09/2019**, then by **PA01H (Les Halles, 1st arr.)**, about 1 km
away, from **01/10/2019**. The two stations never measured on the same day, so their levels
cannot be compared directly. The join leaves an 8-day gap. Both are urban background stations,
and they are joined into a single "central Paris" series.

**Coverage: continuous series from 2018 to 2026.** There are 2,955 days of PM2.5 (7 % missing
days), 658 of them above the WHO guideline. Three secondary gaps to know about:
- no NO₂ in 2021 (file missing), where NOx can replace it;
- no PM10 at the target station from 2023;
- 2020 is the lockdown year, with atypical emissions.

## Step 4 — Assembly (`build_dataset.py`)

- **A single target station: PA01H, Paris 1er Les Halles** (PA04C, Paris Centre, before
  October 2019).
  - It is an urban background station: it measures the air breathed by residents, not the air
    next to a road.
  - It is the most complete one (99 % of hours measured).
  - It also measures NO₂, NOx, O₃ and CO, used as explanatory variables.

  The column `source_station` gives the station of origin of each day. Joining PA04C and PA01H
  is an assumption, to mention in the report. Mean levels differ: 14.5 µg/m³ in 2018 against
  9.7 to 12.5 between 2020 and 2025. This is consistent with the general decrease of particles in
  Paris, but the part due to the station change cannot be fully separated.

  A multi-station average is avoided: when a station breaks down, the average changes
  composition and creates false jumps in the series.
- **A complete calendar** as the base: days without a measurement appear as missing values
  (NaN) instead of silently disappearing.
- **Merge on the date** with the weather, public holidays, school holidays and official episodes.
- **Binary target** `above_who`: daily mean PM2.5 above 15 µg/m³ (WHO 2021 guidelines).

Result: `dataset_daily.csv`, one row per day, ready for the EDA. It covers 01/01/2018 to
21/09/2026 (3,186 days).

| Year | Station | Days measured | Mean PM2.5 | Days > 15 µg/m³ |
|---|---|---|---|---|
| 2018 | PA04C | 338 | 14.5 µg/m³ | 115 |
| 2019 | PA04C then PA01H | 273 | 13.5 µg/m³ | 73 |
| 2020 | PA01H | 302 | 10.4 µg/m³ | 49 |
| 2021 | PA01H | 355 | 12.3 µg/m³ | 98 |
| 2022 | PA01H | 361 | 12.5 µg/m³ | 94 |
| 2023 | PA01H | 363 | 10.4 µg/m³ | 66 |
| 2024 | PA01H | 362 | 9.7 µg/m³ | 51 |
| 2025 | PA01H | 340 | 11.6 µg/m³ | 70 |
| 2026 (until 21/09) | PA01H | 261 | 10.6 µg/m³ | 42 |

---

## Step 5 — MySQL database

**Files:** `sql/schema.sql` (table creation), `src/load_mysql.py` (loading),
`sql/analysis_queries.sql` (5 analysis queries).

### Running the loading

```bash
pip install sqlalchemy pymysql
python src/load_mysql.py     # password read from secrets/keys.env (MYSQL_PASSWORD)
```

User, password, host, port and database name are set in `secrets/keys.env` (see "Secrets").
**The password is never written in the code**, otherwise it would end up on GitHub.

The script:
1. prepares the tables in pandas (identifiers, normalisation);
2. creates the database (`air_quality_paris` by default);
3. runs `schema.sql`;
4. inserts the dimensions, then the facts;
5. checks that MySQL contains as many rows as pandas.

It can be run again without creating duplicates: the schema drops then recreates the tables.

### Modelling choices (to defend)

- **Star schema.** One fact table, `daily_measurement`, surrounded by dimensions (`station`,
  `pollutant`, `calendar`) and two context tables (`weather_day`, `episode`). That makes
  **6 entities and 6 relationships**, for a required minimum of 4 and 3.
- **Normalisation.** The station name and the pollutant unit are stored only once, in their
  dimension. The fact table only contains numeric identifiers. Repeating "PARIS 1er Les Halles"
  on 386,000 rows would waste space and risk inconsistencies.
- **Numeric identifiers (`station_id`) rather than the code (`PA01H`) as key.** An integer is
  smaller and faster to join. The identifiers are created in Python, which lets us replace the
  codes by the identifiers before insertion.
- **Foreign keys and a uniqueness constraint** on (date, station, pollutant). MySQL thus refuses
  any orphan or duplicated measurement. These keys also let Workbench draw the relationships.
- **Loading with SQLAlchemy rather than the import wizard**, as advised in the course: types
  under control, correct dates, reproducible loading.
- **`if_exists="append"`, not `"replace"`.** With `"replace"`, pandas would recreate the tables
  its own way, without primary or foreign keys.
- **Unknown values as `NULL`, not 0.** If public holidays were not collected,
  `is_public_holiday` is `NULL` (unknown) and not 0, which would mean "not a holiday".
- **Hourly measurements (9.3 million rows) do not go into MySQL**: they will go into BigQuery.

### Entity-relationship diagram

In MySQL Workbench: menu *Database > Reverse Engineer…*, choose the connection, then the
`air_quality_paris` database, and confirm until the end. The diagram shows the tables and the
relationships. Export it as an image with *File > Export > Export as PNG*.

**Screenshots to take for the report and the slides:** the diagram; the script output (counts
OK); the result of one or two queries.

### Results of the 5 queries (on the 2018-2026 data)

1. **Trend.** PM2.5 decreases at urban background stations, from 12.8 µg/m³ in 2018 to 8.4 in
   2024 (10.5 in 2025). Traffic stations always stay above background stations. The number of
   stations varies over the years (column `n_stations`): the means must be read carefully.
2. **Seasonality.** In central Paris, **March** is the riskiest month: 37.5 % of days exceed the
   WHO guideline, followed by January (34.5 %) and February (32.0 %). **August** is the safest
   (5.0 %). Winter and early spring concentrate the episodes.
3. **Traffic effect.** Next to roads, NO₂ is about **twice as high** as in the urban background
   (ratio 1.8 to 2.1). The excess due to traffic went from 30.8 µg/m³ in 2018 to about
   15 µg/m³ in 2024-2026: it has been halved. *(2021 is missing, as there is no NO₂ file for
   that year.)*
4. **Worst days.** The record is 15/01/2022 (68.5 µg/m³, more than 4 times the WHO guideline).
   The 10 worst days all fall between December and March, with almost no rain (0.2 mm at most), high pressure (above
   1,020 hPa) and, for 8 of them, a mean wind below 2.5 m/s.
5. **Official episodes.** 48 of the 49 particle (PM10) episode days from 2019 to 2026 exceed the
   WHO PM2.5 guideline in central Paris, including all 5 PM10 "false alarms" of the 2024-2025
   reports: on those days the Parisian air was degraded, without reaching the regional triggering
   criteria. Ozone episodes are different: only 21 of the 46 measured O3 days exceed the PM2.5
   guideline, as summer ozone and winter particles are different phenomena.

Query results exported from MySQL Workbench: `reports/sql_results/`.

---

## Step 6 — REST API with Flask (`src/api_flask.py`)

The API reads the MySQL database and returns JSON: anyone can use the data without knowing SQL.
**2 resources, 4 endpoints**: for each resource, one paginated list with filters and one single
object with nested details.

| Endpoint | Returns | Parameters |
|---|---|---|
| `GET /stations` | list of stations, paginated | `type` (urban_background, traffic, rural, observation), `name`, `page`, `per_page` |
| `GET /stations/<station_code>` | one station + a summary per pollutant (days measured, period, mean, days above the WHO PM2.5 guideline) | — |
| `GET /measurements` | daily measurements, paginated, most recent first | `station`, `pollutant`, `start`, `end` (YYYY-MM-DD), `min_value`, `page`, `per_page` |
| `GET /measurements/<id>` | one measurement + station, pollutant, weather of the day, official episodes of the day | — |
| `GET /` | documentation page (HTML), **public** | — |
| `GET /openapi.yaml` | OpenAPI 3 specification (`docs/openapi.yaml`), **public** | — |

**Authentication:** every data endpoint requires the API key, sent in the HTTP header
`X-API-Key`. The key is the line `API_KEY=` of `secrets/keys.env` (never in the code). To create
one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`, then paste the result after
`API_KEY=`. Without a valid key the API answers **401**; if `API_KEY` is empty, **503**.

```bash
curl -H "X-API-Key: YOUR_KEY" "http://127.0.0.1:5000/stations?type=traffic&per_page=5"
```

In a browser (quick test only: the URL stays in the history), add `api_key=YOUR_KEY`:

- http://127.0.0.1:5000/stations?type=traffic&per_page=5&api_key=YOUR_KEY
- http://127.0.0.1:5000/stations/PA01H?api_key=YOUR_KEY
- http://127.0.0.1:5000/measurements?station=PA01H&pollutant=PM25&start=2025-01-01&min_value=15&api_key=YOUR_KEY

The OpenAPI file can be opened in https://editor.swagger.io (File > Import file) to get an
interactive documentation page.

**Design choices (to defend)**
- **Pagination** (20 items by default, 100 at most): the API never sends 386,000 rows at once.
  Each list answer gives `total_items`, `total_pages` and the links `next_page` / `previous_page`.
- **Filters as SQL parameters** (`:station`, `:start`…), never pasted into the SQL text: this
  protects against SQL injection.
- **Nested details** on the single-object endpoints: the station and the pollutant as
  sub-objects, and the context of the day taken from the other tables (weather, episodes).
- **Clear errors in JSON**: 400 for an invalid parameter (e.g. `start=2025-13-01`), 401 for a
  missing or wrong key, 404 for an unknown station or measurement.
- **API key checked before every request** (`check_api_key`); the documentation stays public
  because it contains no data.

**Run:** `python src/api_flask.py`, then open http://127.0.0.1:5000 in a browser. Stop it with
Ctrl+C (in Jupyter: *Kernel > Interrupt*). **Screenshots for the report:** the documentation
page, one list answer and one detail answer.

## Step 7 — BigQuery (`src/export_bigquery.py`)

**Why:** the course asks for a Big Data system. The BigQuery public datasets were checked first
(OpenAQ air-quality dataset, last query of `sql/bigquery_queries.sql`); a **denormalised
version of the MySQL database** is loaded, with partitioning and clustering.

**Denormalised table:** one row = one station × pollutant × day, with the station name and type,
the pollutant name and unit, the calendar, the weather of the day and the official episode, all
in the same row (386,561 rows × 27 columns, about 45-60 MB). No JOIN is needed: in a data
warehouse, storage is cheap and joins on big tables are expensive.

**Partitioning and clustering:**
- partition by the integer column **`year`** (range 2018 → 2027, interval 1). A date partition
  would be the usual choice, but the free **sandbox deletes date partitions older than 60 days**:
  all our 2018-2026 data would disappear. *To confirm with the check query after the upload.*
- cluster by **`pollutant_code`, `station_code`**, the two most frequent filters.

**Step by step (about 40 minutes)**
1. Run `python src/export_bigquery.py`. It creates `data/processed/bigquery/daily_measurements_denorm.csv`
   and `bigquery_schema.json`.
2. Go to https://console.cloud.google.com/bigquery with a Google account. Accept the sandbox
   (free, no credit card) and create a project (e.g. `air-quality-paris`).
3. In the Explorer panel: ⋮ next to the project > **Create dataset**: ID `air_quality_paris`,
   location **EU**.
4. ⋮ next to the dataset > **Create table**:
   - Source: **Upload**, choose the CSV file, format CSV;
   - Table: `daily_measurements`;
   - Schema: enable **Edit as text** and paste the content of `bigquery_schema.json`;
   - Partitioning: **Partition by field** `year`, integer range start `2018`, end `2027`, interval `1`;
   - Clustering order: `pollutant_code,station_code`;
   - Advanced options: **Header rows to skip = 1**;
   - **Create table**.
5. Open `sql/bigquery_queries.sql` and run the queries one by one in the console.

**Screenshots:** the table's *Details* tab (partitioning and clustering visible), queries 1a and
1b with their "will process X MB" message, and the result of query 3.

**Sandbox limits to know:** tables expire after 60 days (fine for the presentation), 10 GB of
storage in total (not given back when data are deleted, so avoid uploading many times), 1 TB of
queries per month.

---

## Step 8 — Exploratory analysis and machine learning

Both notebooks read `data/processed/dataset_daily.csv` and save their charts in `reports/figures/`
(reused in the report and the slides).

**`notebooks/02_eda.ipynb` — EDA.** Missing values, trend (≈ 14.5 µg/m³ in 2018 → ≈ 10 in 2024),
seasonality (January-March: about a third of days above the WHO guideline, August 5 %), weather
effects (calm days below 2 m/s: 41 % above the guideline, windy days ≥ 5 m/s: 8 %), persistence
(if today > 15 µg/m³, tomorrow is also above in 65 % of cases, against 10 % otherwise), official
episodes (98 % of PM10 episode days are above the WHO PM2.5 guideline), traffic vs background NO2.

**`notebooks/03_machine_learning.ipynb` — D+1 forecast.**
- Features known on the evening of day D: PM2.5 of D, D−1, D−2 and 7-day mean, NOx and ozone peak
  of D, weather of D+1 (observed, as a stand-in for the forecast: assumption), calendar of D+1.
- Time-based split: train 2018-2023, validation 2024, test 2025, demo 2026.
- Models: persistence baseline ("tomorrow = today"), linear regression, Random Forest tuned on
  the validation year (grid on `max_depth`, `min_samples_leaf`, `max_features`).
- Imbalance (≈ 20 % of exceedance days): alert threshold chosen on 2024 so that at least 80 % of
  exceedances are caught (recall first), keeping the highest such threshold (13 µg/m³).
- **Test 2025:** Random Forest MAE 2.99 µg/m³ vs 3.88 for persistence (−23 %); exceedances:
  recall 0.94, precision 0.57. **Demo 2026:** MAE 2.73 vs 3.34; recall 0.76, precision 0.53.
- Most important features: PM2.5 of today, then tomorrow's wind (direction, speed) and temperature.

---

## Tools used, and explanations for the jury

All the code relies on tools seen in the course (pandas, numpy, requests, BeautifulSoup,
SQLAlchemy, `re`, `pathlib`, `os`, `time`, functions, `try/except`), with two exceptions. Here
they are, with a model answer in case of a question:

- **`pdfplumber` (reading the PDF reports).** *"A PDF is not a table: it is a page where each
  character is placed at a position (x, y). pdfplumber gives me the list of these characters
  with their coordinates. I group those at the same height to rebuild the lines, sort them from
  left to right, then apply a regular expression to find the date, the pollutant, the threshold,
  'forecast' and 'observed'. I had to do this myself, because pdfplumber's automatic reading
  took the table for vertical text."*
- **`urllib.robotparser` (Python standard library).** *"Before scraping, I read the website's
  `robots.txt` file, which says what robots are allowed to visit. robotparser reads it and tells
  me whether a page is allowed. It is a good-practice rule of scraping."*

## GDPR and security

- **No personal data** in the database: pollution measurements, weather, calendars and official
  episodes, all public. The **register of processing activities** and the **data-sorting
  procedures** (with their frequency) are in [`docs/rgpd.md`](docs/rgpd.md).
- **The API is protected** by a key (see Step 6).
- **The scraping** only concerns public Airparif pages. It respects `robots.txt`, identifies
  itself with an explicit User-Agent and pauses between requests.
- **Licences**: Airparif data are under the ODbL licence (attribution required); Météo-France
  and data.gouv.fr data are under the Licence Ouverte (Etalab).
- **API keys and the MySQL password** are never in the code: they are stored in
  `secrets/keys.env`, which git ignores.

## Sources

- Airparif, episode history: https://www.airparif.fr/historique-des-episodes-de-pollution
- Airparif, PDF reports: https://www.airparif.fr/sites/default/files/Bilan_Episodes_Web_2025.pdf
- Airparif, data (measurements, Open Data): https://www.airparif.fr/airparif/nos-donnees
- Météo-France, Climatological Public Data API:
  https://portail-api.meteofrance.fr/web/fr/api/DonneesPubliquesClimatologie
- Météo-France API documentation:
  https://confluence-meteofrance.atlassian.net/wiki/spaces/OpenDataMeteoFrance/pages/854261785/API+Donn+es+Climatologiques
- Public holidays: https://calendrier.api.gouv.fr
- School calendar: https://data.education.gouv.fr
- WHO global air quality guidelines (2021)

## AI use statement

Claude (Anthropic) was used throughout the project to generate and debug code, suggest methods and draft the report and presentation. The author executed all collection, loading and modelling steps herself, reviewed the code, and verified the results. Several AI outputs were corrected during this review, for example a wrong episode date, an unverified claim about the OpenAQ dataset, and a false-alarm count. The final choices and their justification are the author's.
