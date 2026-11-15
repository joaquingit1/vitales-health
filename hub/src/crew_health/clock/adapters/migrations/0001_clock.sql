CREATE TABLE IF NOT EXISTS clock_segment (
    source_clock_id TEXT NOT NULL,
    physical_clock_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    predecessor_id TEXT,
    first_sequence INTEGER NOT NULL CHECK (first_sequence >= 0),
    capture_offset_version INTEGER NOT NULL CHECK (capture_offset_version >= 0),
    PRIMARY KEY (source_clock_id, segment_id),
    UNIQUE (source_clock_id, first_sequence),
    FOREIGN KEY (source_clock_id, predecessor_id) REFERENCES clock_segment(source_clock_id, segment_id)
) STRICT;
CREATE INDEX IF NOT EXISTS clock_segment_sequence ON clock_segment(source_clock_id, first_sequence DESC);
CREATE TABLE IF NOT EXISTS clock_correction (
    correction_id TEXT PRIMARY KEY,
    source_clock_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    version INTEGER NOT NULL UNIQUE CHECK (version > 0),
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    offset_ms INTEGER NOT NULL,
    uncertainty_ms INTEGER NOT NULL CHECK (uncertainty_ms >= 0),
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('operator','measured','ground')),
    CHECK (valid_until IS NULL OR valid_from < valid_until),
    FOREIGN KEY (source_clock_id, segment_id) REFERENCES clock_segment(source_clock_id, segment_id)
) STRICT;
CREATE INDEX IF NOT EXISTS clock_correction_effective ON clock_correction(source_clock_id,segment_id,version DESC);
CREATE TABLE IF NOT EXISTS clock_mission_version (
    clock_version TEXT PRIMARY KEY,
    generation INTEGER NOT NULL UNIQUE CHECK (generation > 0),
    epoch TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_until TEXT,
    day_label_template TEXT NOT NULL,
    CHECK (effective_until IS NULL OR effective_from < effective_until)
) STRICT;
CREATE INDEX IF NOT EXISTS clock_mission_effective ON clock_mission_version(generation DESC,effective_from,effective_until);
CREATE TABLE IF NOT EXISTS clock_hlc (
    node_id TEXT NOT NULL,
    incarnation_id TEXT NOT NULL,
    physical_ms INTEGER NOT NULL CHECK (physical_ms >= 0),
    logical INTEGER NOT NULL CHECK (logical >= 0),
    PRIMARY KEY (node_id, incarnation_id)
) STRICT;
CREATE TRIGGER IF NOT EXISTS clock_segment_no_update BEFORE UPDATE ON clock_segment BEGIN SELECT RAISE(ABORT,'immutable clock segment'); END;
CREATE TRIGGER IF NOT EXISTS clock_segment_no_delete BEFORE DELETE ON clock_segment BEGIN SELECT RAISE(ABORT,'immutable clock segment'); END;
CREATE TRIGGER IF NOT EXISTS clock_correction_no_update BEFORE UPDATE ON clock_correction BEGIN SELECT RAISE(ABORT,'immutable correction'); END;
CREATE TRIGGER IF NOT EXISTS clock_correction_no_delete BEFORE DELETE ON clock_correction BEGIN SELECT RAISE(ABORT,'immutable correction'); END;
CREATE TRIGGER IF NOT EXISTS clock_mission_no_update BEFORE UPDATE ON clock_mission_version BEGIN SELECT RAISE(ABORT,'immutable mission version'); END;
CREATE TRIGGER IF NOT EXISTS clock_mission_no_delete BEFORE DELETE ON clock_mission_version BEGIN SELECT RAISE(ABORT,'immutable mission version'); END;
CREATE TABLE IF NOT EXISTS clock_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    last_committed_at TEXT NOT NULL,
    frozen INTEGER NOT NULL CHECK (frozen IN (0,1)),
    correction_version INTEGER NOT NULL CHECK (correction_version>=0),
    minimum_revision INTEGER NOT NULL CHECK (minimum_revision>=-1)
) STRICT;
