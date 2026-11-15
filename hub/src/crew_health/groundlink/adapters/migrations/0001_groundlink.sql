-- Ground link and approved comms queue (CONTRACTS 12; SYSTEM 4.2).
-- STRICT tables; instants are RFC 3339 UTC text; delays are decimal text in seconds.
CREATE TABLE groundlink_status (
    seq INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('environment','override','clear','observed')),
    at TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    override_json TEXT,
    state_json TEXT NOT NULL
) STRICT;
CREATE TABLE groundlink_bundle (
    bundle_id TEXT PRIMARY KEY,
    ref_kind TEXT NOT NULL CHECK(ref_kind IN ('ground_message','handoff')),
    ref_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','sending','sent','arrived')),
    attempts INTEGER NOT NULL CHECK(attempts >= 0),
    next_attempt_at TEXT,
    sent_at TEXT,
    arrival_due_at TEXT,
    arrived_at TEXT,
    one_way_delay_s TEXT,
    simulated INTEGER NOT NULL CHECK(simulated IN (0,1)),
    last_error TEXT,
    UNIQUE(ref_kind, ref_id)
) STRICT;
CREATE INDEX groundlink_bundle_queue ON groundlink_bundle(state, queued_at);
CREATE TABLE groundlink_ack (
    ack_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    ref_kind TEXT NOT NULL CHECK(ref_kind IN ('ground_message','handoff')),
    ref_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('receipt','reply')),
    sent_by_ground_at TEXT NOT NULL,
    text TEXT,
    by_kind TEXT NOT NULL,
    by_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('waiting','due','delivered')),
    arrival_due_at TEXT,
    delivered_at TEXT,
    attempts INTEGER NOT NULL CHECK(attempts >= 0),
    next_attempt_at TEXT,
    last_error TEXT
) STRICT;
CREATE INDEX groundlink_ack_queue ON groundlink_ack(state, arrival_due_at);
CREATE TRIGGER groundlink_status_no_update BEFORE UPDATE ON groundlink_status
BEGIN SELECT RAISE(ABORT, 'groundlink history is append only'); END;
CREATE TRIGGER groundlink_bundle_no_delete BEFORE DELETE ON groundlink_bundle
BEGIN SELECT RAISE(ABORT, 'approved bundles cannot be deleted'); END;
CREATE TRIGGER groundlink_ack_no_delete BEFORE DELETE ON groundlink_ack
BEGIN SELECT RAISE(ABORT, 'ground acknowledgements cannot be deleted'); END;

CREATE INDEX groundlink_status_kind ON groundlink_status(kind, seq);
CREATE TABLE groundlink_quarantine (
    event_id TEXT PRIMARY KEY,
    subscriber TEXT NOT NULL,
    event_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    quarantined_at TEXT NOT NULL
) STRICT;
CREATE TRIGGER groundlink_quarantine_no_update BEFORE UPDATE ON groundlink_quarantine
BEGIN SELECT RAISE(ABORT, 'quarantine is append only'); END;
CREATE TRIGGER groundlink_quarantine_no_delete BEFORE DELETE ON groundlink_quarantine
BEGIN SELECT RAISE(ABORT, 'quarantine is append only'); END;
CREATE TABLE groundlink_gap (
    gap_seq INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    gap_kind TEXT NOT NULL,
    gap_planned INTEGER NOT NULL CHECK(gap_planned IN (0,1))
) STRICT;
-- The bound is operational state so the retention trigger can enforce the active hub setting.
CREATE TABLE groundlink_retention (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    retention_rows INTEGER NOT NULL CHECK(retention_rows BETWEEN 100 AND 1000000)
) STRICT;
CREATE TRIGGER groundlink_gap_close_only BEFORE UPDATE ON groundlink_gap
WHEN OLD.ended_at IS NOT NULL OR NEW.ended_at IS NULL
    OR NEW.gap_seq IS NOT OLD.gap_seq OR NEW.started_at IS NOT OLD.started_at
    OR NEW.gap_kind IS NOT OLD.gap_kind OR NEW.gap_planned IS NOT OLD.gap_planned
BEGIN SELECT RAISE(ABORT, 'only an open gap can be closed'); END;
CREATE TRIGGER groundlink_gap_retention BEFORE DELETE ON groundlink_gap
WHEN OLD.gap_seq >= coalesce((
    SELECT gap_seq FROM groundlink_gap ORDER BY gap_seq DESC LIMIT 1
    OFFSET (SELECT retention_rows-1 FROM groundlink_retention WHERE singleton=1)
), OLD.gap_seq)
BEGIN SELECT RAISE(ABORT, 'gap retained'); END;
