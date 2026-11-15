-- CONTRACTS 26 and SYSTEM 8.1: approved catch-up is immutable; UTC instants use RFC 3339 text.
-- pinned_versions holds the help-pinned document versions the lines were rendered with (canonical JSON).
CREATE TABLE catchup_message (
    ground_message_id TEXT PRIMARY KEY,
    help_session_id TEXT NOT NULL,
    session_revision INTEGER NOT NULL CHECK (session_revision >= 1),
    queued_at TEXT NOT NULL,
    title TEXT NOT NULL CHECK (length(title) > 0),
    simulated INTEGER NOT NULL CHECK (simulated IN (0, 1)),
    state TEXT NOT NULL CHECK (state IN ('pending', 'frozen')),
    pinned_versions TEXT,
    composed_at TEXT,
    subject_person_id TEXT,
    UNIQUE (help_session_id, session_revision),
    CHECK ((state = 'pending' AND pinned_versions IS NULL AND composed_at IS NULL)
        OR (state = 'frozen' AND pinned_versions IS NOT NULL AND composed_at IS NOT NULL))
) STRICT;
CREATE TABLE catchup_line (
    ground_message_id TEXT NOT NULL REFERENCES catchup_message (ground_message_id),
    section TEXT NOT NULL CHECK (section IN ('what_happened', 'what_was_done', 'being_watched', 'advice_welcome')),
    idx INTEGER NOT NULL CHECK (idx >= 0),
    text TEXT NOT NULL CHECK (length(text) > 0),
    PRIMARY KEY (ground_message_id, section, idx)
) STRICT;
CREATE TABLE catchup_quarantine (
    event_id TEXT PRIMARY KEY,
    subscriber TEXT NOT NULL,
    event_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK (attempts >= 1),
    quarantined_at TEXT NOT NULL
) STRICT;
CREATE TRIGGER catchup_message_no_delete BEFORE DELETE ON catchup_message BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER catchup_message_frozen BEFORE UPDATE ON catchup_message WHEN OLD.state = 'frozen' BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER catchup_line_no_update BEFORE UPDATE ON catchup_line BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER catchup_line_no_delete BEFORE DELETE ON catchup_line BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER catchup_line_frozen BEFORE INSERT ON catchup_line WHEN (SELECT state FROM catchup_message WHERE ground_message_id = NEW.ground_message_id) = 'frozen' BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER catchup_quarantine_no_update BEFORE UPDATE ON catchup_quarantine BEGIN SELECT RAISE(ABORT, 'append only'); END;
CREATE TRIGGER catchup_quarantine_no_delete BEFORE DELETE ON catchup_quarantine BEGIN SELECT RAISE(ABORT, 'append only'); END;
