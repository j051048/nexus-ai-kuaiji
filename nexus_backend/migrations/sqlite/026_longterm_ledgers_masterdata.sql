-- Existing monthly book identities and all evidence foreign keys stay intact.
CREATE TABLE ledgers (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,name TEXT NOT NULL,
 created_by TEXT REFERENCES users(id),created_at TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(id,firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
CREATE TABLE ledger_managers (
 ledger_id TEXT NOT NULL REFERENCES ledgers(id),user_id TEXT NOT NULL REFERENCES users(id),
 granted_by TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,user_id)
);
CREATE TABLE ledger_periods (
 ledger_id TEXT NOT NULL,book_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,
 linked_by TEXT REFERENCES users(id),linked_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,period),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES ledgers(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE ledger_commands (
 ledger_id TEXT NOT NULL REFERENCES ledgers(id),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES books(id),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,request_key)
);
CREATE TABLE ledger_rule_candidates (
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL REFERENCES ledgers(id),source_book_id TEXT NOT NULL REFERENCES books(id),
 target_book_id TEXT NOT NULL REFERENCES books(id),source_rule_id TEXT NOT NULL,source_rule_version INTEGER NOT NULL,
 definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','evaluated')),
 target_rule_id TEXT REFERENCES learning_rules(id),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(target_book_id,source_rule_id,source_rule_version),
 FOREIGN KEY(source_rule_id,source_rule_version) REFERENCES learning_rule_versions(rule_id,version)
);
CREATE TABLE company_master_entities (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('counterparty','project','bank_account')),
 code TEXT NOT NULL,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 recorded_book_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(firm_id,company_id,kind,code),UNIQUE(id,firm_id,company_id,kind),
 FOREIGN KEY(recorded_book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TABLE company_master_aliases (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,kind TEXT NOT NULL,entity_id TEXT NOT NULL,
 original TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','retired')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),recorded_book_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,reviewed_by TEXT REFERENCES users(id),review_reason TEXT,reviewed_at TEXT,
 FOREIGN KEY(entity_id,firm_id,company_id,kind) REFERENCES company_master_entities(id,firm_id,company_id,kind),
 FOREIGN KEY(recorded_book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 CHECK((status='pending' AND reviewed_by IS NULL) OR (status<>'pending' AND reviewed_by IS NOT NULL AND reviewed_by<>created_by))
);
CREATE UNIQUE INDEX idx_master_approved_alias ON company_master_aliases(firm_id,company_id,kind,original) WHERE status='approved';
CREATE INDEX idx_master_company ON company_master_entities(firm_id,company_id,kind,active);
CREATE TABLE company_master_history (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),
 object_id TEXT NOT NULL,action TEXT NOT NULL,value_json TEXT NOT NULL CHECK(json_valid(value_json)),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
INSERT INTO ledgers(id,firm_id,company_id,name,created_at) SELECT 'ledger_'||id,firm_id,company_id,name,CURRENT_TIMESTAMP FROM books;
INSERT INTO ledger_periods(ledger_id,book_id,firm_id,company_id,period,linked_at)
 SELECT 'ledger_'||id,id,firm_id,company_id,period,CURRENT_TIMESTAMP FROM books;
CREATE TRIGGER ledger_period_scope_insert BEFORE INSERT ON ledger_periods BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books WHERE id=NEW.book_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id AND period=NEW.period)
 THEN RAISE(ABORT,'period mapping requires canonical book') END; END;
CREATE TRIGGER ledger_period_scope_update BEFORE UPDATE ON ledger_periods BEGIN
 SELECT CASE WHEN NEW.book_id<>OLD.book_id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.period<>OLD.period
 THEN RAISE(ABORT,'physical period identity is immutable') END; END;
CREATE TRIGGER ledger_identity_update BEFORE UPDATE ON ledgers BEGIN
 SELECT CASE WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.created_at<>OLD.created_at
 THEN RAISE(ABORT,'ledger identity is immutable') END; END;
CREATE TRIGGER master_entity_update BEFORE UPDATE ON company_master_entities BEGIN
 SELECT CASE WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.kind<>OLD.kind OR NEW.code<>OLD.code
 OR NEW.created_by<>OLD.created_by OR NEW.recorded_book_id<>OLD.recorded_book_id OR NEW.version<>OLD.version+1
 THEN RAISE(ABORT,'master identity or version is immutable') END; END;
CREATE TRIGGER master_alias_update BEFORE UPDATE ON company_master_aliases BEGIN
 SELECT CASE WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.entity_id<>OLD.entity_id
 OR NEW.kind<>OLD.kind OR NEW.original<>OLD.original OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at
 OR NEW.recorded_book_id<>OLD.recorded_book_id OR NEW.reason<>OLD.reason OR NEW.version<>OLD.version+1
 OR NOT ((OLD.status='pending' AND NEW.status IN ('approved','rejected')) OR (OLD.status='approved' AND NEW.status='retired'))
 THEN RAISE(ABORT,'alias identity and review history are immutable') END; END;
CREATE TRIGGER ledger_commands_update BEFORE UPDATE ON ledger_commands BEGIN SELECT RAISE(ABORT,'ledger command receipt is immutable'); END;
CREATE TRIGGER ledger_commands_delete BEFORE DELETE ON ledger_commands BEGIN SELECT RAISE(ABORT,'ledger command receipt is immutable'); END;
CREATE TRIGGER master_history_update BEFORE UPDATE ON company_master_history BEGIN SELECT RAISE(ABORT,'master history is immutable'); END;
CREATE TRIGGER master_history_delete BEFORE DELETE ON company_master_history BEGIN SELECT RAISE(ABORT,'master history is immutable'); END;
CREATE TRIGGER master_alias_delete BEFORE DELETE ON company_master_aliases BEGIN SELECT RAISE(ABORT,'alias history is immutable'); END;
CREATE TRIGGER master_alias_insert BEFORE INSERT ON company_master_aliases BEGIN
 SELECT CASE WHEN NEW.status<>'pending' OR NEW.version<>1 OR NEW.reviewed_by IS NOT NULL
 THEN RAISE(ABORT,'alias requires independent review') END; END;
CREATE TRIGGER master_entity_delete BEFORE DELETE ON company_master_entities BEGIN SELECT RAISE(ABORT,'master history is immutable'); END;
CREATE TRIGGER ledger_candidate_insert BEFORE INSERT ON ledger_rule_candidates BEGIN
 SELECT CASE WHEN NEW.status<>'pending' OR NEW.target_rule_id IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM ledger_periods WHERE ledger_id=NEW.ledger_id AND book_id=NEW.source_book_id)
 OR NOT EXISTS(SELECT 1 FROM ledger_periods WHERE ledger_id=NEW.ledger_id AND book_id=NEW.target_book_id)
 OR NOT EXISTS(SELECT 1 FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.id=NEW.source_rule_id
 AND r.book_id=NEW.source_book_id AND r.status='published' AND r.current_version=NEW.source_rule_version AND json(v.definition_json)=json(NEW.definition_json))
 THEN RAISE(ABORT,'inheritance requires genuine published source definition and same ledger') END; END;
CREATE TRIGGER ledger_candidate_update BEFORE UPDATE ON ledger_rule_candidates BEGIN
 SELECT CASE WHEN NEW.id<>OLD.id OR NEW.ledger_id<>OLD.ledger_id OR NEW.source_book_id<>OLD.source_book_id OR NEW.target_book_id<>OLD.target_book_id
 OR NEW.source_rule_id<>OLD.source_rule_id OR NEW.source_rule_version<>OLD.source_rule_version OR NEW.definition_json<>OLD.definition_json
 OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at OR OLD.status<>'pending' OR NEW.status<>'evaluated' OR NEW.target_rule_id IS NULL
 THEN RAISE(ABORT,'inherited rule provenance is immutable') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.id=NEW.target_rule_id
 AND r.book_id=NEW.target_book_id AND r.status='draft' AND json(v.definition_json)=json(NEW.definition_json))
 THEN RAISE(ABORT,'inherited rule requires genuine target draft definition') END; END;
CREATE TRIGGER ledger_candidate_delete BEFORE DELETE ON ledger_rule_candidates BEGIN SELECT RAISE(ABORT,'rule candidate history is immutable'); END;
