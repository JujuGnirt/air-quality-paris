-- =====================================================================
-- MySQL DATABASE SCHEMA — Air quality in Paris (2018-2026)
-- =====================================================================
-- Star schema:
--   * one FACT table      : daily_measurement (one daily mean per
--     station and per pollutant);
--   * DIMENSION tables    : station, pollutant, calendar;
--   * two context tables linked to the calendar: weather_day, episode.
--
-- Relationships (foreign keys):
--   daily_measurement -> station    (N:1)  a station has many measurements
--   daily_measurement -> pollutant  (N:1)  a pollutant has many measurements
--   daily_measurement -> calendar   (N:1)  a day has many measurements
--   weather_day       -> calendar   (1:1)  a day has one weather record
--   episode           -> calendar   (N:1)  a day can have several episodes
--   episode           -> pollutant  (N:1)  a pollutant has many episodes
-- => 6 entities, 6 relationships (minimum required: 4 entities, 3 relationships)
--
-- This script is run automatically by src/load_mysql.py.
-- It drops then recreates the tables: it can be run again without
-- creating duplicates.
-- =====================================================================

-- Drop the tables that depend on others first (reverse order)
DROP TABLE IF EXISTS episode;
DROP TABLE IF EXISTS daily_measurement;
DROP TABLE IF EXISTS weather_day;
DROP TABLE IF EXISTS calendar;
DROP TABLE IF EXISTS pollutant;
DROP TABLE IF EXISTS station;


-- ---------------------------------------------------------------------
-- DIMENSION: Airparif monitoring stations
-- ---------------------------------------------------------------------
CREATE TABLE station (
    station_id    INT          NOT NULL,          -- identifier assigned in Python (1, 2, 3...)
    station_code  VARCHAR(10)  NOT NULL,          -- Airparif code, e.g. PA01H
    station_name  VARCHAR(100) NOT NULL,          -- e.g. PARIS 1er Les Halles
    station_type  VARCHAR(20)  NOT NULL,          -- urban_background / traffic / rural / observation
    PRIMARY KEY (station_id),
    UNIQUE KEY uq_station_code (station_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------
-- DIMENSION: pollutants
-- ---------------------------------------------------------------------
CREATE TABLE pollutant (
    pollutant_id    INT         NOT NULL,
    pollutant_code  VARCHAR(10) NOT NULL,         -- e.g. PM25, NO2
    pollutant_name  VARCHAR(50) NOT NULL,         -- e.g. PM2.5 fine particles
    unit            VARCHAR(15) NOT NULL,         -- microg/m3 (mg/m3 for CO)
    PRIMARY KEY (pollutant_id),
    UNIQUE KEY uq_pollutant_code (pollutant_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------
-- DIMENSION: calendar (one row per day, 2018 -> 2026)
-- ---------------------------------------------------------------------
CREATE TABLE calendar (
    date                 DATE        NOT NULL,
    year                 SMALLINT    NOT NULL,
    month                TINYINT     NOT NULL,    -- 1 to 12
    day_of_week          TINYINT     NOT NULL,    -- 1 = Monday ... 7 = Sunday
    is_weekend           TINYINT(1)  NOT NULL,    -- 0 / 1
    is_public_holiday    TINYINT(1)  NULL,        -- NULL if the information was not collected
    holiday_name         VARCHAR(50) NULL,
    is_school_holiday    TINYINT(1)  NULL,        -- school holidays, zone C (Paris)
    school_holiday_name  VARCHAR(60) NULL,
    PRIMARY KEY (date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------
-- CONTEXT: daily weather (Météo-France API, Paris-Montsouris station)
-- ---------------------------------------------------------------------
CREATE TABLE weather_day (
    date               DATE  NOT NULL,
    temp_mean_c        FLOAT NULL,
    temp_min_c         FLOAT NULL,
    temp_max_c         FLOAT NULL,
    humidity_mean_pct  FLOAT NULL,
    pressure_mean_hpa  FLOAT NULL,                -- sea-level pressure
    rain_mm            FLOAT NULL,
    wind_mean_ms       FLOAT NULL,
    wind_max_ms        FLOAT NULL,
    wind_dir_deg       FLOAT NULL,
    PRIMARY KEY (date),
    CONSTRAINT fk_weather_calendar FOREIGN KEY (date) REFERENCES calendar (date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------
-- FACTS: daily means per station and per pollutant
-- ---------------------------------------------------------------------
CREATE TABLE daily_measurement (
    measurement_id  INT      NOT NULL AUTO_INCREMENT,
    date            DATE     NOT NULL,
    station_id      INT      NOT NULL,
    pollutant_id    INT      NOT NULL,
    mean            FLOAT    NOT NULL,            -- daily mean (18-hours-out-of-24 rule)
    max             FLOAT    NULL,                -- highest hourly value of the day
    n_hours         TINYINT  NOT NULL,            -- number of hours measured (18 to 25)
    PRIMARY KEY (measurement_id),
    -- only one measurement per day, station and pollutant
    UNIQUE KEY uq_measurement (date, station_id, pollutant_id),
    -- index to speed up the most frequent filters (station + pollutant)
    KEY idx_station_pollutant (station_id, pollutant_id),
    CONSTRAINT fk_measurement_calendar  FOREIGN KEY (date)         REFERENCES calendar (date),
    CONSTRAINT fk_measurement_station   FOREIGN KEY (station_id)   REFERENCES station (station_id),
    CONSTRAINT fk_measurement_pollutant FOREIGN KEY (pollutant_id) REFERENCES pollutant (pollutant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ---------------------------------------------------------------------
-- CONTEXT: official pollution episodes (Airparif scraping + Open Data)
-- ---------------------------------------------------------------------
CREATE TABLE episode (
    episode_id            INT         NOT NULL AUTO_INCREMENT,
    date                  DATE        NOT NULL,
    pollutant_id          INT         NOT NULL,
    level                 VARCHAR(15) NOT NULL,   -- information / alert
    source                VARCHAR(15) NOT NULL,   -- pdf_report / html_page / opendata_csv
    forecast              TINYINT(1)  NULL,       -- had Airparif forecast the episode?
    observed              TINYINT(1)  NULL,       -- was it observed afterwards?
    forecast_result       VARCHAR(30) NULL,       -- correct_detection / missed_detection / false_alarm
    criterion_population  TINYINT(1)  NULL,
    criterion_area        TINYINT(1)  NULL,
    PRIMARY KEY (episode_id),
    UNIQUE KEY uq_episode (date, pollutant_id),
    CONSTRAINT fk_episode_calendar  FOREIGN KEY (date)         REFERENCES calendar (date),
    CONSTRAINT fk_episode_pollutant FOREIGN KEY (pollutant_id) REFERENCES pollutant (pollutant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
