-- Routine (E25 layer B; SYSTEM 4.2 routine-owned tables).
-- STRICT tables; instants are RFC 3339 UTC text with Z; transitions are append only.

CREATE TABLE routine_occurrence (
    occurrence_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision >= 1),
    person_id TEXT NOT NULL, item_id TEXT NOT NULL, environment_id TEXT NOT NULL,
    mission_day INTEGER NOT NULL CHECK (mission_day >= 1),
    cause_kind TEXT NOT NULL CHECK (cause_kind IN ('cadence','override','per_eva','cadence_event','assistant','person')),
    cause_key TEXT NOT NULL, periodic INTEGER NOT NULL CHECK (periodic IN (0,1)),
    override_id TEXT, binding_id TEXT, started_by TEXT, insight_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('not_due','due','later','done','skipped','missed')),
    due_from TEXT, base_due_until TEXT, due_until TEXT,
    due_kind TEXT NOT NULL CHECK (due_kind IN ('specific','all_day')), later_until TEXT,
    completed_at TEXT, outcome_json TEXT CHECK (outcome_json IS NULL OR json_valid(outcome_json)),
    skip_json TEXT CHECK (skip_json IS NULL OR json_valid(skip_json)),
    miss_counted INTEGER CHECK (miss_counted IS NULL OR miss_counted IN (0,1)),
    end_reason TEXT, guidance_release_id TEXT, config_release_id TEXT NOT NULL,
    origin_mode TEXT NOT NULL CHECK (origin_mode IN ('LIVE','REPLAY','SIM')),
    clock_version TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (person_id,item_id,mission_day,cause_key),
    CHECK ((state='done')=(completed_at IS NOT NULL)),
    CHECK ((state='done')=(outcome_json IS NOT NULL)),
    CHECK ((state='skipped')=(skip_json IS NOT NULL)),
    CHECK ((state='missed')=(miss_counted IS NOT NULL)),
    CHECK (state NOT IN ('skipped','missed') OR outcome_json IS NULL),
    CHECK (periodic=0 OR cause_kind IN ('cadence','override')),
    CHECK (cause_kind<>'assistant' OR (insight_id IS NOT NULL AND length(insight_id)>0 AND periodic=0)),
    CHECK (due_until IS NULL OR base_due_until IS NULL OR julianday(due_until)>=julianday(base_due_until))
) STRICT;
CREATE INDEX routine_occ_open ON routine_occurrence(person_id,state) WHERE state IN ('not_due','due','later');
CREATE INDEX routine_occ_day ON routine_occurrence(person_id,mission_day);
CREATE INDEX routine_occ_periodic ON routine_occurrence(person_id,item_id,mission_day);
CREATE UNIQUE INDEX routine_occ_insight ON routine_occurrence(insight_id) WHERE cause_kind='assistant';
CREATE TRIGGER routine_occ_final BEFORE UPDATE ON routine_occurrence
WHEN OLD.state IN ('done','skipped','missed') OR NEW.revision<>OLD.revision+1
 OR NEW.person_id<>OLD.person_id OR NEW.item_id<>OLD.item_id OR NEW.mission_day<>OLD.mission_day
 OR NEW.cause_key<>OLD.cause_key OR NEW.created_at<>OLD.created_at
BEGIN SELECT RAISE(ABORT,'routine_occurrence final or immutable field'); END;
CREATE TRIGGER routine_occ_no_delete BEFORE DELETE ON routine_occurrence
BEGIN SELECT RAISE(ABORT,'routine_occurrence is never deleted'); END;

CREATE TABLE routine_occurrence_transition (
    occurrence_id TEXT NOT NULL REFERENCES routine_occurrence(occurrence_id), revision INTEGER NOT NULL CHECK (revision>=1),
    from_state TEXT CHECK (from_state IS NULL OR from_state IN ('not_due','due','later','done','skipped','missed')),
    to_state TEXT NOT NULL CHECK (to_state IN ('not_due','due','later','done','skipped','missed')),
    at TEXT NOT NULL, actor_kind TEXT NOT NULL CHECK (actor_kind IN ('person','station','ground','operator','adapter','engine','assistant')),
    actor_id TEXT NOT NULL, reason_code TEXT, miss_counted INTEGER CHECK (miss_counted IS NULL OR miss_counted IN (0,1)),
    PRIMARY KEY (occurrence_id,revision)
) STRICT;
CREATE TRIGGER routine_transition_no_update BEFORE UPDATE ON routine_occurrence_transition
BEGIN SELECT RAISE(ABORT,'routine_occurrence_transition is append only'); END;
CREATE TRIGGER routine_transition_no_delete BEFORE DELETE ON routine_occurrence_transition
BEGIN SELECT RAISE(ABORT,'routine_occurrence_transition is append only'); END;

