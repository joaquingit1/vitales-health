CREATE TABLE IF NOT EXISTS events_retention (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    watermark INTEGER NOT NULL CHECK(watermark>=0)
) STRICT;
INSERT OR IGNORE INTO events_retention VALUES(1,0);
CREATE TABLE IF NOT EXISTS events_outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    emitted_at TEXT NOT NULL,
    envelope BLOB NOT NULL,
    content_hash TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS events_outbox_age ON events_outbox(emitted_at,seq);
CREATE TABLE IF NOT EXISTS events_cursor (
    subscriber TEXT PRIMARY KEY,
    seq INTEGER NOT NULL CHECK(seq>=0)
) STRICT;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events_outbox BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS events_delete_guard BEFORE DELETE ON events_outbox
WHEN OLD.seq>(SELECT watermark FROM events_retention WHERE singleton=1)
BEGIN SELECT RAISE(ABORT,'event retained'); END;
CREATE TRIGGER IF NOT EXISTS events_watermark_monotonic BEFORE UPDATE ON events_retention
WHEN NEW.watermark<OLD.watermark OR NEW.singleton!=OLD.singleton
BEGIN SELECT RAISE(ABORT,'retention watermark cannot decrease'); END;
CREATE TRIGGER IF NOT EXISTS events_watermark_no_delete BEFORE DELETE ON events_retention
BEGIN SELECT RAISE(ABORT,'retention watermark required'); END;
CREATE TRIGGER IF NOT EXISTS events_cursor_monotonic BEFORE UPDATE ON events_cursor
WHEN NEW.seq<OLD.seq OR NEW.subscriber!=OLD.subscriber
BEGIN SELECT RAISE(ABORT,'subscriber cursor cannot decrease'); END;
CREATE TRIGGER IF NOT EXISTS events_cursor_no_delete BEFORE DELETE ON events_cursor
BEGIN SELECT RAISE(ABORT,'durable subscriber cannot be discarded'); END;
