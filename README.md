# Air quality in Paris: forecasting PM2.5 one day ahead
RNCP37827 (Développeur en intelligence artificielle), block BC01 · Julie TRING · September 2026 · report: `reports/RNCP_report_air_quality_paris.pdf`
 
**Goal.** Fine particles are linked to nearly 40,000 deaths a year in France. On the evening of day D, can we tell whether PM2.5 in central Paris will exceed the WHO guideline (15 µg/m³) on day D+1?
 
**Data (5 source types).** Airparif CSV files (9.3 M hourly values, 52 stations, 2018-2026) · web scraping of Airparif pollution episodes (HTML + PDF) · Météo-France API (daily weather) + public-holiday API · MySQL database (star schema, 6 tables) · BigQuery (denormalised table, partitioned by year, clustered).
 
**Results.** PM2.5 fell from 14.5 to 9.7 µg/m³ (2018 → 2024), but 22 % of days still exceed the guideline, mostly in winter and March with calm weather. On 2025 (unseen), a Random Forest forecasts D+1 PM2.5 with a mean error of 2.99 µg/m³ (−23 % vs "tomorrow = today") and announces 60 of the 64 days above the guideline.
 
| Folder | Content |
|---|---|
| `src/` | collection (flat files, scraping, API), cleaning, MySQL loading, BigQuery export, Flask API |
| `sql/` | `schema.sql`, 5 analysis queries (MySQL), BigQuery queries |
| `notebooks/` | cleaning, EDA, machine learning |
| `docs/` | `openapi.yaml` (API specification), `rgpd.md` (GDPR register and procedures) |
| `reports/` | report (PDF, Word) and figures |
 
**Run** (Python 3, MySQL 8): put the Airparif CSV files in `data/raw/airparif_measurements/`, copy `keys.env.example` to `secrets/keys.env` and fill it in, then
```
pip install -r requirements.txt
python src/collect_flat_files.py && python src/run_web_collection.py
python src/load_mysql.py && python src/export_bigquery.py
python src/api_flask.py        # http://127.0.0.1:5000, header X-API-Key required
```
 
**GDPR and security.** No personal data in the database (register in `docs/rgpd.md`); keys stored in `secrets/keys.env`, never on GitHub. Data: Airparif (ODbL), Météo-France (Licence Ouverte).
 
**AI use.** Claude (Anthropic) helped generate and debug code and draft the report; the author ran every step, verified the results and corrected several AI errors.
