-- Supplies ledger (E32 5.1; SYSTEM 4.2 row supplies, 8.1 partitions). STRICT, append only; decimals are canonical
-- text written by the domain codec (SQLite never does arithmetic on them); instants are RFC 3339 UTC text.
CREATE TABLE supplies_use (
  supply_use_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  environment_id TEXT NOT NULL,
  custody_location_id TEXT NOT NULL,
  supply_id TEXT NOT NULL,
  unit_ucum TEXT NOT NULL,
  quantity_decimal TEXT NOT NULL,
  partition_seq INTEGER NOT NULL CHECK (partition_seq >= 1),
  used_after_decimal TEXT NOT NULL,
  plan_run_id TEXT,
  help_session_id TEXT,
  step_id TEXT,
  occurrence INTEGER CHECK (occurrence IS NULL OR occurrence >= 1),
  at_utc TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL,
  by_kind TEXT NOT NULL,
  by_id TEXT NOT NULL,
  config_release_id TEXT NOT NULL,
  command_key TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL,
  CHECK (NOT (plan_run_id IS NOT NULL AND help_session_id IS NOT NULL)),
  UNIQUE (mission_id, origin_mode, environment_id, custody_location_id, supply_id, partition_seq)
) STRICT;
CREATE TRIGGER supplies_use_no_update BEFORE UPDATE ON supplies_use BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER supplies_use_no_delete BEFORE DELETE ON supplies_use BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- The unit each supply's uses are counted in, registered by its first use (INV-S13, guard S-A1).
CREATE TABLE supplies_stock (
  supply_id TEXT PRIMARY KEY,
  unit_ucum TEXT NOT NULL,
  first_use_id TEXT NOT NULL
) STRICT;
CREATE TRIGGER supplies_stock_no_update BEFORE UPDATE ON supplies_stock BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER supplies_stock_no_delete BEFORE DELETE ON supplies_stock BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- Evidence of a help-session use the ledger refused (E32 4.1, HZ14): one row per command key.
CREATE TABLE supplies_use_refused (
  refusal_id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE', 'REPLAY', 'SIM')),
  environment_id TEXT NOT NULL,
  custody_location_id TEXT NOT NULL,
  supply_id TEXT NOT NULL,
  unit_ucum TEXT NOT NULL,
  quantity_decimal TEXT NOT NULL,
  help_session_id TEXT NOT NULL,
  step_id TEXT,
  code TEXT NOT NULL,
  command_key TEXT NOT NULL UNIQUE,
  at_utc TEXT NOT NULL,
  observed_at_utc TEXT NOT NULL
) STRICT;
CREATE TRIGGER supplies_use_refused_no_update BEFORE UPDATE ON supplies_use_refused BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER supplies_use_refused_no_delete BEFORE DELETE ON supplies_use_refused BEGIN SELECT RAISE(ABORT, 'append-only'); END;
