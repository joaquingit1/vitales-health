-- Sensor tables (E23 5.1). Readings are append only (triggers refuse UPDATE and DELETE) and kept for the mission's
-- life. Quantity columns hold Decimal text beside their UCUM unit. Every nullable contract field stays nullable.
CREATE TABLE sensors_channel (
    channel_id TEXT PRIMARY KEY,
    sensor_type_id TEXT NOT NULL,
    hazard_id TEXT NOT NULL,
    environment_ids TEXT NOT NULL,
    location TEXT NOT NULL,
    location_label TEXT,
    adapter_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
    input_unit TEXT NOT NULL,
    expected_interval_seconds INTEGER,
    stale_after_seconds INTEGER NOT NULL CHECK (stale_after_seconds >= 1),
    source_label TEXT NOT NULL,
    dataset TEXT,
    display_note TEXT,
    trusted INTEGER NOT NULL CHECK (trusted IN (0, 1)),
    health_url TEXT,
    registry_version TEXT NOT NULL,
    freshness TEXT NOT NULL CHECK (freshness IN ('current', 'stale', 'never_reported')),
    has_history INTEGER NOT NULL DEFAULT 0 CHECK (has_history IN (0, 1)),
    last_received_at TEXT,
    last_observed_at TEXT,
    window_end TEXT,
    reference_at TEXT,
    reference_state TEXT CHECK (reference_state IS NULL OR reference_state IN ('known', 'unknown', 'future', 'replay')),
    reference_corrected_at TEXT,
    reference_uncertainty_ms INTEGER,
    quiet_anchor_at TEXT,
    quiet_anchor_environment TEXT,
    stale_since TEXT,
    next_stale_check_at TEXT,
    clock_state TEXT NOT NULL DEFAULT 'unknown' CHECK (clock_state IN ('trusted', 'uncertain', 'unknown'))
) STRICT;
CREATE INDEX sensors_channel_type ON sensors_channel (sensor_type_id, channel_id);

CREATE TABLE sensors_reading (
    rev INTEGER PRIMARY KEY AUTOINCREMENT,
    reading_id TEXT NOT NULL UNIQUE,
    channel_id TEXT NOT NULL REFERENCES sensors_channel (channel_id),
    adapter_id TEXT NOT NULL,
    adapter_sequence INTEGER NOT NULL CHECK (adapter_sequence >= 0),
    batch_id TEXT NOT NULL,
    value_text TEXT NOT NULL,
    unit TEXT NOT NULL,
    canonical_text TEXT NOT NULL,
    original_value TEXT NOT NULL,
    original_unit TEXT NOT NULL,
    conversion TEXT,
    mapping_version TEXT NOT NULL,
    aggregation_kind TEXT NOT NULL,
    aggregation_window INTEGER,
    observed_at TEXT NOT NULL,
    window_start TEXT,
    received_at TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
    quality_status TEXT NOT NULL CHECK (quality_status IN ('accepted', 'rejected', 'uncertain', 'not_assessed')),
    time_state TEXT NOT NULL CHECK (time_state IN ('known', 'replay', 'unknown', 'future')),
    corrected_at TEXT,
    uncertainty_ms INTEGER,
    segment_id TEXT,
    source_clock_version INTEGER,
    replay TEXT,
    provenance_trace TEXT,
    instant_conflict INTEGER NOT NULL DEFAULT 0 CHECK (instant_conflict IN (0, 1)),
    body_sha256 TEXT NOT NULL,
    capture_release_id TEXT NOT NULL,
    UNIQUE (channel_id, adapter_sequence)
) STRICT;
CREATE INDEX sensors_reading_channel_time ON sensors_reading (channel_id, observed_at);
CREATE TRIGGER sensors_reading_no_update BEFORE UPDATE ON sensors_reading BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER sensors_reading_no_delete BEFORE DELETE ON sensors_reading BEGIN SELECT RAISE(ABORT, 'append only'); END;

CREATE TABLE sensors_bucket (
    channel_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    config_release_id TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    partial_json TEXT NOT NULL,
    PRIMARY KEY (channel_id, profile_id, config_release_id, bucket_start)
) STRICT;

CREATE TABLE sensors_evaluation (
    evaluation_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    config_release_id TEXT NOT NULL,
    input_rev INTEGER NOT NULL CHECK (input_rev >= 0),
    high_water_at TEXT,
    status TEXT NOT NULL,
    evaluation_json TEXT NOT NULL,
    UNIQUE (channel_id, profile_id, config_release_id)
) STRICT;

CREATE TABLE sensors_evaluation_input (
    input_ref TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
    dataset TEXT,
    partition_ref TEXT NOT NULL,
    high_rev INTEGER NOT NULL CHECK (high_rev >= 1),
    capture_release_id TEXT NOT NULL,
    evaluator_version TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    window_end TEXT,
    correction_map_version INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX sensors_evaluation_input_channel ON sensors_evaluation_input (channel_id, high_rev);
CREATE TRIGGER sensors_evaluation_input_no_update BEFORE UPDATE ON sensors_evaluation_input BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER sensors_evaluation_input_no_delete BEFORE DELETE ON sensors_evaluation_input BEGIN SELECT RAISE(ABORT, 'append only'); END;

CREATE TABLE sensors_status (
    status_key TEXT PRIMARY KEY,
    sensor_type_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    last_emitted_at TEXT,
    pending_emit_at TEXT,
    UNIQUE (sensor_type_id, environment_id)
) STRICT;

CREATE TABLE sensors_batch (
    batch_key TEXT PRIMARY KEY,
    adapter_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    duplicates INTEGER NOT NULL,
    rejected_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (adapter_id, batch_id)
) STRICT;

CREATE TABLE sensors_environment (
    environment_key TEXT PRIMARY KEY CHECK (environment_key = 'current'),
    profile_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    mission_mode TEXT NOT NULL CHECK (mission_mode IN ('LIVE', 'REPLAY', 'SIM')),
    activated_at TEXT NOT NULL
) STRICT;
