-- Medicines aboard (E32 5.2), simplified to one append-only record table plus the rows that carry invariants:
-- one terminal event per proposal, one movement per administration (INV-M5), one correction per administration.
-- Decimals are canonical text written by the domain codec; instants are RFC 3339 UTC text.
CREATE TABLE med_record (
  record_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('inspection', 'context', 'diagnosis', 'proposal', 'administration', 'assessment')),
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
