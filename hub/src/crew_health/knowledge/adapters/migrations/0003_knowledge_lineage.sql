CREATE TABLE IF NOT EXISTS knowledge_passage_artifact (
    passage_id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL,
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    text_hash TEXT NOT NULL,
    immutable_body_hash TEXT NOT NULL,
    immutable_body BLOB NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS knowledge_release_member (
    release_id TEXT NOT NULL,
    passage_id TEXT NOT NULL REFERENCES knowledge_passage_artifact(passage_id),
    membership_hash TEXT NOT NULL,
    snapshot BLOB NOT NULL,
    PRIMARY KEY (release_id,passage_id)
) STRICT;
CREATE TABLE IF NOT EXISTS knowledge_release_use (
    release_id TEXT NOT NULL,
    passage_id TEXT NOT NULL,
    use TEXT NOT NULL CHECK (use IN ('risk_context','patient_instruction','routine_basis','limit_basis')),
    status TEXT NOT NULL CHECK (status IN ('approved','superseded','withdrawn')),
    approval_ref TEXT NOT NULL,
    successor_ref TEXT,
    PRIMARY KEY (release_id,passage_id,use),
    FOREIGN KEY (release_id,passage_id) REFERENCES knowledge_release_member(release_id,passage_id)
) STRICT;
CREATE TRIGGER IF NOT EXISTS knowledge_artifact_no_update BEFORE UPDATE ON knowledge_passage_artifact BEGIN SELECT RAISE(ABORT,'immutable evidence artifact'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_artifact_no_delete BEFORE DELETE ON knowledge_passage_artifact BEGIN SELECT RAISE(ABORT,'immutable evidence artifact'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_member_no_update BEFORE UPDATE ON knowledge_release_member BEGIN SELECT RAISE(ABORT,'immutable evidence membership'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_member_no_delete BEFORE DELETE ON knowledge_release_member BEGIN SELECT RAISE(ABORT,'immutable evidence membership'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_use_no_update BEFORE UPDATE ON knowledge_release_use BEGIN SELECT RAISE(ABORT,'immutable evidence purpose'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_use_no_delete BEFORE DELETE ON knowledge_release_use BEGIN SELECT RAISE(ABORT,'immutable evidence purpose'); END;
