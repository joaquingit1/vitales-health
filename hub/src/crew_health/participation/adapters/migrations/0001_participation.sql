CREATE TABLE participation_intent (
  intent_seq INTEGER PRIMARY KEY,
  intent_key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind = 'completion'),
  payload TEXT NOT NULL CHECK (json_valid(payload) AND length(payload) <= 2048),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  patient_id TEXT NOT NULL,
  enqueued_at TEXT NOT NULL
) STRICT;

CREATE INDEX participation_intent_patient ON participation_intent (patient_id);

CREATE TRIGGER participation_intent_no_update BEFORE UPDATE ON participation_intent
  BEGIN SELECT RAISE(ABORT, 'participation_intent is append only'); END;

CREATE TRIGGER participation_intent_no_delete BEFORE DELETE ON participation_intent
  BEGIN SELECT RAISE(ABORT, 'participation_intent is append only'); END;

CREATE TABLE participation_intent_conflict (
  conflict_seq INTEGER PRIMARY KEY,
  intent_key TEXT NOT NULL REFERENCES participation_intent (intent_key),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  seen_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER participation_intent_conflict_no_update BEFORE UPDATE ON participation_intent_conflict
  BEGIN SELECT RAISE(ABORT, 'participation_intent_conflict is append only'); END;

CREATE TRIGGER participation_intent_conflict_no_delete BEFORE DELETE ON participation_intent_conflict
  BEGIN SELECT RAISE(ABORT, 'participation_intent_conflict is append only'); END;

CREATE TABLE participation_projection (
  intent_seq INTEGER PRIMARY KEY REFERENCES participation_intent (intent_seq),
  state TEXT NOT NULL CHECK (state IN ('pending', 'applied', 'nothing', 'parked')),
  attempts INTEGER NOT NULL CHECK (attempts >= 0),
  last_error_code TEXT,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX participation_projection_state ON participation_projection (state, intent_seq);

CREATE TABLE participation_completion (
  completion_id TEXT PRIMARY KEY,
  intent_seq INTEGER NOT NULL REFERENCES participation_intent (intent_seq),
  idempotency_key TEXT NOT NULL UNIQUE,
  patient_id TEXT NOT NULL,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'SIM')),
  basis TEXT NOT NULL CHECK (basis IN ('daily_check', 'concern_reported', 'episode_updated')),
  basis_ref TEXT NOT NULL,
  actor_person_id TEXT,
  cause_kind TEXT CHECK (cause_kind IS NULL OR cause_kind IN ('answer', 'urgent_event')),
  cause_ref TEXT,
  occurred_at TEXT NOT NULL,
  original_offset TEXT,
  mission_clock_version TEXT NOT NULL,
  mission_day INTEGER NOT NULL,
  completion_rule_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
  status_reason TEXT CHECK (status_reason IS NULL OR status_reason IN
    ('ORIGIN_MISMATCH', 'IDENTITY_MISMATCH', 'REQUIRED_ANSWER_MISSING')),
  lineage_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 0),
  replaces_completion_id TEXT,
  CHECK ((status = 'rejected') = (status_reason IS NOT NULL))
) STRICT;

CREATE INDEX participation_completion_person
  ON participation_completion (mission_id, origin_mode, patient_id, status, mission_day);

CREATE TRIGGER participation_completion_no_update BEFORE UPDATE ON participation_completion
  BEGIN SELECT RAISE(ABORT, 'participation_completion is append only'); END;

CREATE TRIGGER participation_completion_no_delete BEFORE DELETE ON participation_completion
  BEGIN SELECT RAISE(ABORT, 'participation_completion is append only'); END;

CREATE TABLE participation_award (
  seq INTEGER PRIMARY KEY,
  award_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('earned', 'removed')),
  patient_id TEXT NOT NULL,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'SIM')),
  threshold INTEGER NOT NULL CHECK (threshold >= 1),
  patch_design_id TEXT NOT NULL,
  reached_by_completion_id TEXT NOT NULL,
  reached_on_mission_day INTEGER NOT NULL,
  derivation_version TEXT NOT NULL,
  at TEXT NOT NULL
) STRICT;

CREATE INDEX participation_award_person ON participation_award (patient_id, mission_id, origin_mode, seq);

CREATE TRIGGER participation_award_no_update BEFORE UPDATE ON participation_award
  BEGIN SELECT RAISE(ABORT, 'participation_award is append only'); END;

CREATE TRIGGER participation_award_no_delete BEFORE DELETE ON participation_award
  BEGIN SELECT RAISE(ABORT, 'participation_award is append only'); END;

CREATE TABLE participation_goal_consent (
  seq INTEGER PRIMARY KEY,
  patient_id TEXT NOT NULL,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'SIM')),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  effective_from_day INTEGER,
  effective_at TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  CHECK ((enabled = 1) = (effective_from_day IS NOT NULL)),
  UNIQUE (patient_id, mission_id, origin_mode, revision)
) STRICT;

CREATE TRIGGER participation_goal_consent_no_update BEFORE UPDATE ON participation_goal_consent
  BEGIN SELECT RAISE(ABORT, 'participation_goal_consent is append only'); END;

CREATE TRIGGER participation_goal_consent_no_delete BEFORE DELETE ON participation_goal_consent
  BEGIN SELECT RAISE(ABORT, 'participation_goal_consent is append only'); END;