CREATE TABLE routine_override (
    override_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision>=1), item_id TEXT NOT NULL,
    person_id TEXT, audience TEXT NOT NULL CHECK (audience IN ('person','everyone_in_location','all_crew')),
    binding_id TEXT, trigger_index INTEGER NOT NULL CHECK (trigger_index>=0), trigger_text TEXT NOT NULL,
    started_by TEXT NOT NULL CHECK (started_by IN ('rule','sensor_limit','person')),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('warning','mission_event','overlay','observation','answer','person')),
    source_ref TEXT NOT NULL, new_cadence_json TEXT NOT NULL CHECK (json_valid(new_cadence_json)),
    cadence_text TEXT NOT NULL, reason_text TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('active','ended')),
    started_at TEXT NOT NULL, ends_kind TEXT NOT NULL CHECK (ends_kind IN ('warning_cleared','effect_complete','days','person_stops','mission_event')),
    ends_at TEXT, ends_text TEXT NOT NULL, stop_role TEXT, ended_at TEXT,
    end_reason TEXT CHECK (end_reason IS NULL OR end_reason IN ('warning_cleared','effect_complete','days_elapsed','stopped_by_person','mission_event','environment_changed')),
    basis TEXT NOT NULL, source_ids_json TEXT NOT NULL CHECK (json_valid(source_ids_json)), config_release_id TEXT NOT NULL,
    member_ids_json TEXT NOT NULL CHECK (json_valid(member_ids_json)), member_basis_json TEXT NOT NULL CHECK (json_valid(member_basis_json)),
    source_origin_mode TEXT NOT NULL CHECK (source_origin_mode IN ('LIVE','REPLAY','SIM')), source_at TEXT NOT NULL,
    source_chain_json TEXT NOT NULL CHECK (json_valid(source_chain_json)), effect_json TEXT NOT NULL CHECK (json_valid(effect_json)),
    awaiting_disposition INTEGER NOT NULL CHECK (awaiting_disposition IN (0,1)), note TEXT,
    CHECK ((state='ended')=(ended_at IS NOT NULL AND end_reason IS NOT NULL)),
    CHECK (audience<>'person' OR person_id IS NOT NULL)
) STRICT;
CREATE INDEX routine_ovr_active ON routine_override(state,item_id);
CREATE INDEX routine_ovr_source ON routine_override(source_kind,source_ref);
CREATE TRIGGER routine_ovr_final BEFORE UPDATE ON routine_override WHEN OLD.state='ended'
BEGIN SELECT RAISE(ABORT,'ended routine_override is immutable'); END;
CREATE TRIGGER routine_ovr_no_delete BEFORE DELETE ON routine_override
BEGIN SELECT RAISE(ABORT,'routine_override is never deleted'); END;
CREATE TABLE routine_override_member (
    override_id TEXT NOT NULL REFERENCES routine_override(override_id), person_id TEXT NOT NULL,
    member_basis TEXT NOT NULL CHECK (member_basis IN ('location','location_unknown','site','subject')),
    PRIMARY KEY (override_id,person_id)
) STRICT;
CREATE INDEX routine_member_person ON routine_override_member(person_id);
CREATE TABLE routine_override_added (
    override_id TEXT NOT NULL REFERENCES routine_override(override_id),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('occurrence','instance')), target_id TEXT NOT NULL,
    PRIMARY KEY (override_id,target_kind,target_id)
) STRICT;
CREATE INDEX routine_added_target ON routine_override_added(target_id);
CREATE TABLE routine_question_instance (
    instance_id TEXT PRIMARY KEY, override_id TEXT NOT NULL REFERENCES routine_override(override_id), person_id TEXT NOT NULL,
    question_set_id TEXT NOT NULL, slot TEXT NOT NULL, question_ids_json TEXT NOT NULL CHECK (json_valid(question_ids_json)),
    due_from TEXT NOT NULL, due_until TEXT NOT NULL, answers_json TEXT NOT NULL CHECK (json_valid(answers_json)),
    state TEXT NOT NULL CHECK (state IN ('open','answered','partially_answered','unanswered')), revision INTEGER NOT NULL CHECK (revision>=1),
    carrier_day INTEGER NOT NULL CHECK (carrier_day>=1), carrier_occurrence_id TEXT REFERENCES routine_occurrence(occurrence_id),
    UNIQUE (override_id,person_id,question_set_id,slot), CHECK (julianday(due_until)>julianday(due_from))
) STRICT;
CREATE INDEX routine_instance_open ON routine_question_instance(person_id,state) WHERE state='open';
CREATE TABLE routine_overlay (
    overlay_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision>=1), person_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('eva','post_eva','exercise','sleep')),
    state TEXT NOT NULL CHECK (state IN ('planned','active','ended')), planned_start TEXT, started_at TEXT, ended_at TEXT,
    CHECK (ended_at IS NULL OR started_at IS NULL OR julianday(ended_at)>=julianday(started_at))
) STRICT;
CREATE INDEX routine_overlay_person ON routine_overlay(person_id,started_at);
CREATE TABLE routine_deadline (deadline_key TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK (generation>=1)) STRICT;
CREATE TABLE routine_clock_state (id INTEGER PRIMARY KEY CHECK (id=1), suspect_since TEXT) STRICT;
CREATE TABLE routine_barrier_ask (
    person_id TEXT NOT NULL, offered_day INTEGER NOT NULL CHECK (offered_day>=1), item_ids_json TEXT NOT NULL CHECK (json_valid(item_ids_json)),
    outcome TEXT NOT NULL CHECK (outcome IN ('pending','answered','shown_unanswered')), answer_id TEXT, at TEXT NOT NULL,
    PRIMARY KEY (person_id,offered_day)
) STRICT;

