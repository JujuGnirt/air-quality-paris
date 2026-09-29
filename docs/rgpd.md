# GDPR (RGPD): register of processing activities and data-sorting procedures

Project: *Forecasting fine-particle (PM2.5) pollution in central Paris one day ahead*
Controller (person responsible): Julie TRING, author of the project.
Last review: 29 September 2026. Next review: at every change of the schema or of a
data source, and at least once a year.

## 1. Summary

The database (MySQL, 6 tables) and its BigQuery copy contain
**no personal data**: only measurements from fixed monitoring stations, weather
observed at one Météo-France station, calendars and official pollution episodes.
None of these values relates to an identified or identifiable person.

The only personal data met in the project are **outside the database**: the IP
addresses shown by the Flask development server in its console (section 2, line T7).
They are not stored.

## 2. Register of processing activities (GDPR article 30)

| # | Processing | Purpose | Data categories | Personal data? | Source | Retention | Recipients | Security |
|---|---|---|---|---|---|---|---|---|
| T1 | Collection of Airparif measurements (flat files) | Target and pollutant features | Station code, date, hourly concentration | No | Airparif, ODbL licence | Raw files kept for reproducibility | Project, jury | Local disk; raw files never modified |
| T2 | Web scraping of pollution episodes (HTML + PDF) | Official episodes, validation | Date, pollutant, level, forecast result | No (public institutional pages, no user content) | airparif.fr | Same as T1 | Project, jury | robots.txt respected, identified User-Agent, pauses |
| T3 | Météo-France API (daily climatology) | Weather features | Temperature, wind, rain, pressure at station 75114001 | No | Météo-France, Licence Ouverte | Same as T1 | Project, jury | API key in `secrets/keys.env`, excluded from Git |
| T4 | Public holidays API and school calendar | Calendar features | Date, holiday name | No | api.gouv.fr, data.education.gouv.fr | Same as T1 | Project, jury | — |
| T5 | Storage in MySQL (6 tables) | Integrity, SQL analysis, API back-end | Tables station, pollutant, calendar, weather_day, daily_measurement, episode | No (column list checked, procedure P1) | T1 to T4 | Duration of the project | Project; API users | MySQL password in `secrets/keys.env`; local server |
| T6 | Denormalised copy in BigQuery | Big-data queries | Same columns as T5 | No | T5 | Duration of the project | Project | Private Google Cloud project, author's account only |
| T7 | Flask API access log (console) | Debugging during development | **IP address**, date and time, requested URL | **Yes (IP address)** | Visitors of the API | **Not stored**: shown in the console only, lost when the server stops | Author only | Server listens on 127.0.0.1 (local machine only) |
| T8 | API authentication | Restrict access to the data | One shared API key | No (key not linked to a named person) | `secrets/keys.env` | Changed if leaked | — | Key never in the code or on GitHub |

Legal basis: T1 to T6 process no personal data, so no legal basis is required.
T7 would rely on legitimate interest (security and debugging of the service).

Rights of data subjects: no personal data are stored, so there is nothing to
access, correct or delete. If this changes (for example user accounts for the
API), this register is updated first, and a contact address for GDPR requests is
added to the API documentation.

## 3. Data-sorting procedures (procédures de tri)

Purpose: make sure that no personal data enters the database, and remove it if
it ever does.

| # | Procedure | Automated? | Frequency | What to do if it fails |
|---|---|---|---|---|
| P1 | **Column check**: list every column of the database (query below) and compare it with the data dictionary (report, section 4). Any column that could identify a person (name, e-mail, address, phone, IP, free text) is refused. | Semi-automated (SQL query, manual comparison) | At every schema change, and every month while the project runs | Drop the column, reload the table, document the change here |
| P2 | **Source check**: a new source is accepted only if it publishes aggregated or station-level data; scraping targets institutional pages only, never comments or user profiles. | Manual | Before adding a source or changing a scraper | Do not collect the source |
| P3 | **Minimisation at loading**: `load_mysql.py` builds each table from an explicit list of columns; every other column of the raw files is dropped before `to_sql`. | Automated (in the code) | At every loading | Row counts checked by `load_mysql.py`; fix the column list |
| P4 | **Logs**: the development server does not write logs to disk. If the API were deployed, access logs would be rotated daily and deleted automatically after 6 months at most, a duration within the CNIL recommendations for security logs. | Automated (log rotation) | Daily | Delete the log files older than the limit |
| P5 | **Secrets**: `secrets/keys.env` is ignored by Git; `git status` is read before every push; a leaked key is revoked and replaced. | Manual | Before every push | Revoke the key, rewrite the Git history if needed |
| P6 | **Review of this register** | Manual | Once a year, and at every new processing | Update sections 2 and 3 |

Query used by P1 (MySQL):

```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = DATABASE()   -- the database currently selected
ORDER BY table_name, ordinal_position;
```

Expected result (columns defined in `sql/schema.sql`): 6 tables, only station,
pollutant, date, calendar, weather, measurement and episode columns. No personal data.
