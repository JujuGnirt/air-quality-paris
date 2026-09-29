-- =====================================================================
-- 5 ANALYSIS QUERIES — Air quality in Paris
-- =====================================================================
-- Requires MySQL 8 (queries 3 and 5: WITH and window functions).
-- "Central Paris" = station PA04C (until September 2019) then PA01H:
-- the two never measured on the same day, so there is at most one value
-- per day.
-- Note: `date`, `year`, `month`, `mean` and `max` are column names here;
-- MySQL accepts them, and the table alias (m.mean) removes any ambiguity.
--
-- OPTIMISATIONS APPLIED (common to the 5 queries)
-- 1. Indexes created by schema.sql on the fact table (386,561 rows):
--    - PRIMARY KEY (measurement_id);
--    - UNIQUE KEY (date, station_id, pollutant_id): no duplicate measurement,
--      and fast joins / filters on the date;
--    - KEY idx_station_pollutant (station_id, pollutant_id): the filters
--      "this station + this pollutant" read only the matching rows instead
--      of scanning the whole table.
-- 2. Filters on codes ('PM25', 'PA01H') are applied to the small dimension
--    tables (52 stations, 6 pollutants); MySQL then uses the integer keys
--    and the index above to reach the fact table.
-- 3. Only the needed columns are selected (never SELECT *).
-- 4. Filters are applied BEFORE grouping, sorting or window functions (in the
--    WHERE clause or inside the first CTE), so the heavy steps work on a few
--    thousand rows instead of 386,561.
-- To check how MySQL runs a query: put EXPLAIN in front of the SELECT.
-- =====================================================================


-- ---------------------------------------------------------------------
-- QUERY 1 — Is fine-particle pollution going down?
-- Yearly mean of PM2.5 per station type.
-- Techniques: joins between 4 tables, GROUP BY on two criteria.
-- Caution: the number of stations varies over the years; the column
-- n_stations shows it, to interpret the means carefully.
-- Optimisation: the PM25 and station-type filters are in WHERE, so rows
-- are discarded before the GROUP BY; COUNT(DISTINCT) only on kept rows.
-- ---------------------------------------------------------------------
SELECT
    c.year,
    s.station_type,
    ROUND(AVG(m.mean), 1)            AS pm25_mean_ug_m3,
    COUNT(DISTINCT m.station_id)     AS n_stations
FROM daily_measurement m
JOIN station   s ON s.station_id   = m.station_id
JOIN pollutant p ON p.pollutant_id = m.pollutant_id
JOIN calendar  c ON c.date         = m.date
WHERE p.pollutant_code = 'PM25'
  AND s.station_type IN ('urban_background', 'traffic')
GROUP BY c.year, s.station_type
ORDER BY c.year, s.station_type;


-- ---------------------------------------------------------------------
-- QUERY 2 — Which months are the most at risk?
-- Share of days above the WHO guideline (15 µg/m³) in central Paris,
-- per month, all years together.
-- Techniques: conditional aggregation (in MySQL a comparison is worth
-- 1 or 0, so SUM counts the days and AVG gives a proportion).
-- Optimisation: one pass over the data computes the count, the number of
-- days above 15 and the mean together (no sub-query per month); the
-- filter on 2 stations + PM25 uses idx_station_pollutant (~3,000 rows).
-- ---------------------------------------------------------------------
SELECT
    c.month,
    COUNT(*)                                AS days_measured,
    SUM(m.mean > 15)                        AS days_above_who,
    ROUND(100 * AVG(m.mean > 15), 1)        AS pct_days_above_who,
    ROUND(AVG(m.mean), 1)                   AS pm25_mean_ug_m3
FROM daily_measurement m
JOIN station   s ON s.station_id   = m.station_id
JOIN pollutant p ON p.pollutant_id = m.pollutant_id
JOIN calendar  c ON c.date         = m.date
WHERE p.pollutant_code = 'PM25'
  AND s.station_code IN ('PA01H', 'PA04C')
GROUP BY c.month
ORDER BY c.month;


