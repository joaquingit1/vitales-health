-- Camera dispatch intents (E06 hub side, coordinator decision A7). One row per E05 start assigned to the camera
-- worker, keyed by the start's correlation ID; STRICT; instants are RFC 3339 UTC text. device_ids is the JSON list
-- of the cameras the device configuration pairs with the subject (B1).
CREATE TABLE IF NOT EXISTS camera_dispatch (
  correlation_id TEXT PRIMARY KEY,
  capability_code TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('assigned', 'ready', 'dispatched', 'done', 'reporting', 'reported', 'dropped', 'cancelling', 'cancelled')),
  request_id TEXT,
  patient_id TEXT,
  sequence INTEGER CHECK (sequence IS NULL OR sequence >= 1),
  acquisition_id TEXT UNIQUE,
  device_ids TEXT NOT NULL DEFAULT '[]',
  required_origin TEXT CHECK (required_origin IS NULL OR required_origin IN ('LIVE', 'REPLAY', 'SIM')),
  deadline TEXT,
  code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS camera_dispatch_state ON camera_dispatch (state, updated_at);
CREATE INDEX IF NOT EXISTS camera_dispatch_request ON camera_dispatch (request_id, sequence);
-- Reference windows opened for E30 (M8): whether the window persists, and the observation its reading was recorded
-- as, written once and never cleared (abandoning never deletes a recorded reading).
CREATE TABLE IF NOT EXISTS camera_reference (
  ticket_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  persist INTEGER NOT NULL CHECK (persist IN (0, 1)),
  observation_id TEXT,
  opened_at TEXT NOT NULL,
  recorded_at TEXT
) STRICT;
