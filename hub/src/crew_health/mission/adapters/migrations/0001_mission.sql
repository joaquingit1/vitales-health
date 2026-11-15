-- Mission tables (E23 5.1). Each record is stored whole as JSON beside its indexed columns; the partial unique
-- indexes make one active and one proposed environment, and one active overlay per person and kind.
CREATE TABLE mission_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    mission_id TEXT NOT NULL UNIQUE,
    mission_mode TEXT NOT NULL CHECK (mission_mode IN ('LIVE', 'REPLAY', 'SIM')),
    planned_length_days INTEGER CHECK (planned_length_days IS NULL OR planned_length_days >= 1),
    site_node_id TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE mission_environment (
    profile_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    environment_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('proposed', 'active', 'superseded', 'rejected')),
    reason TEXT NOT NULL CHECK (reason IN ('mission_start', 'phase_plan', 'operator', 'ground', 'demo_switch')),
    effective_at TEXT,
    proposed_at TEXT NOT NULL,
    activated_at TEXT,
    superseded_at TEXT,
    record_json TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX mission_environment_one_active ON mission_environment (state) WHERE state = 'active';
CREATE UNIQUE INDEX mission_environment_one_proposed ON mission_environment (state) WHERE state = 'proposed';
CREATE INDEX mission_environment_order ON mission_environment (proposed_at, profile_id);

CREATE TABLE mission_overlay (
    overlay_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    person_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('planned', 'active', 'ended')),
    started_at TEXT,
    record_json TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX mission_overlay_one_active ON mission_overlay (person_id, kind) WHERE state = 'active';
CREATE INDEX mission_overlay_person ON mission_overlay (person_id, started_at);

CREATE TABLE mission_event (
    event_id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    record_json TEXT NOT NULL
) STRICT;
CREATE INDEX mission_event_order ON mission_event (occurred_at, event_id);
CREATE TRIGGER mission_event_no_update BEFORE UPDATE ON mission_event BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER mission_event_no_delete BEFORE DELETE ON mission_event BEGIN SELECT RAISE(ABORT, 'append only'); END;

CREATE TABLE mission_state_fact (
    fact_key TEXT PRIMARY KEY,
    fact TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    record_json TEXT NOT NULL
) STRICT;
