-- Teach and check (E30 5.1; SYSTEM 4.2 tables teach_guided_check, teach_attempt, teach_beat, teach_sample).
-- STRICT tables; instants are RFC 3339 UTC text; exact ratios are n/d text so 60/7 is never lost.

CREATE TABLE IF NOT EXISTS teach_guided_check (
    guided_check_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    skill_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    environment_id TEXT,
    helper_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    measured_record_id TEXT NOT NULL,
    count_origin TEXT NOT NULL CHECK (count_origin IN ('LIVE', 'REPLAY', 'SIM')),
    plan_run_id TEXT,
    plan_step_id TEXT,
    episode_id TEXT,
    role_assignment_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('teaching', 'counting', 'checked', 'retry', 'cancelled')),
    teach_step_no INTEGER NOT NULL CHECK (teach_step_no >= 1),
    steps_done_json TEXT NOT NULL,
    current_attempt_no INTEGER,
    helper_answer TEXT CHECK (helper_answer IS NULL OR helper_answer IN ('regular', 'irregular', 'not_sure')),
    answer_id TEXT,
    previous_answer_id TEXT,
    camera_json TEXT,
    result TEXT CHECK (result IS NULL OR result IN ('agree', 'disagree', 'no_reference')),
    difference_exact TEXT,
    count_exact TEXT,
    result_text TEXT,
    observation_id TEXT,
    experimental INTEGER NOT NULL CHECK (experimental IN (0, 1)),
    pins_json TEXT NOT NULL,
    config_release_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by_session TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS teach_guided_check_active
    ON teach_guided_check (subject_id, skill_id, state);

CREATE TABLE IF NOT EXISTS teach_attempt (
    guided_check_id TEXT NOT NULL REFERENCES teach_guided_check (guided_check_id),
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    started_utc TEXT NOT NULL,
    started_elapsed_ns INTEGER NOT NULL,
    generation TEXT NOT NULL,
    count_seconds INTEGER NOT NULL CHECK (count_seconds >= 1),
    opened_client_time TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'incomplete', 'finished', 'abandoned')),
    flags_json TEXT NOT NULL,
    camera_ticket_id TEXT,
    camera_unavailable_reason TEXT,
    count_exact TEXT,
    result TEXT CHECK (result IS NULL OR result IN ('agree', 'disagree', 'no_reference')),
    difference_exact TEXT,
    observation_id TEXT,
    finished_at TEXT,
    PRIMARY KEY (guided_check_id, attempt_no)
) STRICT;

CREATE TABLE IF NOT EXISTS teach_beat (
    guided_check_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    elapsed_ns INTEGER NOT NULL CHECK (elapsed_ns >= 0),
    at_utc TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    PRIMARY KEY (guided_check_id, attempt_no, sequence),
    FOREIGN KEY (guided_check_id, attempt_no) REFERENCES teach_attempt (guided_check_id, attempt_no)
) STRICT;

CREATE TABLE IF NOT EXISTS teach_sample (
    sample_id TEXT PRIMARY KEY,
    help_session_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    at TEXT NOT NULL,
    rate_per_min TEXT,
    reason TEXT CHECK (reason IS NULL OR reason IN ('cannot_see_hands', 'quality_gate_failed')),
    in_band INTEGER CHECK (in_band IS NULL OR in_band IN (0, 1)),
    direction TEXT CHECK (direction IS NULL OR direction IN ('too_slow', 'too_fast')),
    text TEXT NOT NULL,
    experimental INTEGER NOT NULL CHECK (experimental IN (0, 1)),
    config_release_id TEXT NOT NULL,
    CHECK ((rate_per_min IS NULL) <> (reason IS NULL))
) STRICT;

CREATE INDEX IF NOT EXISTS teach_sample_page ON teach_sample (help_session_id, at, sample_id);
