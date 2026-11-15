-- Medicines aboard (E32 5.2), simplified to one append-only record table plus the rows that carry invariants:
-- one terminal event per proposal, one movement per administration (INV-M5), one correction per administration.
-- Decimals are canonical text written by the domain codec; instants are RFC 3339 UTC text.
CREATE TABLE medicines_record (
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
CREATE INDEX medicines_record_subject ON medicines_record (mission_id, origin_mode, kind, subject_person_id, at_utc);
CREATE INDEX medicines_record_lot ON medicines_record (mission_id, origin_mode, kind, lot_id, at_utc);
CREATE TRIGGER medicines_record_no_update BEFORE UPDATE ON medicines_record BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_record_no_delete BEFORE DELETE ON medicines_record BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE medicines_proposal_terminal (
  proposal_id TEXT PRIMARY KEY REFERENCES medicines_record (record_id),
  state TEXT NOT NULL CHECK (state IN ('confirmed', 'expired', 'superseded')),
  at_utc TEXT NOT NULL,
  reason TEXT NOT NULL
) STRICT;
CREATE TRIGGER medicines_terminal_no_update BEFORE UPDATE ON medicines_proposal_terminal BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_terminal_no_delete BEFORE DELETE ON medicines_proposal_terminal BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE medicines_movement (
  movement_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  environment_id TEXT NOT NULL,
  lot_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('administration', 'return', 'waste', 'count_correction')),
  delta_decimal TEXT NOT NULL,
  unit_ucum TEXT NOT NULL,
  administration_id TEXT UNIQUE REFERENCES medicines_record (record_id),
  at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL,
  command_key TEXT UNIQUE,
  CHECK ((kind = 'administration') = (administration_id IS NOT NULL))
) STRICT;
CREATE INDEX medicines_movement_lot ON medicines_movement (mission_id, origin_mode, environment_id, lot_id);
CREATE TRIGGER medicines_movement_no_update BEFORE UPDATE ON medicines_movement BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_movement_no_delete BEFORE DELETE ON medicines_movement BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE medicines_admin_ingredient (
  administration_id TEXT NOT NULL REFERENCES medicines_record (record_id),
  ingredient_id TEXT NOT NULL,
  accounting_decimal TEXT NOT NULL,
  unit_ucum TEXT NOT NULL,
  PRIMARY KEY (administration_id, ingredient_id)
) STRICT;
CREATE TRIGGER medicines_admin_ingredient_no_update BEFORE UPDATE ON medicines_admin_ingredient BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_admin_ingredient_no_delete BEFORE DELETE ON medicines_admin_ingredient BEGIN SELECT RAISE(ABORT, 'append-only'); END;

