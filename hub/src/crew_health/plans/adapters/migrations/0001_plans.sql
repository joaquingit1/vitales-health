CREATE TABLE plans_run (
    plan_run_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision > 0),
    plan_id TEXT NOT NULL,
    plan_version TEXT NOT NULL,
    subject_person_id TEXT NOT NULL,
    episode_id TEXT,
    help_session_id TEXT,
    environment_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
    opened_release_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'watching', 'escalated', 'closed')),
    opened_by_kind TEXT NOT NULL,
    opened_by_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    handoff_id TEXT,
    link_status TEXT NOT NULL CHECK (link_status IN ('up', 'delayed', 'down')),
    link_gap_kind TEXT,
    link_next_contact_at TEXT,
    close_disposition TEXT,
    close_note TEXT,
    close_by_kind TEXT,
    close_by_id TEXT,
    close_at TEXT,
    CHECK ((episode_id IS NULL) <> (help_session_id IS NULL)),
    CHECK ((state = 'closed') = (close_at IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX plans_run_open_unique ON plans_run (
    plan_id, subject_person_id, coalesce(episode_id, help_session_id)
) WHERE state <> 'closed';
CREATE INDEX plans_run_subject_state ON plans_run (subject_person_id, state);

CREATE TABLE plans_run_pin (
    plan_run_id TEXT NOT NULL REFERENCES plans_run(plan_run_id),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    PRIMARY KEY (plan_run_id, name)
) STRICT;

CREATE TABLE plans_run_step (
    plan_run_id TEXT NOT NULL REFERENCES plans_run(plan_run_id),
    step_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    stage_id TEXT NOT NULL,
    substitute_only INTEGER NOT NULL CHECK (substitute_only IN (0, 1)),
    principal_step_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('open', 'done', 'not_available', 'substituted')),
    by_kind TEXT,
    by_id TEXT,
    at TEXT,
    substitute_step_id TEXT,
    guided_check_id TEXT,
    not_available_reason TEXT,
    runs INTEGER NOT NULL DEFAULT 0 CHECK (runs >= 0),
    recorded_revision INTEGER,
    PRIMARY KEY (plan_run_id, step_id)
) STRICT;
CREATE UNIQUE INDEX plans_run_step_guided_check ON plans_run_step (guided_check_id)
    WHERE guided_check_id IS NOT NULL;

CREATE TABLE plans_run_step_observation (
    plan_run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    occurrence INTEGER NOT NULL,
    observation_id TEXT NOT NULL,
    PRIMARY KEY (plan_run_id, step_id, occurrence, observation_id),
    FOREIGN KEY (plan_run_id, step_id) REFERENCES plans_run_step(plan_run_id, step_id)
) STRICT;
CREATE TABLE plans_run_step_supply_use (
    plan_run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    occurrence INTEGER NOT NULL,
    supply_use_id TEXT NOT NULL,
    PRIMARY KEY (plan_run_id, step_id, occurrence, supply_use_id),
    FOREIGN KEY (plan_run_id, step_id) REFERENCES plans_run_step(plan_run_id, step_id)
) STRICT;
CREATE TABLE plans_run_recheck (
    plan_run_id TEXT NOT NULL REFERENCES plans_run(plan_run_id),
    step_id TEXT NOT NULL,
    due_at TEXT,
    done_at TEXT,
    PRIMARY KEY (plan_run_id, step_id)
) STRICT;
-- Design 4.3: every accepted completion of a step, append only; plans_run_step and plans_run_recheck are
-- projections of the latest one. Observations and supply uses keep the same occurrence number.
CREATE TABLE plans_run_recheck_entry (
    plan_run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    occurrence INTEGER NOT NULL CHECK (occurrence >= 1),
    at TEXT NOT NULL,
    by_kind TEXT NOT NULL,
    by_id TEXT,
    guided_check_id TEXT,
    recorded_revision INTEGER NOT NULL CHECK (recorded_revision >= 1),
    PRIMARY KEY (plan_run_id, step_id, occurrence),
    FOREIGN KEY (plan_run_id, step_id) REFERENCES plans_run_step(plan_run_id, step_id)
) STRICT;
CREATE TRIGGER plans_run_recheck_entry_no_update BEFORE UPDATE ON plans_run_recheck_entry
BEGIN SELECT RAISE(ABORT, 'plans_run_recheck_entry is append only'); END;
CREATE TRIGGER plans_run_recheck_entry_no_delete BEFORE DELETE ON plans_run_recheck_entry
BEGIN SELECT RAISE(ABORT, 'plans_run_recheck_entry is append only'); END;
CREATE TABLE plans_run_recheck_observation (
    plan_run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    PRIMARY KEY (plan_run_id, step_id, observation_id),
    FOREIGN KEY (plan_run_id, step_id) REFERENCES plans_run_recheck(plan_run_id, step_id)
) STRICT;
CREATE TABLE plans_run_escalation (
    plan_run_id TEXT NOT NULL REFERENCES plans_run(plan_run_id),
    step_id TEXT NOT NULL,
    help_session_id TEXT NOT NULL,
    at TEXT NOT NULL,
    session_closed INTEGER NOT NULL CHECK (session_closed IN (0, 1)),
    escalation_id TEXT,
    PRIMARY KEY (plan_run_id, help_session_id)
) STRICT;
CREATE TABLE plans_escalation_intent (
    escalation_id TEXT PRIMARY KEY,
    plan_run_id TEXT NOT NULL REFERENCES plans_run(plan_run_id),
    step_id TEXT NOT NULL,
    occurrence INTEGER NOT NULL CHECK (occurrence > 0),
    expected_run_revision INTEGER NOT NULL,
    subject_person_id TEXT NOT NULL,
    flow_id TEXT,
    pinned_release_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
    client_time TEXT NOT NULL,
    session_json TEXT NOT NULL,
    parent_key TEXT NOT NULL,
    parent_actor TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'recorded')),
    help_session_id TEXT,
    UNIQUE (plan_run_id, step_id, occurrence)
) STRICT;
CREATE TABLE plans_parsed_plan (
    plan_version TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (plan_version, plan_id)
) STRICT;

CREATE TABLE plans_run_context (
    plan_run_id TEXT PRIMARY KEY REFERENCES plans_run(plan_run_id) DEFERRABLE INITIALLY DEFERRED,
    context_json TEXT NOT NULL,
    sha256 TEXT NOT NULL
) STRICT;
