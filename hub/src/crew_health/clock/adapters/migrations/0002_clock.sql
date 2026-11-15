CREATE TABLE IF NOT EXISTS clock_hub_capture (
    source_clock_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    PRIMARY KEY (source_clock_id, segment_id),
    FOREIGN KEY (source_clock_id, segment_id) REFERENCES clock_segment(source_clock_id, segment_id),
    FOREIGN KEY (version) REFERENCES clock_correction(version)
) STRICT;