CREATE TABLE medicines_correction (
  administration_id TEXT PRIMARY KEY REFERENCES medicines_record (record_id),
  reason_text TEXT NOT NULL,
  by_id TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  command_key TEXT UNIQUE
) STRICT;
CREATE TRIGGER medicines_correction_no_update BEFORE UPDATE ON medicines_correction BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_correction_no_delete BEFORE DELETE ON medicines_correction BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Clock evidence (E32 3.8 C1, C3, C4): every medicines command appends the hub clock, the monotonic reading and the
-- process epoch it ran at; a forward jump inside one process (wall time advanced more than monotonic time beyond
-- medicines.clock_forward_tolerance_seconds) appends a discontinuity row with the reading it was measured against and
-- the clock correction journal head at detection. It is resolved only by a later correction revision.
CREATE TABLE medicines_clock_check (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('reading', 'discontinuity')),
  at_utc TEXT NOT NULL,
  monotonic_ns INTEGER NOT NULL,
  process_epoch TEXT NOT NULL,
  reference_seq INTEGER REFERENCES medicines_clock_check (seq),
  wall_delta_ms INTEGER,
  monotonic_delta_ms INTEGER,
  correction_version INTEGER,
  CHECK ((kind = 'discontinuity') = (reference_seq IS NOT NULL))
) STRICT;
CREATE INDEX medicines_clock_check_epoch ON medicines_clock_check (process_epoch, kind, seq);
CREATE TRIGGER medicines_clock_check_no_update BEFORE UPDATE ON medicines_clock_check BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_clock_check_no_delete BEFORE DELETE ON medicines_clock_check BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Artifacts (E32 5.2): content-addressed bytes of pharmacy bundle files, bundle manifests, engine inputs and results.
-- Pruning is the only delete, and only of a hash recorded in medicines_artifact_prune first.
CREATE TABLE medicines_artifact (
  sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
  kind TEXT NOT NULL CHECK (kind IN ('bundle_file', 'bundle_manifest', 'inputs', 'result')),
  bytes BLOB NOT NULL,
  size_bytes INTEGER NOT NULL CHECK (size_bytes = length(bytes)),
  at_utc TEXT NOT NULL
) STRICT;
CREATE TABLE medicines_artifact_prune (
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  run_id TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  PRIMARY KEY (sha256, run_id)
) STRICT;
CREATE TRIGGER medicines_artifact_no_update BEFORE UPDATE ON medicines_artifact BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_artifact_delete_only_pruned BEFORE DELETE ON medicines_artifact
WHEN NOT EXISTS (SELECT 1 FROM medicines_artifact_prune WHERE sha256 = OLD.sha256)
BEGIN SELECT RAISE(ABORT, 'only a recorded prune deletes an artifact'); END;
CREATE TRIGGER medicines_artifact_prune_no_update BEFORE UPDATE ON medicines_artifact_prune BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_artifact_prune_no_delete BEFORE DELETE ON medicines_artifact_prune BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Pharmacy bundles: the manifest (file name to file SHA-256) is itself an artifact and its hash is the bundle hash.
CREATE TABLE medicines_bundle (
  bundle_sha256 TEXT PRIMARY KEY REFERENCES medicines_artifact (sha256),
  imported_at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL
) STRICT;
CREATE TABLE medicines_bundle_activation (
  seq INTEGER PRIMARY KEY,
  bundle_sha256 TEXT NOT NULL REFERENCES medicines_bundle (bundle_sha256),
  at_utc TEXT NOT NULL,
  by_id TEXT NOT NULL
) STRICT;
CREATE TRIGGER medicines_bundle_no_update BEFORE UPDATE ON medicines_bundle BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_bundle_no_delete BEFORE DELETE ON medicines_bundle BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_bundle_activation_no_update BEFORE UPDATE ON medicines_bundle_activation BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_bundle_activation_no_delete BEFORE DELETE ON medicines_bundle_activation BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Assessment runs: scheduled (queued) with their trigger, started with the bundle and inputs they ran on; status is
-- the last event, one terminal event per run.
CREATE TABLE medicines_assessment_run (
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
CREATE INDEX medicines_assessment_run_partition ON medicines_assessment_run (mission_id, origin_mode, at_utc);
CREATE TABLE medicines_assessment_start (
  run_id TEXT PRIMARY KEY REFERENCES medicines_assessment_run (run_id),
  as_of_utc TEXT NOT NULL,
  bundle_sha256 TEXT NOT NULL REFERENCES medicines_bundle (bundle_sha256),
  inputs_sha256 TEXT NOT NULL CHECK (length(inputs_sha256) = 64),
  at_utc TEXT NOT NULL
) STRICT;
CREATE TABLE medicines_assessment_event (
  run_id TEXT NOT NULL REFERENCES medicines_assessment_run (run_id),
  seq INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed', 'timed_out', 'interrupted')),
  error_code TEXT,
  at_utc TEXT NOT NULL,
  PRIMARY KEY (run_id, seq)
) STRICT;
CREATE UNIQUE INDEX medicines_assessment_one_terminal ON medicines_assessment_event (run_id)
WHERE status IN ('completed', 'failed', 'timed_out', 'interrupted');
CREATE TABLE medicines_assessment_result (
  run_id TEXT PRIMARY KEY REFERENCES medicines_assessment_run (run_id),
  result_sha256 TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  summaries_json TEXT NOT NULL
) STRICT;
CREATE TRIGGER medicines_assessment_run_no_update BEFORE UPDATE ON medicines_assessment_run BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_run_no_delete BEFORE DELETE ON medicines_assessment_run BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_start_no_update BEFORE UPDATE ON medicines_assessment_start BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_start_no_delete BEFORE DELETE ON medicines_assessment_start BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_event_no_update BEFORE UPDATE ON medicines_assessment_event BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_event_no_delete BEFORE DELETE ON medicines_assessment_event BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_result_no_update BEFORE UPDATE ON medicines_assessment_result BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER medicines_assessment_result_no_delete BEFORE DELETE ON medicines_assessment_result BEGIN SELECT RAISE(ABORT, 'append-only'); END;
