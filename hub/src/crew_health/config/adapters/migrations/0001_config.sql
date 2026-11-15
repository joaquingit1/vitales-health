CREATE TABLE IF NOT EXISTS config_document (
    name TEXT NOT NULL,
    version TEXT NOT NULL CHECK(length(version)=64),
    raw BLOB NOT NULL,
    loaded_at TEXT NOT NULL,
    PRIMARY KEY(name,version)
) STRICT;
CREATE TABLE IF NOT EXISTS config_release (
    release_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('draft','active','superseded','rejected')),
    created_at TEXT NOT NULL,
    created_by_json BLOB NOT NULL,
    activated_at TEXT,
    activated_by_json BLOB,
    superseded_at TEXT,
    validation_json BLOB NOT NULL,
    note TEXT,
    object_revision INTEGER NOT NULL CHECK(object_revision>0)
) STRICT;
CREATE TABLE IF NOT EXISTS config_release_pin (
    release_id TEXT NOT NULL REFERENCES config_release(release_id),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    PRIMARY KEY(release_id,name),
    FOREIGN KEY(name,version) REFERENCES config_document(name,version)
) STRICT;
CREATE TABLE IF NOT EXISTS config_activation (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    release_id TEXT REFERENCES config_release(release_id),
    generation INTEGER NOT NULL CHECK(generation>=0)
) STRICT;
INSERT OR IGNORE INTO config_activation VALUES (1,NULL,0);
CREATE INDEX IF NOT EXISTS config_release_order ON config_release(created_at,release_id);
CREATE TRIGGER IF NOT EXISTS config_document_no_update BEFORE UPDATE ON config_document BEGIN SELECT RAISE(ABORT,'immutable document'); END;
CREATE TRIGGER IF NOT EXISTS config_document_no_delete BEFORE DELETE ON config_document BEGIN SELECT RAISE(ABORT,'immutable document'); END;
CREATE TRIGGER IF NOT EXISTS config_pin_no_update BEFORE UPDATE ON config_release_pin BEGIN SELECT RAISE(ABORT,'immutable pin'); END;
CREATE TRIGGER IF NOT EXISTS config_pin_no_delete BEFORE DELETE ON config_release_pin BEGIN SELECT RAISE(ABORT,'immutable pin'); END;
CREATE TRIGGER IF NOT EXISTS config_release_no_delete BEFORE DELETE ON config_release BEGIN SELECT RAISE(ABORT,'durable release'); END;
