CREATE TABLE episodes_episode (
 episode_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 origin_mode TEXT NOT NULL CHECK(origin_mode IN ('LIVE','REPLAY','SIM')),
 concern_text TEXT NOT NULL CHECK(length(trim(concern_text))>0),
 onset_at TEXT, onset_precision TEXT NOT NULL CHECK(onset_precision IN ('exact','hour','day','unknown')),
 state TEXT NOT NULL CHECK(state IN ('draft','open','awaiting_information','awaiting_review','followup','resolved')),
 revision INTEGER NOT NULL CHECK(revision>=1), created_at TEXT NOT NULL, created_by TEXT NOT NULL,
 questionnaire_version TEXT NOT NULL, red_flag_ids_json BLOB NOT NULL, route_module_id TEXT,
 config_release_id TEXT NOT NULL, last_resolution_id TEXT,
 CHECK((onset_at IS NULL)=(onset_precision='unknown'))
) STRICT;
CREATE INDEX episodes_patient_state ON episodes_episode(patient_id,state,episode_id);
CREATE TABLE episodes_change (
 episode_id TEXT NOT NULL REFERENCES episodes_episode, revision INTEGER NOT NULL, kind TEXT NOT NULL,
 actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL, at TEXT NOT NULL, from_state TEXT NOT NULL,to_state TEXT NOT NULL,cause_ref TEXT,
 PRIMARY KEY(episode_id,revision)
) STRICT;
CREATE TABLE episodes_link (
 kind TEXT NOT NULL CHECK(kind IN ('measurement_request','capability_gap','assessment','help_session','plan_run','guided_check','handoff','observation')),
 ref_id TEXT NOT NULL,episode_id TEXT NOT NULL REFERENCES episodes_episode,ref_state TEXT,
 origin_mode TEXT NOT NULL,linked_at TEXT NOT NULL,updated_at TEXT NOT NULL, PRIMARY KEY(kind,ref_id)
) STRICT;
CREATE INDEX episodes_link_episode ON episodes_link(episode_id,kind,ref_state);
CREATE TABLE episodes_task (
 task_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,kind TEXT NOT NULL CHECK(kind IN ('review','review_item','custom')),
 owner_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('open','done','cancelled')),
 revision INTEGER NOT NULL CHECK(revision>0),due_at TEXT, data BLOB NOT NULL
) STRICT;
CREATE UNIQUE INDEX episodes_one_review ON episodes_task(episode_id) WHERE kind='review' AND state='open';
CREATE INDEX episodes_due ON episodes_task(state,due_at,task_id);
CREATE TABLE episodes_task_event(task_id TEXT NOT NULL REFERENCES episodes_task,seq INTEGER NOT NULL,kind TEXT NOT NULL,at TEXT NOT NULL,actor_id TEXT NOT NULL,data BLOB NOT NULL,PRIMARY KEY(task_id,seq)) STRICT;
CREATE TABLE episodes_task_source(task_id TEXT NOT NULL REFERENCES episodes_task,source_kind TEXT NOT NULL,ref_id TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(task_id,source_kind,ref_id)) STRICT;
CREATE TABLE episodes_task_overdue(task_id TEXT PRIMARY KEY REFERENCES episodes_task,due_at TEXT NOT NULL,noticed_at TEXT NOT NULL) STRICT;
CREATE TABLE episodes_review(review_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,reviewer_id TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('crew_medical_officer','ground')),signed_at TEXT NOT NULL,episode_revision_seen INTEGER NOT NULL,data BLOB NOT NULL) STRICT;
CREATE TABLE episodes_ack(ack_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,data BLOB NOT NULL) STRICT;
CREATE TABLE episodes_resolution(resolution_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,episode_revision_seen INTEGER NOT NULL,data BLOB NOT NULL) STRICT;
CREATE TABLE episodes_reopen(reopen_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,resolution_id TEXT NOT NULL UNIQUE,data BLOB NOT NULL) STRICT;
CREATE TABLE episodes_onset(episode_id TEXT NOT NULL REFERENCES episodes_episode,seq INTEGER NOT NULL, data BLOB NOT NULL,PRIMARY KEY(episode_id,seq)) STRICT;
CREATE TABLE episodes_timeline(entry_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,seq INTEGER NOT NULL,kind TEXT NOT NULL,ref_id TEXT NOT NULL,ref_state TEXT,occurred_at TEXT NOT NULL,recorded_at TEXT NOT NULL,actor_kind TEXT,actor_id TEXT,source TEXT NOT NULL,dedupe_key TEXT NOT NULL UNIQUE,UNIQUE(episode_id,seq)) STRICT;
CREATE TABLE episodes_effect(effect_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL REFERENCES episodes_episode,record_id TEXT NOT NULL,superseded_id TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('due','done','failed')),attempts INTEGER NOT NULL,next_at TEXT NOT NULL,last_error_code TEXT) STRICT;
CREATE INDEX episodes_effect_due ON episodes_effect(state,next_at,effect_id);
CREATE TRIGGER episodes_update BEFORE UPDATE ON episodes_episode BEGIN
 SELECT CASE WHEN NEW.revision<>OLD.revision+1 OR NEW.patient_id<>OLD.patient_id OR NEW.mission_id<>OLD.mission_id OR NEW.origin_mode<>OLD.origin_mode OR NEW.concern_text<>OLD.concern_text OR NEW.created_at<>OLD.created_at OR NEW.red_flag_ids_json<>OLD.red_flag_ids_json THEN RAISE(ABORT,'immutable episode identity or revision') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM episodes_change WHERE episode_id=OLD.episode_id AND revision=NEW.revision AND to_state=NEW.state) THEN RAISE(ABORT,'missing change') END;
 SELECT CASE WHEN NEW.state='resolved' AND OLD.state<>'resolved' AND NOT EXISTS(SELECT 1 FROM episodes_resolution WHERE episode_id=OLD.episode_id AND episode_revision_seen=OLD.revision) THEN RAISE(ABORT,'resolution required') END;
 SELECT CASE WHEN OLD.state='resolved' AND NEW.state<>'resolved' AND NOT EXISTS(SELECT 1 FROM episodes_reopen WHERE episode_id=OLD.episode_id AND resolution_id=OLD.last_resolution_id) THEN RAISE(ABORT,'reopening required') END;