CREATE TABLE routine_cadence_activation (
    activation_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK (revision>=1),
    binding_id TEXT NOT NULL, item_id TEXT NOT NULL, person_id TEXT NOT NULL,
    cadence_json TEXT NOT NULL CHECK (json_valid(cadence_json)), started_at TEXT NOT NULL,
    ends_kind TEXT NOT NULL CHECK (ends_kind IN ('days','mission_event','warning_cleared')),
    ends_at TEXT, state TEXT NOT NULL CHECK (state IN ('active','ended')), ended_at TEXT,
    end_reason TEXT CHECK (end_reason IS NULL OR end_reason IN ('days_elapsed','mission_event','warning_cleared','environment_changed')),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('warning','mission_event','overlay','observation','answer','person')),
    source_ref TEXT NOT NULL, source_at TEXT NOT NULL,
    source_origin_mode TEXT NOT NULL CHECK (source_origin_mode IN ('LIVE','REPLAY','SIM')),
    config_release_id TEXT NOT NULL,
    CHECK ((state='ended')=(ended_at IS NOT NULL AND end_reason IS NOT NULL)),
    CHECK ((ends_kind='days')=(ends_at IS NOT NULL))
) STRICT;
CREATE INDEX routine_rca_person ON routine_cadence_activation(person_id,state,item_id);
CREATE TRIGGER routine_rca_final BEFORE UPDATE ON routine_cadence_activation
WHEN OLD.state='ended' OR NEW.revision<>OLD.revision+1
 OR NEW.activation_id<>OLD.activation_id OR NEW.binding_id<>OLD.binding_id
 OR NEW.item_id<>OLD.item_id OR NEW.person_id<>OLD.person_id
 OR NEW.started_at<>OLD.started_at OR NEW.config_release_id<>OLD.config_release_id
BEGIN SELECT RAISE(ABORT,'ended or immutable routine_cadence_activation'); END;
CREATE TRIGGER routine_rca_no_delete BEFORE DELETE ON routine_cadence_activation
BEGIN SELECT RAISE(ABORT,'routine_cadence_activation is never deleted'); END;
