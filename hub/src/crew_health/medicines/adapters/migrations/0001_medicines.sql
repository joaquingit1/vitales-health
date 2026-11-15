-- Medicines aboard (E32 5.2), simplified to one append-only record table plus the rows that carry invariants:
-- one terminal event per proposal, one movement per administration (INV-M5), one correction per administration.
-- Decimals are canonical text written by the domain codec; instants are RFC 3339 UTC text.
CREATE TABLE med_record (
  record_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('inspection', 'assay', 'context', 'diagnosis', 'proposal', 'administration')),
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  subject_person_id TEXT,
  lot_id TEXT,
  environment_id TEXT,
  at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL,
  command_key TEXT UNIQUE,
  body_json TEXT NOT NULL
) STRICT;
CREATE INDEX med_record_subject ON med_record (mission_id, origin_mode, kind, subject_person_id, at_utc);
CREATE INDEX med_record_lot ON med_record (mission_id, origin_mode, kind, lot_id, at_utc);
CREATE TRIGGER med_record_no_update BEFORE UPDATE ON med_record BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_record_no_delete BEFORE DELETE ON med_record BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE med_proposal_terminal (
  proposal_id TEXT PRIMARY KEY REFERENCES med_record (record_id),
  state TEXT NOT NULL CHECK (state IN ('confirmed', 'expired', 'superseded')),
  at_utc TEXT NOT NULL,
  reason TEXT NOT NULL
) STRICT;
CREATE TRIGGER med_terminal_no_update BEFORE UPDATE ON med_proposal_terminal BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_terminal_no_delete BEFORE DELETE ON med_proposal_terminal BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE med_movement (
  movement_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  environment_id TEXT NOT NULL,
  lot_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('administration', 'return', 'waste', 'count_correction')),
  delta_decimal TEXT NOT NULL,
  unit_ucum TEXT NOT NULL,
  administration_id TEXT UNIQUE REFERENCES med_record (record_id),
  at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL,
  command_key TEXT UNIQUE,
  CHECK ((kind = 'administration') = (administration_id IS NOT NULL))
) STRICT;
CREATE INDEX med_movement_lot ON med_movement (mission_id, origin_mode, environment_id, lot_id);
CREATE TRIGGER med_movement_no_update BEFORE UPDATE ON med_movement BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_movement_no_delete BEFORE DELETE ON med_movement BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE med_admin_ingredient (
  administration_id TEXT NOT NULL REFERENCES med_record (record_id),
  ingredient_id TEXT NOT NULL,
  accounting_decimal TEXT NOT NULL,
  unit_ucum TEXT NOT NULL,
  PRIMARY KEY (administration_id, ingredient_id)
) STRICT;
CREATE TRIGGER med_admin_ingredient_no_update BEFORE UPDATE ON med_admin_ingredient BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_admin_ingredient_no_delete BEFORE DELETE ON med_admin_ingredient BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE med_correction (
  administration_id TEXT PRIMARY KEY REFERENCES med_record (record_id),
  reason_text TEXT NOT NULL,
  by_id TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  command_key TEXT UNIQUE
) STRICT;
CREATE TRIGGER med_correction_no_update BEFORE UPDATE ON med_correction BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_correction_no_delete BEFORE DELETE ON med_correction BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Artifacts (E32 5.2): content-addressed bytes of pharmacy bundle files, bundle manifests, engine inputs and results.
-- Pruning is the only delete, and only of a hash recorded in med_artifact_prune first.
CREATE TABLE med_artifact (
  sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
  kind TEXT NOT NULL CHECK (kind IN ('bundle_file', 'bundle_manifest', 'inputs', 'result')),
  bytes BLOB NOT NULL,
  size_bytes INTEGER NOT NULL CHECK (size_bytes = length(bytes)),
  at_utc TEXT NOT NULL
) STRICT;
CREATE TABLE med_artifact_prune (
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  run_id TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  PRIMARY KEY (sha256, run_id)
) STRICT;
CREATE TRIGGER med_artifact_no_update BEFORE UPDATE ON med_artifact BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_artifact_delete_only_pruned BEFORE DELETE ON med_artifact
WHEN NOT EXISTS (SELECT 1 FROM med_artifact_prune WHERE sha256 = OLD.sha256)
BEGIN SELECT RAISE(ABORT, 'only a recorded prune deletes an artifact'); END;
CREATE TRIGGER med_artifact_prune_no_update BEFORE UPDATE ON med_artifact_prune BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_artifact_prune_no_delete BEFORE DELETE ON med_artifact_prune BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Pharmacy bundles: the manifest (file name to file SHA-256) is itself an artifact and its hash is the bundle hash.
CREATE TABLE med_bundle (
  bundle_sha256 TEXT PRIMARY KEY REFERENCES med_artifact (sha256),
  imported_at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL
) STRICT;
CREATE TABLE med_bundle_activation (
  seq INTEGER PRIMARY KEY,
  bundle_sha256 TEXT NOT NULL REFERENCES med_bundle (bundle_sha256),
  at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL
) STRICT;
CREATE TRIGGER med_bundle_no_update BEFORE UPDATE ON med_bundle BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_bundle_no_delete BEFORE DELETE ON med_bundle BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_bundle_activation_no_update BEFORE UPDATE ON med_bundle_activation BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_bundle_activation_no_delete BEFORE DELETE ON med_bundle_activation BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Assessment runs: scheduled (queued) with their trigger, started with the bundle and inputs they ran on; status is
-- the last event, one terminal event per run.
CREATE TABLE med_assessment_run (
  run_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  trigger TEXT NOT NULL,
  task_id TEXT NOT NULL UNIQUE,
  process_epoch TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL,
  command_key TEXT UNIQUE
) STRICT;
CREATE INDEX med_assessment_run_partition ON med_assessment_run (mission_id, origin_mode, at_utc);
CREATE TABLE med_assessment_start (
  run_id TEXT PRIMARY KEY REFERENCES med_assessment_run (run_id),
  as_of_utc TEXT NOT NULL,
  bundle_sha256 TEXT NOT NULL REFERENCES med_bundle (bundle_sha256),
  inputs_sha256 TEXT NOT NULL CHECK (length(inputs_sha256) = 64),
  at_utc TEXT NOT NULL
) STRICT;
CREATE TABLE med_assessment_event (
  run_id TEXT NOT NULL REFERENCES med_assessment_run (run_id),
  seq INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'timed_out', 'interrupted')),
  error_code TEXT,
  at_utc TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
) STRICT;
CREATE UNIQUE INDEX med_assessment_one_terminal ON med_assessment_event (run_id)
WHERE status IN ('completed', 'failed', 'timed_out', 'interrupted');
CREATE TABLE med_assessment_result (
  run_id TEXT PRIMARY KEY REFERENCES med_assessment_run (run_id),
  result_sha256 TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  summaries_json TEXT NOT NULL
) STRICT;
CREATE TRIGGER med_assessment_run_no_update BEFORE UPDATE ON med_assessment_run BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_run_no_delete BEFORE DELETE ON med_assessment_run BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_start_no_update BEFORE UPDATE ON med_assessment_start BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_start_no_delete BEFORE DELETE ON med_assessment_start BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_event_no_update BEFORE UPDATE ON med_assessment_event BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_event_no_delete BEFORE DELETE ON med_assessment_event BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_result_no_update BEFORE UPDATE ON med_assessment_result BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER med_assessment_result_no_delete BEFORE DELETE ON med_assessment_result BEGIN SELECT RAISE(ABORT, 'append-only'); END;
