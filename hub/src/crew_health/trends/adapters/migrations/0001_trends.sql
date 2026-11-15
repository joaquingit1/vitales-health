-- Experimental trends forecasts (E14; SYSTEM 5.7 transactions and exact quantities).
-- STRICT tables; instants are RFC 3339 UTC text; decimals are text, never REAL.
CREATE TABLE trends_forecast_input (
    input_ref TEXT PRIMARY KEY,
    series_key TEXT NOT NULL,
    person_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE','REPLAY','SIM')),
    code TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    fingerprint TEXT NOT NULL,
    unit TEXT NOT NULL,
    series_json TEXT NOT NULL,
    observation_ids_json TEXT NOT NULL,
    release_id TEXT NOT NULL,
    task_id TEXT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(series_key, generation)
) STRICT;
CREATE TABLE trends_forecast_result (
    input_ref TEXT PRIMARY KEY REFERENCES trends_forecast_input(input_ref),
    task_id TEXT NOT NULL,
    task_generation INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('forecast','unavailable')),
    reason TEXT NULL,
    model_id TEXT NULL,
    model_revision TEXT NULL,
    model_sha256 TEXT NULL,
    policy_version TEXT NULL,
    interval_level TEXT NULL,
    steps_json TEXT NULL,
    created_at TEXT NOT NULL
) STRICT;
