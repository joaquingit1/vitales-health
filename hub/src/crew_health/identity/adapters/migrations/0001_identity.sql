CREATE TABLE identity_roster (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  mission_id TEXT NOT NULL,
  namespace TEXT NOT NULL CHECK (namespace IN ('LIVE', 'REPLAY', 'SIM')),
  revision INTEGER NOT NULL CHECK (revision >= 1)
) STRICT;

CREATE TABLE identity_person (
  person_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  namespace TEXT NOT NULL CHECK (namespace IN ('LIVE', 'REPLAY', 'SIM')),
  display_name TEXT NOT NULL CHECK (length(display_name) > 0),
  roster_index INTEGER NOT NULL CHECK (roster_index >= 0),
  from_day INTEGER NOT NULL,
  until_day INTEGER CHECK (until_day IS NULL OR until_day >= from_day),
  medical_officer_rank INTEGER CHECK (medical_officer_rank IS NULL OR medical_officer_rank >= 1),
  removed_at TEXT,
  revision INTEGER NOT NULL CHECK (revision >= 1)
) STRICT;

CREATE INDEX identity_person_order ON identity_person (removed_at, roster_index);

CREATE UNIQUE INDEX identity_person_rank ON identity_person (mission_id, medical_officer_rank)
  WHERE medical_officer_rank IS NOT NULL AND removed_at IS NULL;

CREATE TRIGGER identity_person_removed_final BEFORE UPDATE ON identity_person
  WHEN OLD.removed_at IS NOT NULL
  BEGIN SELECT RAISE(ABORT, 'identity_person removal is final'); END;

CREATE TRIGGER identity_person_retained BEFORE DELETE ON identity_person
  BEGIN SELECT RAISE(ABORT, 'identity_person rows are retained'); END;

CREATE TABLE identity_credential (
  principal_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('crew', 'station', 'ground', 'operator', 'editor', 'adapter')),
  person_id TEXT REFERENCES identity_person (person_id),
  mission_id TEXT NOT NULL,
  display_name TEXT NOT NULL CHECK (length(display_name) > 0),
  verifier TEXT,
  generation INTEGER NOT NULL CHECK (generation >= 1),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  CHECK ((kind = 'crew') = (person_id IS NOT NULL)),
  CHECK (kind = 'crew' OR person_id IS NULL),
  CHECK (kind <> 'adapter' OR verifier IS NULL)
) STRICT;

CREATE UNIQUE INDEX identity_credential_single_kind ON identity_credential (kind)
  WHERE kind IN ('station', 'ground', 'operator', 'editor') AND enabled = 1;

CREATE UNIQUE INDEX identity_credential_person ON identity_credential (person_id)
  WHERE person_id IS NOT NULL;

CREATE TRIGGER identity_credential_kind_fixed BEFORE UPDATE ON identity_credential
  WHEN NEW.kind IS NOT OLD.kind OR NEW.person_id IS NOT OLD.person_id
  BEGIN SELECT RAISE(ABORT, 'identity_credential kind is fixed'); END;

CREATE TRIGGER identity_credential_generation_forward BEFORE UPDATE ON identity_credential
  WHEN NEW.generation < OLD.generation
  BEGIN SELECT RAISE(ABORT, 'identity_credential generation only grows'); END;

CREATE TABLE identity_session (
  session_id TEXT PRIMARY KEY,
  token_digest TEXT NOT NULL UNIQUE CHECK (length(token_digest) = 64),
  principal_id TEXT NOT NULL REFERENCES identity_credential (principal_id),
  kind TEXT NOT NULL CHECK (kind IN ('crew', 'station', 'ground', 'operator', 'editor', 'adapter')),
  person_id TEXT,
  mission_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 1),
  listener TEXT NOT NULL,
  process_epoch TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_monotonic_ns INTEGER NOT NULL CHECK (created_monotonic_ns >= 0),
  expires_at TEXT NOT NULL CHECK (expires_at > created_at),
  ended_at TEXT,
  end_reason TEXT CHECK (
    end_reason IS NULL
    OR end_reason IN ('signed_out', 'replaced', 'credential_changed', 'member_removed', 'restarted', 'revoked')
  ),
  CHECK ((ended_at IS NULL) = (end_reason IS NULL)),
  CHECK ((kind = 'crew') = (person_id IS NOT NULL))
) STRICT;

CREATE INDEX identity_session_principal ON identity_session (principal_id, ended_at, created_at);

CREATE INDEX identity_session_open ON identity_session (ended_at, expires_at);

CREATE TRIGGER identity_session_terminal BEFORE UPDATE ON identity_session
  WHEN OLD.ended_at IS NOT NULL
  BEGIN SELECT RAISE(ABORT, 'identity_session end is terminal'); END;

CREATE TRIGGER identity_session_fixed BEFORE UPDATE ON identity_session
  WHEN NEW.session_id IS NOT OLD.session_id OR NEW.token_digest IS NOT OLD.token_digest OR
    NEW.principal_id IS NOT OLD.principal_id OR NEW.kind IS NOT OLD.kind OR
    NEW.person_id IS NOT OLD.person_id OR NEW.mission_id IS NOT OLD.mission_id OR
    NEW.generation IS NOT OLD.generation OR NEW.listener IS NOT OLD.listener OR
    NEW.process_epoch IS NOT OLD.process_epoch OR NEW.created_at IS NOT OLD.created_at OR
    NEW.created_monotonic_ns IS NOT OLD.created_monotonic_ns OR NEW.expires_at IS NOT OLD.expires_at
  BEGIN SELECT RAISE(ABORT, 'identity_session facts are fixed'); END;

CREATE TRIGGER identity_session_retained BEFORE DELETE ON identity_session
  BEGIN SELECT RAISE(ABORT, 'identity_session rows are retained'); END;

CREATE TABLE identity_auth_bucket (
  scope TEXT NOT NULL CHECK (scope IN ('principal', 'client', 'global')),
  bucket_key TEXT NOT NULL CHECK (length(bucket_key) > 0),
  anchor_ns INTEGER NOT NULL CHECK (anchor_ns >= 0),
  process_epoch TEXT NOT NULL,
  attempts INTEGER NOT NULL CHECK (attempts >= 0),
  PRIMARY KEY (scope, bucket_key)
) STRICT;

CREATE INDEX identity_auth_bucket_age ON identity_auth_bucket (anchor_ns);

CREATE TABLE identity_access_generation (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  generation INTEGER NOT NULL CHECK (generation >= 0)
) STRICT;

CREATE TRIGGER identity_access_generation_forward BEFORE UPDATE ON identity_access_generation
  WHEN NEW.generation <= OLD.generation
  BEGIN SELECT RAISE(ABORT, 'identity_access_generation only grows'); END;

INSERT OR IGNORE INTO identity_access_generation VALUES (1, 0);