-- ---------------------------------------------------------------------
-- QUERY 3 — How much pollution does traffic add?
-- NO2 (traffic tracer): roadside stations versus background stations,
-- per year, with the gap in µg/m³ and the traffic / background ratio.
-- Techniques: CTE (WITH), then a "pivot" with CASE WHEN to put the two
-- station types side by side.
-- Optimisation: the first CTE filters NO2 and aggregates to one row per
-- year and type (about 18 rows); the pivot then works on these few rows
-- instead of joining the fact table twice (once per station type).
-- ---------------------------------------------------------------------
WITH no2_by_type AS (
    SELECT
        c.year,
        s.station_type,
        AVG(m.mean) AS no2_mean
    FROM daily_measurement m
    JOIN station   s ON s.station_id   = m.station_id
    JOIN pollutant p ON p.pollutant_id = m.pollutant_id
    JOIN calendar  c ON c.date         = m.date
    WHERE p.pollutant_code = 'NO2'
      AND s.station_type IN ('urban_background', 'traffic')
    GROUP BY c.year, s.station_type
),
side_by_side AS (
    SELECT
        year,
        MAX(CASE WHEN station_type = 'urban_background' THEN no2_mean END) AS no2_background,
        MAX(CASE WHEN station_type = 'traffic'          THEN no2_mean END) AS no2_traffic
    FROM no2_by_type
    GROUP BY year
)
SELECT
    year,
    ROUND(no2_background, 1)                 AS no2_background_ug_m3,
    ROUND(no2_traffic, 1)                    AS no2_traffic_ug_m3,
    ROUND(no2_traffic - no2_background, 1)   AS traffic_excess_ug_m3,
    ROUND(no2_traffic / no2_background, 2)   AS traffic_background_ratio
FROM side_by_side
ORDER BY year;


-- ---------------------------------------------------------------------
-- QUERY 4 — What weather comes with the worst days?
-- The 10 most polluted days (PM2.5) in central Paris, with the weather
-- of the day.
-- Techniques: LEFT JOIN (keep the day even if the weather is missing),
-- ORDER BY + LIMIT.
-- Expected: cold, weak wind, little rain, high pressure (anticyclone).
-- Optimisation: the sort only concerns the ~3,000 central-Paris PM2.5 days
-- (filtered first), and weather_day is joined on its primary key (date).
-- ---------------------------------------------------------------------
SELECT
    m.date,
    ROUND(m.mean, 1)        AS pm25_ug_m3,
    w.temp_mean_c,
    w.wind_mean_ms,
    w.rain_mm,
    w.pressure_mean_hpa
FROM daily_measurement m
JOIN station   s ON s.station_id   = m.station_id
JOIN pollutant p ON p.pollutant_id = m.pollutant_id
LEFT JOIN weather_day w ON w.date  = m.date
WHERE p.pollutant_code = 'PM25'
  AND s.station_code IN ('PA01H', 'PA04C')
ORDER BY m.mean DESC
LIMIT 10;


-- ---------------------------------------------------------------------
-- QUERY 5 — Do official episodes show up in our measurements?
-- For each official episode (Airparif scraping + Open Data), PM2.5 in
-- central Paris on the day itself and the day before, and the quality
-- of the Airparif forecast.
-- Techniques: window function LAG() (value of the previous row), CTE,
-- LEFT JOIN.
-- Caution: LAG takes the previous row; if a measurement day is missing,
-- "the day before" is then the last measured day.
-- Optimisation: the CTE keeps only central-Paris PM2.5 rows BEFORE the
-- window function, so LAG() orders ~3,000 rows instead of 386,561; the
-- 107 episodes are then joined on the date.
-- ---------------------------------------------------------------------
WITH pm25_centre AS (
    SELECT
        m.date,
        m.mean                                   AS pm25_day,
        LAG(m.mean) OVER (ORDER BY m.date)       AS pm25_day_before
    FROM daily_measurement m
    JOIN station   s ON s.station_id   = m.station_id
    JOIN pollutant p ON p.pollutant_id = m.pollutant_id
    WHERE p.pollutant_code = 'PM25'
      AND s.station_code IN ('PA01H', 'PA04C')
)
SELECT
    e.date,
    p.pollutant_code                  AS episode_pollutant,
    e.level,
    e.forecast_result,
    ROUND(x.pm25_day_before, 1)       AS pm25_day_before_ug_m3,
    ROUND(x.pm25_day, 1)              AS pm25_day_ug_m3,
    x.pm25_day > 15                   AS above_who
FROM episode e
JOIN pollutant p ON p.pollutant_id = e.pollutant_id
LEFT JOIN pm25_centre x ON x.date  = e.date
ORDER BY e.date;
