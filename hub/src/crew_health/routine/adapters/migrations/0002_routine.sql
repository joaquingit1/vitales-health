-- Immutable routine record versions let the shared ledger refer to the accepted result.
CREATE TABLE routine_record_version (
    kind TEXT NOT NULL CHECK (kind IN ('occurrence','override')),
    record_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision>=1),
    record BLOB NOT NULL,
    PRIMARY KEY(kind,record_id,revision)
) STRICT;
CREATE TABLE routine_command_version (
    actor_ref TEXT NOT NULL, command_key TEXT NOT NULL,
    kind TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(actor_ref,command_key),
    FOREIGN KEY(kind,record_id,revision) REFERENCES routine_record_version(kind,record_id,revision)
) STRICT;
CREATE TRIGGER routine_version_no_update BEFORE UPDATE ON routine_record_version
BEGIN SELECT RAISE(ABORT,'routine versions are append only'); END;
CREATE TRIGGER routine_version_no_delete BEFORE DELETE ON routine_record_version
BEGIN SELECT RAISE(ABORT,'routine versions are append only'); END;
CREATE TRIGGER routine_command_version_no_update BEFORE UPDATE ON routine_command_version
BEGIN SELECT RAISE(ABORT,'routine command references are append only'); END;
CREATE TRIGGER routine_command_version_no_delete BEFORE DELETE ON routine_command_version
BEGIN SELECT RAISE(ABORT,'routine command references are append only'); END;
