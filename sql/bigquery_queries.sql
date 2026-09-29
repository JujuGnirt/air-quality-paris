-- =====================================================================
-- BIGQUERY QUERIES — denormalised table air_quality_paris.daily_measurements
-- =====================================================================
-- Paste each query into the BigQuery console, one at a time.
-- Before running, the console shows "This query will process X MB":
-- take a screenshot of it for queries 1a / 1b (effect of the partition).
-- No JOIN anywhere: the table is denormalised (see src/export_bigquery.py).
-- =====================================================================


-- ---------------------------------------------------------------------
-- QUERY 1a — Monthly PM2.5 in central Paris in 2024, WITH the partition filter.
-- `year = 2024` lets BigQuery read only the 2024 partition (about 1/9 of the
-- table); the clustering on pollutant_code / station_code reduces it further.
-- ---------------------------------------------------------------------
SELECT
    month,
    ROUND(AVG(daily_mean), 1)  AS pm25_mean_ug_m3,
    SUM(above_who_pm25)        AS days_above_who
FROM air_quality_paris.daily_measurements
WHERE year = 2024
  AND pollutant_code = 'PM25'
  AND station_code = 'PA01H'
GROUP BY month
ORDER BY month;


-- ---------------------------------------------------------------------
-- QUERY 1b — Same question, but filtering on `date` instead of `year`.
-- The table is partitioned on `year`, so this filter cannot skip the other
-- partitions: compare the "will process X MB" message with query 1a.
-- ---------------------------------------------------------------------
SELECT
    month,
    ROUND(AVG(daily_mean), 1)  AS pm25_mean_ug_m3,
    SUM(above_who_pm25)        AS days_above_who
FROM air_quality_paris.daily_measurements
WHERE date BETWEEN '2024-01-01' AND '2024-12-31'
  AND pollutant_code = 'PM25'
  AND station_code = 'PA01H'
GROUP BY month
ORDER BY month;


-- ---------------------------------------------------------------------
-- QUERY 2 — Traffic versus background NO2 per year, with no JOIN:
-- the station type is already in each row.
-- ---------------------------------------------------------------------
SELECT
    year,
    ROUND(AVG(IF(station_type = 'urban_background', daily_mean, NULL)), 1) AS no2_background,
    ROUND(AVG(IF(station_type = 'traffic', daily_mean, NULL)), 1)          AS no2_traffic
FROM air_quality_paris.daily_measurements
WHERE pollutant_code = 'NO2'
GROUP BY year
ORDER BY year;


-- ---------------------------------------------------------------------
-- QUERY 3 — Does wind clean the air? Mean PM2.5 in central Paris by wind
-- class (weather columns are in the same row as the measurement).
-- ---------------------------------------------------------------------
SELECT
    CASE
        WHEN wind_mean_ms < 2 THEN '1. calm (< 2 m/s)'
        WHEN wind_mean_ms < 4 THEN '2. light (2-4 m/s)'
        WHEN wind_mean_ms < 6 THEN '3. moderate (4-6 m/s)'
        ELSE                       '4. strong (>= 6 m/s)'
    END                                    AS wind_class,
    COUNT(*)                               AS days,
    ROUND(AVG(daily_mean), 1)              AS pm25_mean_ug_m3,
    ROUND(100 * AVG(above_who_pm25), 1)    AS pct_days_above_who
FROM air_quality_paris.daily_measurements
WHERE pollutant_code = 'PM25'
  AND station_code IN ('PA01H', 'PA04C')
  AND wind_mean_ms IS NOT NULL
GROUP BY wind_class
ORDER BY wind_class;


-- ---------------------------------------------------------------------
-- CHECK — the 2018 data are really there (the sandbox deletes old DATE
-- partitions; the integer partition on `year` should avoid it).
-- ---------------------------------------------------------------------
SELECT year, COUNT(*) AS n_rows
FROM air_quality_paris.daily_measurements
GROUP BY year
ORDER BY year;


-- ---------------------------------------------------------------------
-- PUBLIC DATASET CHECK (required by the course: "check public datasets").
-- BigQuery hosts an OpenAQ air-quality dataset. Is it usable for Paris?
-- The answer (latest date, number of rows) justifies in the report why we
-- load our own data instead.
-- ---------------------------------------------------------------------
SELECT
    pollutant,
    COUNT(*)        AS n_rows,
    MIN(timestamp)  AS first_value,
    MAX(timestamp)  AS latest_value
FROM `bigquery-public-data.openaq.global_air_quality`
WHERE country = 'FR' AND LOWER(city) LIKE '%paris%'
GROUP BY pollutant;
