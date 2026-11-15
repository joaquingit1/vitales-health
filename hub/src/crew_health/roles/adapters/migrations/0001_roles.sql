-- E31 roles module (SYSTEM 4.1, 4.2). Additive; rollback drops only these tables.
-- No column holds a measurement, a rate, a beat count, a duration score or a rank (EI10, I-D4).

CREATE TABLE IF NOT EXISTS roles_assignment (
    role_assignment_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    help_session_id TEXT,
    plan_run_id TEXT,
    role_id TEXT NOT NULL,
    label TEXT NOT NULL CHECK (length(label) > 0),
    person_id TEXT NOT NULL,
    person_display_name TEXT NOT NULL,
    skill_id TEXT,
    task_text TEXT,
    reason_code TEXT NOT NULL,
    reason_text TEXT NOT NULL CHECK (length(reason_text) > 0),
    context_reasons TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('proposed', 'accepted', 'changed')),
    proposed_at TEXT NOT NULL,
    accepted_at TEXT,
    changed_by_kind TEXT,
    changed_by_id TEXT,
    changed_by_name TEXT,
    changed_at TEXT,
    replaced_by_id TEXT,
    retired_at TEXT,
    retired_reason TEXT CHECK (retired_reason IS NULL OR retired_reason IN
        ('ineligible_subject', 'ineligible_eva', 'need_changed', 'not_on_roster', 'no_candidate', 'degraded')),
    acting_since TEXT,
    acting_ended_at TEXT,
    proposal_seq INTEGER NOT NULL,
    config_release_id TEXT NOT NULL,
    CHECK ((help_session_id IS NULL) <> (plan_run_id IS NULL))
) STRICT;

CREATE INDEX IF NOT EXISTS ix_roles_current_help ON roles_assignment (help_session_id, role_id)
    WHERE replaced_by_id IS NULL AND retired_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_roles_current_plan ON roles_assignment (plan_run_id, skill_id)
    WHERE replaced_by_id IS NULL AND retired_at IS NULL AND plan_run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_roles_person_current ON roles_assignment (person_id, role_id)
    WHERE replaced_by_id IS NULL AND retired_at IS NULL;

CREATE TABLE IF NOT EXISTS roles_context (
    context_kind TEXT NOT NULL CHECK (context_kind IN ('help_session', 'plan_run')),
    context_id TEXT NOT NULL,
    proposal_seq INTEGER NOT NULL CHECK (proposal_seq >= 0),
    subject_person_id TEXT,
    open INTEGER NOT NULL CHECK (open IN (0, 1)),
    as_of TEXT,
    degraded_at TEXT,
    revision INTEGER CHECK (revision IS NULL OR revision >= 1), -- the HelpSession or PlanRun revision last applied
    next_check_at TEXT, -- earliest stale_at among the holders a proposal used (freshness timer, design 3.5)
    PRIMARY KEY (context_kind, context_id)
) STRICT;

CREATE TABLE IF NOT EXISTS roles_unfilled (
    context_kind TEXT NOT NULL,
    context_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    role_id TEXT NOT NULL,
    label TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    PRIMARY KEY (context_kind, context_id, position)
) STRICT;

CREATE TABLE IF NOT EXISTS roles_recompute (
    context_kind TEXT NOT NULL CHECK (context_kind IN ('help_session', 'plan_run')),
    context_id TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    first_requested_at TEXT NOT NULL, -- the first unmet request: overdue is measured from it (design 5.2)
    version INTEGER NOT NULL CHECK (version >= 1), -- bumped by every request; a pass finishes only what it read
    reason TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    PRIMARY KEY (context_kind, context_id)
) STRICT;

-- SYSTEM 4.2 names this table roles_skill_record: it holds the attributable practices a SkillRecord is computed from.
CREATE TABLE IF NOT EXISTS roles_skill_record (
    practice_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('preflight', 'drill', 'event')),
    source_ref_id TEXT NOT NULL,
    practiced_at TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'SIM')),
    mission_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    config_release_id TEXT NOT NULL,
    UNIQUE (source, source_ref_id, person_id, skill_id)
) STRICT;

CREATE INDEX IF NOT EXISTS ix_roles_practice_lookup
    ON roles_skill_record (person_id, skill_id, mission_id, origin_mode, practiced_at DESC);

CREATE TABLE IF NOT EXISTS roles_drill (
    drill_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    scenario_id TEXT NOT NULL,
    skill_ids TEXT NOT NULL,
    routine_occurrence_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('running', 'completed', 'abandoned')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    abandoned_at TEXT,
    day_end TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'SIM')),
    mission_id TEXT NOT NULL,
    started_by TEXT NOT NULL,
    completed_by TEXT,
    environment_id TEXT NOT NULL,
    config_release_id TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS ix_roles_drill_state ON roles_drill (state, started_at);

CREATE TABLE IF NOT EXISTS roles_drill_participant (
    drill_id TEXT NOT NULL REFERENCES roles_drill (drill_id),
    person_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    completed INTEGER NOT NULL CHECK (completed IN (0, 1)),
    core_fresh_at_completion INTEGER NOT NULL CHECK (core_fresh_at_completion IN (0, 1)),
    completed_at TEXT,
    mission_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'SIM')),
    PRIMARY KEY (drill_id, person_id)
) STRICT;

CREATE INDEX IF NOT EXISTS ix_roles_participation
    ON roles_drill_participant (person_id, mission_id, origin_mode, completed, completed_at, drill_id);
