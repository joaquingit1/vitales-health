CREATE TABLE assistant_conversation (
 conversation_id TEXT PRIMARY KEY, person_id TEXT NOT NULL,
 context_kind TEXT NOT NULL CHECK(context_kind IN ('general','episode','help_session','warning','reading','insight')),
 context_ref_id TEXT, state TEXT NOT NULL CHECK(state IN ('open','closed')),
 revision INTEGER NOT NULL CHECK(revision>=1), body BLOB NOT NULL CHECK(json_valid(body))
) STRICT;
CREATE UNIQUE INDEX assistant_open_context ON assistant_conversation(person_id,context_kind,ifnull(context_ref_id,'')) WHERE state='open';
CREATE TABLE assistant_message (
 message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES assistant_conversation(conversation_id),
 seq INTEGER NOT NULL CHECK(seq>=1), body BLOB NOT NULL CHECK(json_valid(body)), UNIQUE(conversation_id,seq)
) STRICT;
CREATE TABLE assistant_turn (
 turn_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE,
 conversation_id TEXT REFERENCES assistant_conversation(conversation_id), episode_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('conversation','assessment','help','insight')),
 state TEXT NOT NULL CHECK(state IN ('queued','running','committing','completed','cancelled','timed_out','rejected','failed','stale')),
 priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 2), created_at TEXT NOT NULL,
 continuation_key TEXT UNIQUE, body BLOB NOT NULL CHECK(json_valid(body))
) STRICT;
CREATE UNIQUE INDEX assistant_one_active_turn ON assistant_turn(conversation_id) WHERE conversation_id IS NOT NULL AND state IN ('queued','running','committing');
CREATE UNIQUE INDEX assistant_one_active_assessment ON assistant_turn(episode_id) WHERE kind='assessment' AND state IN ('queued','running','committing');
CREATE INDEX assistant_pending_turn ON assistant_turn(state,priority,created_at,turn_id);
CREATE TABLE assistant_turn_history (
 turn_id TEXT NOT NULL REFERENCES assistant_turn(turn_id), seq INTEGER NOT NULL CHECK(seq>=1),
 state TEXT NOT NULL, at TEXT NOT NULL, body BLOB NOT NULL CHECK(json_valid(body)), PRIMARY KEY(turn_id,seq)
) STRICT;
CREATE TABLE assistant_runtime_state (id INTEGER PRIMARY KEY CHECK(id=1), body BLOB NOT NULL CHECK(json_valid(body))) STRICT;
CREATE TABLE assistant_runtime_history (seq INTEGER PRIMARY KEY, state TEXT NOT NULL, at TEXT NOT NULL, body BLOB NOT NULL CHECK(json_valid(body))) STRICT;
CREATE TABLE assistant_assessment (
 assessment_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, person_id TEXT NOT NULL,
 body BLOB NOT NULL CHECK(json_valid(body)), completeness TEXT NOT NULL CHECK(completeness IN ('complete','incomplete')),
 stop_reason TEXT, config_release_id TEXT NOT NULL, turn_id TEXT NOT NULL, job_id TEXT NOT NULL,
 created_at TEXT NOT NULL, validation_status TEXT NOT NULL CHECK(validation_status IN ('draft','stale','reviewed')),
 cause_ref TEXT, review_id TEXT
) STRICT;
CREATE INDEX assistant_assessment_episode ON assistant_assessment(episode_id,created_at,assessment_id);
CREATE TABLE assistant_claim_ref (
 assessment_id TEXT NOT NULL REFERENCES assistant_assessment(assessment_id), section TEXT NOT NULL CHECK(section IN ('finding','hypothesis','contradiction')),
 item_index INTEGER NOT NULL CHECK(item_index>=0), kind TEXT NOT NULL CHECK(kind IN ('observation','answer','daily_check','passage')),
 ref_id TEXT NOT NULL, PRIMARY KEY(assessment_id,section,item_index,kind,ref_id)
) STRICT;
CREATE INDEX assistant_claim_ref_lookup ON assistant_claim_ref(ref_id);
CREATE TABLE assistant_assessment_history (
 assessment_id TEXT NOT NULL REFERENCES assistant_assessment(assessment_id), seq INTEGER NOT NULL CHECK(seq>=1),
 state TEXT NOT NULL, cause_ref TEXT, review_id TEXT, at TEXT NOT NULL, PRIMARY KEY(assessment_id,seq)
) STRICT;
CREATE TABLE assistant_tool_call (
 turn_id TEXT NOT NULL REFERENCES assistant_turn(turn_id), tool_call_id TEXT NOT NULL,
 body BLOB NOT NULL CHECK(json_valid(body)), PRIMARY KEY(turn_id,tool_call_id)
) STRICT;
CREATE TABLE assistant_applied_effect (
 turn_id TEXT NOT NULL REFERENCES assistant_turn(turn_id), tool_call_id TEXT NOT NULL,
 body BLOB NOT NULL CHECK(json_valid(body)), PRIMARY KEY(turn_id,tool_call_id)
) STRICT;
CREATE TABLE assistant_insight (
 insight_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, topic_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','dismissed','expired')), revision INTEGER NOT NULL CHECK(revision>=1),
 created_at TEXT NOT NULL, body BLOB NOT NULL CHECK(json_valid(body))
) STRICT;
CREATE UNIQUE INDEX assistant_active_insight ON assistant_insight(person_id,topic_id) WHERE state='active';
CREATE TABLE assistant_insight_history (insight_id TEXT NOT NULL REFERENCES assistant_insight(insight_id),seq INTEGER NOT NULL,state TEXT NOT NULL,at TEXT NOT NULL,PRIMARY KEY(insight_id,seq)) STRICT;
CREATE TRIGGER assistant_message_no_update BEFORE UPDATE ON assistant_message BEGIN SELECT RAISE(ABORT,'immutable assistant message'); END;
CREATE TRIGGER assistant_message_no_delete BEFORE DELETE ON assistant_message BEGIN SELECT RAISE(ABORT,'immutable assistant message'); END;
CREATE TRIGGER assistant_turn_no_delete BEFORE DELETE ON assistant_turn BEGIN SELECT RAISE(ABORT,'immutable assistant turn'); END;
CREATE TRIGGER assistant_turn_identity BEFORE UPDATE ON assistant_turn WHEN NEW.turn_id IS NOT OLD.turn_id OR NEW.task_id IS NOT OLD.task_id OR NEW.conversation_id IS NOT OLD.conversation_id OR NEW.episode_id IS NOT OLD.episode_id OR NEW.kind IS NOT OLD.kind OR NEW.priority IS NOT OLD.priority OR NEW.created_at IS NOT OLD.created_at OR NEW.continuation_key IS NOT OLD.continuation_key BEGIN SELECT RAISE(ABORT,'immutable assistant identity'); END;
CREATE TRIGGER assistant_turn_terminal BEFORE UPDATE ON assistant_turn WHEN OLD.state IN ('completed','cancelled','timed_out','rejected','failed','stale') BEGIN SELECT RAISE(ABORT,'terminal assistant turn'); END;
CREATE TRIGGER assistant_turn_history_no_update BEFORE UPDATE ON assistant_turn_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_turn_history_no_delete BEFORE DELETE ON assistant_turn_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_runtime_history_no_update BEFORE UPDATE ON assistant_runtime_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_runtime_history_no_delete BEFORE DELETE ON assistant_runtime_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_assessment_history_no_update BEFORE UPDATE ON assistant_assessment_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_assessment_history_no_delete BEFORE DELETE ON assistant_assessment_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_assessment_no_delete BEFORE DELETE ON assistant_assessment BEGIN SELECT RAISE(ABORT,'immutable assistant assessment'); END;
CREATE TRIGGER assistant_assessment_content BEFORE UPDATE ON assistant_assessment WHEN NEW.assessment_id IS NOT OLD.assessment_id OR NEW.episode_id IS NOT OLD.episode_id OR NEW.person_id IS NOT OLD.person_id OR NEW.body IS NOT OLD.body OR NEW.completeness IS NOT OLD.completeness OR NEW.stop_reason IS NOT OLD.stop_reason OR NEW.config_release_id IS NOT OLD.config_release_id OR NEW.turn_id IS NOT OLD.turn_id OR NEW.job_id IS NOT OLD.job_id OR NEW.created_at IS NOT OLD.created_at BEGIN SELECT RAISE(ABORT,'immutable assistant assessment'); END;
CREATE TRIGGER assistant_assessment_transition BEFORE UPDATE ON assistant_assessment WHEN NOT ((OLD.validation_status='draft' AND NEW.validation_status IN ('reviewed','stale')) OR (OLD.validation_status='reviewed' AND NEW.validation_status='stale')) BEGIN SELECT RAISE(ABORT,'invalid assessment transition'); END;
CREATE TRIGGER assistant_claim_ref_no_update BEFORE UPDATE ON assistant_claim_ref BEGIN SELECT RAISE(ABORT,'immutable assistant support'); END;
CREATE TRIGGER assistant_claim_ref_no_delete BEFORE DELETE ON assistant_claim_ref BEGIN SELECT RAISE(ABORT,'immutable assistant support'); END;
CREATE TRIGGER assistant_conversation_identity BEFORE UPDATE ON assistant_conversation WHEN NEW.conversation_id IS NOT OLD.conversation_id OR NEW.person_id IS NOT OLD.person_id OR NEW.context_kind IS NOT OLD.context_kind OR NEW.context_ref_id IS NOT OLD.context_ref_id OR NEW.revision != OLD.revision+1 BEGIN SELECT RAISE(ABORT,'invalid conversation projection'); END;
CREATE TRIGGER assistant_conversation_no_delete BEFORE DELETE ON assistant_conversation BEGIN SELECT RAISE(ABORT,'immutable assistant conversation'); END;
CREATE TRIGGER assistant_tool_call_no_update BEFORE UPDATE ON assistant_tool_call BEGIN SELECT RAISE(ABORT,'immutable assistant call'); END;
CREATE TRIGGER assistant_tool_call_no_delete BEFORE DELETE ON assistant_tool_call BEGIN SELECT RAISE(ABORT,'immutable assistant call'); END;
CREATE TRIGGER assistant_effect_no_update BEFORE UPDATE ON assistant_applied_effect BEGIN SELECT RAISE(ABORT,'immutable assistant effect'); END;
CREATE TRIGGER assistant_effect_no_delete BEFORE DELETE ON assistant_applied_effect BEGIN SELECT RAISE(ABORT,'immutable assistant effect'); END;
CREATE TRIGGER assistant_insight_history_no_update BEFORE UPDATE ON assistant_insight_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
CREATE TRIGGER assistant_insight_history_no_delete BEFORE DELETE ON assistant_insight_history BEGIN SELECT RAISE(ABORT,'immutable assistant history'); END;
