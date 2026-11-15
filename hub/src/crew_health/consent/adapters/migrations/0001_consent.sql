CREATE TABLE consent_grant (
  grant_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  granted_by TEXT NOT NULL,
  subject_person_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  object_ref TEXT NOT NULL,
  purpose TEXT NOT NULL,
  actions TEXT NOT NULL CHECK (json_valid(actions) AND json_array_length(actions) > 0),
  fields TEXT NOT NULL CHECK (json_valid(fields) AND json_array_length(fields) > 0),
  policy_version TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_until TEXT CHECK (valid_until IS NULL OR valid_until > valid_from),
  issued_at TEXT NOT NULL,
  issued_epoch TEXT NOT NULL,
  issued_monotonic_ns INTEGER NOT NULL CHECK (issued_monotonic_ns >= 0),
  schedule_generation INTEGER,
  state TEXT NOT NULL CHECK (state IN ('active', 'revoked', 'expired', 'fenced')),
  ended_at TEXT,
  CHECK ((state = 'active') = (ended_at IS NULL)),
  CHECK ((valid_until IS NULL) = (schedule_generation IS NULL)),
  CHECK (subject_person_id <> recipient)
) STRICT;

CREATE INDEX consent_grant_lookup ON consent_grant (subject_person_id, recipient, purpose, object_ref, state);

CREATE INDEX consent_grant_finite ON consent_grant (state, valid_until, issued_epoch);

CREATE TRIGGER consent_grant_terminal BEFORE UPDATE ON consent_grant
  WHEN OLD.state <> 'active'
  BEGIN SELECT RAISE(ABORT, 'consent_grant end is terminal'); END;

CREATE TRIGGER consent_grant_fixed BEFORE UPDATE OF
  grant_id, granted_by, subject_person_id, recipient, mission_id, origin_mode, object_ref, purpose, actions, fields,
  policy_version, valid_from, valid_until, issued_at, issued_epoch, issued_monotonic_ns, schedule_generation
  ON consent_grant
  BEGIN SELECT RAISE(ABORT, 'consent_grant terms are fixed'); END;

CREATE TRIGGER consent_grant_revision_forward BEFORE UPDATE ON consent_grant
  WHEN NEW.revision <> OLD.revision + 1
  BEGIN SELECT RAISE(ABORT, 'consent_grant revision moves by one'); END;

CREATE TRIGGER consent_grant_retained BEFORE DELETE ON consent_grant
  BEGIN SELECT RAISE(ABORT, 'consent_grant rows are retained'); END;
