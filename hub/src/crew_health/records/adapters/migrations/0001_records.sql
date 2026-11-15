CREATE TABLE IF NOT EXISTS records_observation (
    ingestion_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL UNIQUE,
    patient_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK(origin_mode IN ('LIVE','REPLAY','SIM')),
    code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    quality_status TEXT NOT NULL CHECK(quality_status IN ('accepted','rejected','uncertain','not_assessed')),
    supersedes TEXT REFERENCES records_observation(observation_id),
    payload BLOB NOT NULL,
    digest TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS records_observation_partition ON records_observation(patient_id,mission_id,origin_mode,code,observed_at DESC,observation_id);
CREATE TRIGGER IF NOT EXISTS records_observation_no_update BEFORE UPDATE ON records_observation BEGIN SELECT RAISE(ABORT,'immutable observation'); END;
CREATE TRIGGER IF NOT EXISTS records_observation_no_delete BEFORE DELETE ON records_observation BEGIN SELECT RAISE(ABORT,'immutable observation'); END;
CREATE TABLE IF NOT EXISTS records_answer (
    ingestion_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    answer_id TEXT NOT NULL UNIQUE,
    patient_id TEXT NOT NULL,
    mission_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK(origin_mode IN ('LIVE','REPLAY','SIM')),
    question_id TEXT NOT NULL,
    answered_at TEXT NOT NULL,
    supersedes TEXT REFERENCES records_answer(answer_id),
    payload BLOB NOT NULL,
    digest TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS records_answer_partition ON records_answer(patient_id,mission_id,origin_mode,question_id,answered_at DESC,answer_id);
CREATE TRIGGER IF NOT EXISTS records_answer_no_update BEFORE UPDATE ON records_answer BEGIN SELECT RAISE(ABORT,'immutable answer'); END;
CREATE TRIGGER IF NOT EXISTS records_answer_no_delete BEFORE DELETE ON records_answer BEGIN SELECT RAISE(ABORT,'immutable answer'); END;