END;
CREATE TRIGGER episodes_task_update BEFORE UPDATE ON episodes_task BEGIN
 SELECT CASE WHEN OLD.state<>'open' OR NEW.revision<>OLD.revision+1 OR NEW.episode_id<>OLD.episode_id OR NEW.kind<>OLD.kind OR NOT EXISTS(SELECT 1 FROM episodes_task_event WHERE task_id=OLD.task_id AND seq=NEW.revision) THEN RAISE(ABORT,'invalid task transition') END;
END;
CREATE TRIGGER episodes_change_no_delete BEFORE DELETE ON episodes_change BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_event_no_delete BEFORE DELETE ON episodes_task_event BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_source_no_delete BEFORE DELETE ON episodes_task_source BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_overdue_no_delete BEFORE DELETE ON episodes_task_overdue BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_review_no_delete BEFORE DELETE ON episodes_review BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_ack_no_delete BEFORE DELETE ON episodes_ack BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_resolution_no_delete BEFORE DELETE ON episodes_resolution BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_reopen_no_delete BEFORE DELETE ON episodes_reopen BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_onset_no_delete BEFORE DELETE ON episodes_onset BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_timeline_no_delete BEFORE DELETE ON episodes_timeline BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_episode_no_delete BEFORE DELETE ON episodes_episode BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_link_no_delete BEFORE DELETE ON episodes_link BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_no_delete BEFORE DELETE ON episodes_task BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_change_no_update BEFORE UPDATE ON episodes_change BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_event_no_update BEFORE UPDATE ON episodes_task_event BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_source_no_update BEFORE UPDATE ON episodes_task_source BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_task_overdue_no_update BEFORE UPDATE ON episodes_task_overdue BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_review_no_update BEFORE UPDATE ON episodes_review BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_ack_no_update BEFORE UPDATE ON episodes_ack BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_resolution_no_update BEFORE UPDATE ON episodes_resolution BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_reopen_no_update BEFORE UPDATE ON episodes_reopen BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_onset_no_update BEFORE UPDATE ON episodes_onset BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER episodes_timeline_no_update BEFORE UPDATE ON episodes_timeline BEGIN SELECT RAISE(ABORT,'append only'); END;
