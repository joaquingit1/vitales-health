CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts_exact USING fts5(release_id UNINDEXED, passage_id UNINDEXED, search_text, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts_stemmed USING fts5(release_id UNINDEXED, passage_id UNINDEXED, search_text, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS knowledge_index_member (
    release_id TEXT NOT NULL,
    passage_id TEXT NOT NULL,
    use TEXT NOT NULL CHECK (use IN ('risk_context','patient_instruction','routine_basis','limit_basis')),
    environments TEXT,
    PRIMARY KEY (release_id,passage_id,use)
) STRICT;
CREATE TABLE IF NOT EXISTS knowledge_index_ready (
    release_id TEXT PRIMARY KEY,
    passage_count INTEGER NOT NULL CHECK (passage_count >= 0)
) STRICT;
