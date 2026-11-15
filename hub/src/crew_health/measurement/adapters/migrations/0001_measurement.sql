-- E05 measurement: additive schema (E05 5). Expands only measurement_* tables; never touches another module.
-- Times are UTC RFC 3339 text with Z. Quantities are decimal text plus UCUM, never REAL. Lists are JSON text.

CREATE TABLE IF NOT EXISTS measurement_request (
    request_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    episode_id TEXT,
    capability_code TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (length(reason) >= 1),
    decision_affected TEXT NOT NULL CHECK (length(decision_affected) >= 1),
    kit_check_json TEXT NOT NULL,
    requester_kind TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    requester_display_name TEXT,
    patient_id TEXT NOT NULL,
    operator_id TEXT,
    consent_ref TEXT,
    state TEXT NOT NULL CHECK (state IN ('proposed', 'awaiting_acceptance', 'accepted', 'in_progress',
        'completed', 'unavailable', 'declined', 'cancelled', 'failed')),
    result_observation_ids_json TEXT NOT NULL,
    body_region TEXT,
    created_at TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
    mission_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    operator_requirement TEXT NOT NULL CHECK (operator_requirement IN ('self', 'crewmate',
        'crew_medical_officer')),
    methods_json TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    unavailable_cause TEXT CHECK (unavailable_cause IS NULL OR unavailable_cause IN ('no_capability',
        'device_absent', 'operator_absent', 'context_inapplicable')),
    created_by TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS measurement_request_patient
    ON measurement_request (patient_id, mission_id, origin_mode, state, request_id);
CREATE INDEX IF NOT EXISTS measurement_request_episode ON measurement_request (episode_id, request_id);

CREATE TABLE IF NOT EXISTS measurement_attempt (
    attempt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES measurement_request (request_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    adapter_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed', 'rejected')),
    failure_reason TEXT,
    observation_ids_json TEXT NOT NULL,
    quality_notes_json TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('usable', 'not_usable', 'quarantined')),
    held_values_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    request_revision INTEGER NOT NULL CHECK (request_revision >= 2),
    UNIQUE (request_id, sequence)
) STRICT;

CREATE TABLE IF NOT EXISTS measurement_gap (
    gap_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    capability_code TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('no_capability', 'device_absent', 'operator_absent',
        'context_inapplicable')),
    consequence TEXT NOT NULL CHECK (length(consequence) >= 1),
    alternatives_json TEXT NOT NULL,
    body_region TEXT,
    state TEXT NOT NULL CHECK (state IN ('open', 'addressed')),
    plan_id TEXT,
    check_another_way_step_ids_json TEXT NOT NULL,
    source_request_id TEXT UNIQUE REFERENCES measurement_request (request_id),
    release_id TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS measurement_gap_episode ON measurement_gap (episode_id, state, gap_id);

CREATE TABLE IF NOT EXISTS measurement_transition (
    request_id TEXT NOT NULL REFERENCES measurement_request (request_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    cause TEXT NOT NULL,
    attempt_id TEXT,
    at TEXT NOT NULL,
    operator_id TEXT,
    consent_ref TEXT,
    result_observation_ids_json TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    unavailable_cause TEXT,
    PRIMARY KEY (request_id, revision)
) STRICT;

CREATE TABLE IF NOT EXISTS measurement_acquisition (
    request_id TEXT NOT NULL REFERENCES measurement_request (request_id),
    generation INTEGER NOT NULL CHECK (generation >= 1),
    started_at TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    method TEXT NOT NULL,
    device_id TEXT,
    fenced_at TEXT,
    PRIMARY KEY (request_id, generation)
) STRICT;
