-- Help module tables (SYSTEM 4.2). The aggregate is stored whole with its indexed columns; one open session per
-- subject is enforced by the partial unique index, so a duplicate start can never create a second one.
CREATE TABLE help_session (
    help_session_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (state IN ('open', 'closed')),
    subject_person_id TEXT,
    opened_at TEXT NOT NULL,
    turn_id TEXT,
    aggregate_json TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX help_session_open_subject
    ON help_session (subject_person_id) WHERE state = 'open' AND subject_person_id IS NOT NULL;
CREATE UNIQUE INDEX help_session_turn ON help_session (turn_id) WHERE turn_id IS NOT NULL;
CREATE INDEX help_session_state ON help_session (state, help_session_id);

CREATE TABLE help_action (
    help_session_id TEXT NOT NULL REFERENCES help_session (help_session_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    action TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    command_key TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (help_session_id, revision)
) STRICT;

CREATE TABLE help_broadcast (
    broadcast_id TEXT PRIMARY KEY,
    help_session_id TEXT NOT NULL UNIQUE REFERENCES help_session (help_session_id),
    state TEXT NOT NULL CHECK (state IN ('active', 'ended')),
    started_at TEXT NOT NULL,
    view_json TEXT NOT NULL
) STRICT;

CREATE INDEX help_broadcast_state ON help_broadcast (state, started_at, broadcast_id);

CREATE TABLE help_ground_message (
    ground_message_id TEXT PRIMARY KEY,
    help_session_id TEXT NOT NULL REFERENCES help_session (help_session_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (state IN ('queued', 'sent', 'acknowledged')),
    text_sha256 TEXT NOT NULL,
    message_json TEXT NOT NULL
) STRICT;

CREATE TABLE help_log_snapshot (
    ground_message_id TEXT PRIMARY KEY REFERENCES help_ground_message (ground_message_id),
    help_session_id TEXT NOT NULL REFERENCES help_session (help_session_id),
    session_revision INTEGER NOT NULL CHECK (session_revision >= 1),
    snapshot_json TEXT NOT NULL
) STRICT;
