CREATE TABLE assistant_conversations(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 scope_version INTEGER NOT NULL DEFAULT 1 CHECK(scope_version=1),
 book_ids_json TEXT NOT NULL CHECK(json_valid(book_ids_json) AND json_type(book_ids_json)='array' AND json_array_length(book_ids_json)<=12),
 event_ids_json TEXT NOT NULL CHECK(json_valid(event_ids_json) AND json_type(event_ids_json)='array' AND json_array_length(event_ids_json)<=20),
 draft_id TEXT,title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 120),create_key TEXT NOT NULL,create_fingerprint TEXT NOT NULL CHECK(length(create_fingerprint)=64),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,create_key),UNIQUE(id,firm_id,company_id,created_by),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 CHECK(draft_id IS NULL OR json_array_length(book_ids_json)=1)
);
CREATE TABLE assistant_turns(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'chat' CHECK(kind='chat'),provider TEXT NOT NULL,model TEXT NOT NULL,expected_scope_version INTEGER NOT NULL CHECK(expected_scope_version=1),
 question TEXT NOT NULL CHECK(length(trim(question)) BETWEEN 1 AND 4000),input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),model_fingerprint TEXT NOT NULL CHECK(length(model_fingerprint)=64),
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='object' AND length(CAST(snapshot_json AS BLOB))<=65536),
 history_json TEXT NOT NULL CHECK(json_valid(history_json) AND json_type(history_json)='array' AND json_array_length(history_json)<=12 AND length(CAST(history_json AS BLOB))<=49152),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','interrupted','stale','cancelled')),attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
 available_at TEXT NOT NULL,sent_at TEXT,lease_token TEXT,lease_until TEXT,worker_id TEXT,error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',reply_json TEXT,
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(conversation_id,request_key),UNIQUE(id,firm_id,company_id,created_by),FOREIGN KEY(conversation_id,firm_id,company_id,created_by) REFERENCES assistant_conversations(id,firm_id,company_id,created_by),
 CHECK(reply_json IS NULL OR (json_valid(reply_json) AND json_type(reply_json)='object')),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
 OR (status='queued' AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL AND sent_at IS NULL)
 OR (status IN ('succeeded','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL)),
 CHECK((status='succeeded' AND reply_json IS NOT NULL AND sent_at IS NOT NULL) OR (status<>'succeeded' AND reply_json IS NULL))
);
CREATE UNIQUE INDEX idx_assistant_one_active ON assistant_turns(conversation_id) WHERE status IN ('queued','running');
CREATE INDEX idx_assistant_dispatch ON assistant_turns(status,available_at,firm_id,company_id,created_at,id);
CREATE TABLE assistant_messages(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES assistant_conversations(id),turn_id TEXT NOT NULL REFERENCES assistant_turns(id),
 role TEXT NOT NULL CHECK(role IN ('user','assistant')),content_json TEXT NOT NULL CHECK(json_valid(content_json) AND json_type(content_json)='object'),created_at TEXT NOT NULL,
 UNIQUE(turn_id,role)
);
CREATE INDEX idx_assistant_messages_page ON assistant_messages(conversation_id,created_at DESC,id);
CREATE INDEX idx_assistant_conversations_page ON assistant_conversations(firm_id,company_id,created_by,updated_at DESC,id);
CREATE TABLE assistant_company_schedule(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_dispatch_seq INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id));
CREATE TRIGGER assistant_conversations_immutable BEFORE UPDATE ON assistant_conversations BEGIN SELECT RAISE(ABORT,'Conversation scope is immutable');END;
CREATE TRIGGER assistant_conversations_no_delete BEFORE DELETE ON assistant_conversations BEGIN SELECT RAISE(ABORT,'Conversation scope is immutable');END;
CREATE TRIGGER assistant_messages_immutable BEFORE UPDATE ON assistant_messages BEGIN SELECT RAISE(ABORT,'Conversation messages are immutable');END;
CREATE TRIGGER assistant_messages_no_delete BEFORE DELETE ON assistant_messages BEGIN SELECT RAISE(ABORT,'Conversation messages are immutable');END;
CREATE TRIGGER assistant_turn_identity BEFORE UPDATE ON assistant_turns WHEN
 NEW.id<>OLD.id OR NEW.conversation_id<>OLD.conversation_id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.created_by<>OLD.created_by OR NEW.kind<>OLD.kind OR NEW.provider<>OLD.provider OR NEW.model<>OLD.model OR NEW.expected_scope_version<>OLD.expected_scope_version OR NEW.question<>OLD.question OR NEW.input_fingerprint<>OLD.input_fingerprint OR NEW.model_fingerprint<>OLD.model_fingerprint OR NEW.snapshot_json<>OLD.snapshot_json OR NEW.history_json<>OLD.history_json OR NEW.request_key<>OLD.request_key OR NEW.request_fingerprint<>OLD.request_fingerprint OR NEW.created_at<>OLD.created_at OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS NOT OLD.sent_at)
 BEGIN SELECT RAISE(ABORT,'Turn input and send marker are immutable');END;
CREATE TRIGGER assistant_turn_no_delete BEFORE DELETE ON assistant_turns BEGIN SELECT RAISE(ABORT,'Conversation history cannot be deleted');END;
