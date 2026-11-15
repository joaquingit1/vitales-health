-- E24 warnings storage (design 5.1). Expand only: the previous release keeps running against it.
-- A warning is one row: the columns below are the keys every decision, index and constraint needs; `body_json`
-- is the complete stored warning (acknowledgements and notes included). Acknowledgements and notes are part of the
-- warning's revision history, never a second table that could disagree with it.
CREATE TABLE rules_warning (
  warning_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  origin_node_id TEXT NOT NULL,
  definition_id TEXT NOT NULL,
  config_release_id TEXT NOT NULL,
  rule_fingerprint TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  trigger_kind TEXT NOT NULL,
  trigger_ref TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('info','watch','act','emergency')),
  raised_by TEXT NOT NULL CHECK (raised_by IN ('rule','sensor_limit','assistant')),
  sensor_type_id TEXT,
  channel_id TEXT,
  scope TEXT NOT NULL CHECK (scope IN ('habitat','person','crew')),
  scope_key TEXT NOT NULL,
  person_id TEXT,
  environment_id TEXT NOT NULL,
  origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE','REPLAY','SIM')),
  insight_id TEXT,
  headline_rank INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('raised','acknowledged','cleared','resolved','dismissed')),
  raised_at TEXT NOT NULL,
  cleared_at TEXT,
  clear_reason TEXT CHECK (clear_reason IS NULL OR clear_reason IN
    ('condition_false','clear_event','expired','superseded','environment_changed','config_changed')),
  steps_json TEXT NOT NULL CHECK (json_array_length(steps_json) >= 1),
  evidence_json TEXT NOT NULL,
  body_json TEXT NOT NULL,
  CHECK (raised_by <> 'assistant' OR level IN ('info','watch')),
  CHECK (raised_by = 'assistant' OR state <> 'dismissed'),
  CHECK (raised_by <> 'sensor_limit' OR sensor_type_id IS NOT NULL),
  CHECK (scope <> 'person' OR person_id IS NOT NULL),
  CHECK ((state = 'cleared') = (clear_reason IS NOT NULL AND cleared_at IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX rules_warning_one_active ON rules_warning(definition_id, scope_key)
  WHERE state IN ('raised','acknowledged');
CREATE INDEX rules_warning_trigger ON rules_warning(definition_id, scope_key, trigger_kind, trigger_ref);
CREATE INDEX rules_warning_active ON rules_warning(state, environment_id, level, raised_at)
  WHERE state IN ('raised','acknowledged');
CREATE INDEX rules_warning_person ON rules_warning(person_id, state);
CREATE INDEX rules_warning_group ON rules_warning(sensor_type_id, channel_id, state);
CREATE INDEX rules_warning_insight ON rules_warning(insight_id);
CREATE TRIGGER rules_warning_immutable BEFORE UPDATE ON rules_warning
WHEN NEW.level <> OLD.level OR NEW.raised_by <> OLD.raised_by OR NEW.definition_id <> OLD.definition_id
  OR NEW.scope_key <> OLD.scope_key OR NEW.trigger_kind <> OLD.trigger_kind OR NEW.trigger_ref <> OLD.trigger_ref
  OR NEW.raised_at <> OLD.raised_at OR NEW.evidence_json <> OLD.evidence_json OR NEW.steps_json <> OLD.steps_json
  OR NEW.origin_mode <> OLD.origin_mode OR NEW.origin_node_id <> OLD.origin_node_id
  OR NEW.config_release_id <> OLD.config_release_id OR NEW.rule_fingerprint <> OLD.rule_fingerprint
  OR OLD.state IN ('cleared','resolved','dismissed') OR NEW.revision <> OLD.revision + 1
BEGIN SELECT RAISE(ABORT, 'rules_warning immutable field or final state'); END;
CREATE TRIGGER rules_warning_no_delete BEFORE DELETE ON rules_warning
BEGIN SELECT RAISE(ABORT, 'rules_warning is append only'); END;

CREATE TABLE rules_occurrence (
  definition_id TEXT NOT NULL, scope_key TEXT NOT NULL, trigger_ref TEXT NOT NULL,
  warning_id TEXT REFERENCES rules_warning(warning_id),
  kind TEXT NOT NULL CHECK (kind IN ('trigger','clear')),
  occurred_at TEXT NOT NULL, recorded_at TEXT NOT NULL, resolved_by_event_id TEXT,
  PRIMARY KEY (definition_id, scope_key, trigger_ref, kind)) STRICT;
CREATE INDEX rules_occurrence_open ON rules_occurrence(warning_id) WHERE resolved_by_event_id IS NULL;
CREATE INDEX rules_occurrence_clear ON rules_occurrence(definition_id, scope_key, kind, occurred_at);

CREATE TABLE rules_dwell (
  warning_id TEXT PRIMARY KEY REFERENCES rules_warning(warning_id),
  channel_id TEXT NOT NULL, rule_fingerprint TEXT NOT NULL, segment_key TEXT NOT NULL,
  holding_since TEXT NOT NULL,
  last_window_end TEXT NOT NULL, CHECK (last_window_end >= holding_since)) STRICT;
CREATE INDEX rules_dwell_channel ON rules_dwell(channel_id);

CREATE TABLE rules_deadline (
  deadline_key TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('absolute','elapsed')),
  due_at TEXT, elapsed_seconds INTEGER, anchored_at TEXT NOT NULL,
  generation INTEGER NOT NULL DEFAULT 0) STRICT;

-- The evaluation_pending marker of S8.6.
CREATE TABLE rules_pending (
  pending_id TEXT PRIMARY KEY, input_kind TEXT NOT NULL, input_ref TEXT NOT NULL,
  coalesce_key TEXT, input_json TEXT NOT NULL, input_seq INTEGER NOT NULL,
  first_failed_at TEXT NOT NULL, attempts INTEGER NOT NULL, last_code TEXT NOT NULL,
  next_attempt_at TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending','stuck','quarantined','overflow'))) STRICT;
CREATE UNIQUE INDEX rules_pending_coalesce ON rules_pending(coalesce_key) WHERE coalesce_key IS NOT NULL;
CREATE INDEX rules_pending_order ON rules_pending(input_seq);

-- Origin ownership (3.15): replicas of other nodes' warnings. The engine never reads this table.
CREATE TABLE rules_warning_replica (
  warning_id TEXT PRIMARY KEY, origin_node_id TEXT NOT NULL, origin_hlc TEXT NOT NULL,
  revision INTEGER NOT NULL, scope TEXT NOT NULL, person_id TEXT,
  warning_json TEXT NOT NULL, applied_at TEXT NOT NULL) STRICT;

-- The first 200 of an action, byte for byte (durable idempotency, S8.6).
CREATE TABLE rules_action_result (
  actor_ref TEXT NOT NULL, command_key TEXT NOT NULL, warning_id TEXT NOT NULL,
  body_json TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (actor_ref, command_key)) STRICT;

-- Mission events of the types an answer condition looks back at (3.5 `answer`, 3.11).
CREATE TABLE rules_mission_event (
  event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, label TEXT NOT NULL, occurred_at TEXT NOT NULL,
  occurred_corrected_at TEXT, recorded_at TEXT NOT NULL, origin_mode TEXT NOT NULL,
  environment_id TEXT NOT NULL, person_ids_json TEXT NOT NULL, location TEXT) STRICT;
CREATE INDEX rules_mission_event_type ON rules_mission_event(event_type, occurred_at);

-- Last known ground link and supply cover, versioned by observation instant so an older delivery never
-- replaces a newer one.
CREATE TABLE rules_fact (
  fact_key TEXT PRIMARY KEY, observed_at TEXT NOT NULL, body_json TEXT NOT NULL) STRICT;
