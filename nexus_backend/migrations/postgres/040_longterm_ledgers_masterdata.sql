-- Existing monthly book identities and all evidence foreign keys stay intact.
CREATE TABLE accounting.ledgers (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,name TEXT NOT NULL,
 created_by TEXT REFERENCES accounting.users(id),created_at TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(id,firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE TABLE accounting.ledger_managers (
 ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),user_id TEXT NOT NULL REFERENCES accounting.users(id),
 granted_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,user_id)
);
CREATE TABLE accounting.ledger_periods (
 ledger_id TEXT NOT NULL,book_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,
 linked_by TEXT REFERENCES accounting.users(id),linked_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,period),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.ledger_commands (
 ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 book_id TEXT NOT NULL REFERENCES accounting.books(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(ledger_id,request_key)
);
CREATE TABLE accounting.ledger_rule_candidates (
 id TEXT PRIMARY KEY,ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),source_book_id TEXT NOT NULL REFERENCES accounting.books(id),
 target_book_id TEXT NOT NULL REFERENCES accounting.books(id),source_rule_id TEXT NOT NULL,source_rule_version INTEGER NOT NULL,
 definition_json TEXT NOT NULL CHECK(jsonb_typeof(definition_json::jsonb)='object'),status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','evaluated')),
 target_rule_id TEXT REFERENCES accounting.learning_rules(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(target_book_id,source_rule_id,source_rule_version),
 FOREIGN KEY(source_rule_id,source_rule_version) REFERENCES accounting.learning_rule_versions(rule_id,version)
);
CREATE TABLE accounting.company_master_entities (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('counterparty','project','bank_account')),
 code TEXT NOT NULL,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 recorded_book_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(firm_id,company_id,kind,code),UNIQUE(id,firm_id,company_id,kind),
 FOREIGN KEY(recorded_book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.company_master_aliases (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,kind TEXT NOT NULL,entity_id TEXT NOT NULL,
 original TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','retired')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),recorded_book_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,reviewed_by TEXT REFERENCES accounting.users(id),review_reason TEXT,reviewed_at TEXT,
 FOREIGN KEY(entity_id,firm_id,company_id,kind) REFERENCES accounting.company_master_entities(id,firm_id,company_id,kind),
 FOREIGN KEY(recorded_book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 CHECK((status='pending' AND reviewed_by IS NULL) OR (status<>'pending' AND reviewed_by IS NOT NULL AND reviewed_by<>created_by))
);
CREATE UNIQUE INDEX idx_master_approved_alias ON accounting.company_master_aliases(firm_id,company_id,kind,original) WHERE status='approved';
CREATE INDEX idx_master_company ON accounting.company_master_entities(firm_id,company_id,kind,active);
CREATE TABLE accounting.company_master_history (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL REFERENCES accounting.books(id),
 object_id TEXT NOT NULL,action TEXT NOT NULL,value_json TEXT NOT NULL CHECK(jsonb_typeof(value_json::jsonb)='object'),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
INSERT INTO accounting.ledgers(id,firm_id,company_id,name,created_at) SELECT 'ledger_'||id,firm_id,company_id,name,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') FROM accounting.books;
INSERT INTO accounting.ledger_periods(ledger_id,book_id,firm_id,company_id,period,linked_at)
 SELECT 'ledger_'||id,id,firm_id,company_id,period,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') FROM accounting.books;

CREATE FUNCTION accounting.ledger_visible(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(SELECT 1 FROM accounting.ledgers l WHERE l.id=target AND l.firm_id=accounting.actor_firm()
 AND EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=l.id AND accounting.book_allowed(p.book_id)))
$$;
CREATE FUNCTION accounting.ledger_manage(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.ledger_visible(target) AND EXISTS(SELECT 1 FROM accounting.ledger_managers m WHERE m.ledger_id=target AND m.user_id=accounting.actor_user())
 AND EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=target AND accounting.catalog_manage(p.book_id))
$$;
CREATE FUNCTION accounting.master_company_read(target_firm TEXT,target_company TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.firm_id=target_firm AND b.company_id=target_company AND accounting.book_allowed(b.id))
$$;
CREATE FUNCTION accounting.ledger_member_current(source TEXT,target_user TEXT,target_role TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.catalog_manage(source) AND EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id
 JOIN accounting.users u ON u.id=a.user_id AND u.active=1
 WHERE b.id=source AND b.firm_id=accounting.actor_firm() AND a.user_id=target_user AND a.role=target_role
 AND (EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.user_id=u.id AND m.firm_id=b.firm_id)
 OR EXISTS(SELECT 1 FROM accounting.company_memberships c WHERE c.user_id=u.id AND c.company_id=b.company_id AND c.role IN ('owner','admin','finance'))))
$$;
CREATE FUNCTION accounting.ledger_member_choices(source TEXT) RETURNS TABLE(user_id TEXT,role TEXT,name TEXT)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT a.user_id,a.role,u.name FROM accounting.book_access a JOIN accounting.users u ON u.id=a.user_id AND u.active=1
 WHERE a.book_id=source AND accounting.catalog_manage(source)
 AND EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.book_id=source AND accounting.ledger_manage(p.ledger_id))
 AND accounting.ledger_member_current(source,a.user_id,a.role) ORDER BY u.name,a.user_id
$$;
CREATE FUNCTION accounting.ledger_create_period(target_ledger TEXT,target_book TEXT,source_book TEXT,target_period TEXT,target_name TEXT,inherit_users JSONB)
 RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE l RECORD; member RECORD; stamp TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NOT accounting.ledger_manage(target_ledger) OR NOT accounting.catalog_manage(source_book)
 OR NOT EXISTS(SELECT 1 FROM accounting.ledger_periods WHERE ledger_id=target_ledger AND book_id=source_book)
 THEN RAISE EXCEPTION 'new period requires explicit ledger manager and source reviewer' USING ERRCODE='42501'; END IF;
 IF target_period !~ '^(20[0-9]{2})-(0[1-9]|1[0-2])$' OR length(target_name) NOT BETWEEN 1 AND 120 OR length(target_book) NOT BETWEEN 1 AND 100
 OR jsonb_typeof(inherit_users)<>'array' OR jsonb_array_length(inherit_users)>100
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(inherit_users) item WHERE jsonb_typeof(item)<>'string')
 OR (SELECT count(*) FROM jsonb_array_elements_text(inherit_users))<>(SELECT count(DISTINCT value) FROM jsonb_array_elements_text(inherit_users))
 THEN RAISE EXCEPTION 'invalid period or inheritance selection'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.ledger_periods WHERE ledger_id=target_ledger AND period=target_period)
 THEN RAISE EXCEPTION 'ledger period already exists'; END IF;
 SELECT * INTO STRICT l FROM accounting.ledgers WHERE id=target_ledger;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 FOR member IN SELECT value AS id FROM jsonb_array_elements_text(inherit_users) LOOP
 IF NOT EXISTS(SELECT 1 FROM accounting.book_access a WHERE a.book_id=source_book AND a.user_id=member.id AND accounting.ledger_member_current(source_book,member.id,a.role))
 THEN RAISE EXCEPTION 'selected member no longer has current source access' USING ERRCODE='42501'; END IF;
 END LOOP;
 INSERT INTO accounting.books(id,firm_id,company_id,name,period) VALUES(target_book,l.firm_id,l.company_id,target_name,target_period);
 INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(target_book,l.firm_id,accounting.actor_user(),'reviewer');
 INSERT INTO accounting.book_access(book_id,firm_id,user_id,role)
 SELECT target_book,l.firm_id,a.user_id,a.role FROM accounting.book_access a WHERE a.book_id=source_book AND a.user_id IN(SELECT value FROM jsonb_array_elements_text(inherit_users)) AND a.user_id<>accounting.actor_user();
 INSERT INTO accounting.periods(book_id,period,status) VALUES(target_book,target_period,'open');
 INSERT INTO accounting.ledger_periods(ledger_id,book_id,firm_id,company_id,period,linked_by,linked_at)
 VALUES(target_ledger,target_book,l.firm_id,l.company_id,target_period,accounting.actor_user(),stamp);
 RETURN target_book; END $$;

CREATE FUNCTION accounting.ledger_scope_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF TG_TABLE_NAME='ledger_periods' THEN
 IF NOT accounting.ledger_mapping_write(NEW.ledger_id,NEW.book_id)
 OR NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period)
 THEN RAISE EXCEPTION 'period mapping requires scoped reviewer and ledger manager' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (NEW.book_id,NEW.firm_id,NEW.company_id,NEW.period) IS DISTINCT FROM (OLD.book_id,OLD.firm_id,OLD.company_id,OLD.period)
 THEN RAISE EXCEPTION 'physical period identity is immutable'; END IF;
 ELSIF TG_TABLE_NAME='company_master_entities' THEN
 IF NOT accounting.catalog_manage(NEW.recorded_book_id) THEN RAISE EXCEPTION 'master change requires reviewer' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.firm_id,NEW.company_id,NEW.kind,NEW.code,NEW.created_by,NEW.recorded_book_id,NEW.created_at) IS DISTINCT FROM
 (OLD.id,OLD.firm_id,OLD.company_id,OLD.kind,OLD.code,OLD.created_by,OLD.recorded_book_id,OLD.created_at) OR NEW.version<>OLD.version+1)
 THEN RAISE EXCEPTION 'master identity and version are immutable'; END IF;
 ELSIF TG_TABLE_NAME='company_master_aliases' THEN
 IF NOT accounting.catalog_manage(NEW.recorded_book_id) THEN RAISE EXCEPTION 'alias change requires reviewer' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' AND (NEW.status<>'pending' OR NEW.created_by<>accounting.actor_user() OR NEW.version<>1)
 THEN RAISE EXCEPTION 'alias requires independent review'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.firm_id,NEW.company_id,NEW.entity_id,NEW.kind,NEW.original,NEW.created_by,NEW.created_at,NEW.recorded_book_id,NEW.reason)
 IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.entity_id,OLD.kind,OLD.original,OLD.created_by,OLD.created_at,OLD.recorded_book_id,OLD.reason)
 OR NEW.version<>OLD.version+1 OR NEW.reviewed_by<>accounting.actor_user() OR NEW.reviewed_by=NEW.created_by
 OR NOT ((OLD.status='pending' AND NEW.status IN ('approved','rejected')) OR (OLD.status='approved' AND NEW.status='retired')))
 THEN RAISE EXCEPTION 'alias requires independent review and immutable provenance'; END IF;
 ELSIF TG_TABLE_NAME='ledger_rule_candidates' THEN
 IF TG_OP='INSERT' AND (NEW.status<>'pending' OR NEW.target_rule_id IS NOT NULL OR NOT accounting.catalog_manage(NEW.source_book_id)
 OR NOT EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=NEW.ledger_id AND p.book_id=NEW.source_book_id)
 OR NOT EXISTS(SELECT 1 FROM accounting.ledger_periods p WHERE p.ledger_id=NEW.ledger_id AND p.book_id=NEW.target_book_id)
 OR NOT EXISTS(SELECT 1 FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version
 WHERE r.id=NEW.source_rule_id AND r.book_id=NEW.source_book_id AND r.status='published' AND r.current_version=NEW.source_rule_version AND v.definition_json::jsonb=NEW.definition_json::jsonb))
 THEN RAISE EXCEPTION 'inheritance requires genuine published source definition and same ledger'; END IF;
 IF TG_OP='UPDATE' AND ((NEW.id,NEW.ledger_id,NEW.source_book_id,NEW.target_book_id,NEW.source_rule_id,NEW.source_rule_version,NEW.definition_json,NEW.created_by,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.ledger_id,OLD.source_book_id,OLD.target_book_id,OLD.source_rule_id,OLD.source_rule_version,OLD.definition_json,OLD.created_by,OLD.created_at)
 OR OLD.status<>'pending' OR NEW.status<>'evaluated' OR NEW.target_rule_id IS NULL
 OR NOT EXISTS(SELECT 1 FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version
 WHERE r.id=NEW.target_rule_id AND r.book_id=NEW.target_book_id AND r.status='draft' AND v.definition_json::jsonb=NEW.definition_json::jsonb))
 THEN RAISE EXCEPTION 'inherited rule provenance is immutable'; END IF;
 END IF;
 RETURN NEW; END $$;

-- Mapping insertion must also permit initial, explicit ledger initialization.
CREATE FUNCTION accounting.ledger_mapping_write(target_ledger TEXT,target_book TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.catalog_manage(target_book) AND EXISTS(SELECT 1 FROM accounting.ledgers l JOIN accounting.books b ON b.firm_id=l.firm_id AND b.company_id=l.company_id
 JOIN accounting.ledger_managers m ON m.ledger_id=l.id AND m.user_id=accounting.actor_user()
 WHERE l.id=target_ledger AND b.id=target_book)
$$;

CREATE FUNCTION accounting.ledger_manager_grant_allowed(target_ledger TEXT,target_user TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.ledgers l WHERE l.id=target_ledger AND l.firm_id=accounting.actor_firm()
 AND accounting.company_manager(l.company_id) AND EXISTS(SELECT 1 FROM accounting.users u WHERE u.id=target_user AND u.active=1)
 AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.firm_id=l.firm_id AND b.company_id=l.company_id AND accounting.catalog_manage(b.id))
 AND (EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=l.firm_id AND m.user_id=target_user AND m.role IN ('owner','admin','reviewer'))
 OR (NOT EXISTS(SELECT 1 FROM accounting.memberships m WHERE m.firm_id=l.firm_id AND m.user_id=target_user)
 AND EXISTS(SELECT 1 FROM accounting.company_memberships c WHERE c.company_id=l.company_id AND c.user_id=target_user AND c.role='finance'))))
$$;
CREATE FUNCTION accounting.master_resolve(target_company TEXT,target_kind TEXT,target_original TEXT)
 RETURNS TABLE(entity_id TEXT,canonical_value TEXT,entity_version INTEGER,alias_version INTEGER)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH authorized AS (
 SELECT accounting.actor_active() AND accounting.company_visible(target_company) AND (
 accounting.master_company_read(accounting.actor_firm(),target_company)
 OR (target_kind IN ('counterparty','project') AND EXISTS(SELECT 1 FROM accounting.company_memberships c WHERE c.company_id=target_company AND c.user_id=accounting.actor_user()))) AS ok),
 candidates AS (
 SELECT e.id,e.name,e.version,a.version AS av,0 AS priority FROM accounting.company_master_aliases a
 JOIN accounting.company_master_entities e ON e.id=a.entity_id AND e.firm_id=a.firm_id AND e.company_id=a.company_id AND e.kind=a.kind
 WHERE a.firm_id=accounting.actor_firm() AND a.company_id=target_company AND a.kind=target_kind AND a.original=target_original AND a.status='approved' AND e.active=1
 UNION ALL SELECT e.id,e.name,e.version,0,1 FROM accounting.company_master_entities e WHERE e.firm_id=accounting.actor_firm() AND e.company_id=target_company AND e.kind=target_kind AND e.name=target_original AND e.active=1)
 SELECT id,name,version,av FROM candidates,authorized WHERE ok AND length(target_original) BETWEEN 1 AND 200
 AND priority=(SELECT min(priority) FROM candidates) AND (SELECT count(*) FROM candidates WHERE priority=(SELECT min(priority) FROM candidates))=1
$$;
REVOKE ALL ON FUNCTION accounting.ledger_visible(TEXT),accounting.ledger_manage(TEXT),accounting.master_company_read(TEXT,TEXT),
 accounting.ledger_member_current(TEXT,TEXT,TEXT),accounting.ledger_member_choices(TEXT),accounting.ledger_create_period(TEXT,TEXT,TEXT,TEXT,TEXT,JSONB),
 accounting.ledger_scope_guard(),accounting.ledger_mapping_write(TEXT,TEXT),accounting.ledger_manager_grant_allowed(TEXT,TEXT),accounting.master_resolve(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.ledger_visible(TEXT),accounting.ledger_manage(TEXT),accounting.master_company_read(TEXT,TEXT),
 accounting.ledger_member_current(TEXT,TEXT,TEXT),accounting.ledger_member_choices(TEXT),accounting.ledger_create_period(TEXT,TEXT,TEXT,TEXT,TEXT,JSONB),
 accounting.ledger_mapping_write(TEXT,TEXT),accounting.ledger_manager_grant_allowed(TEXT,TEXT),accounting.master_resolve(TEXT,TEXT,TEXT) TO accounting_app;
CREATE TRIGGER ledger_period_guard BEFORE INSERT OR UPDATE ON accounting.ledger_periods FOR EACH ROW EXECUTE FUNCTION accounting.ledger_scope_guard();
CREATE TRIGGER master_entity_guard BEFORE INSERT OR UPDATE ON accounting.company_master_entities FOR EACH ROW EXECUTE FUNCTION accounting.ledger_scope_guard();
CREATE TRIGGER master_alias_guard BEFORE INSERT OR UPDATE ON accounting.company_master_aliases FOR EACH ROW EXECUTE FUNCTION accounting.ledger_scope_guard();
CREATE TRIGGER ledger_candidate_guard BEFORE INSERT OR UPDATE ON accounting.ledger_rule_candidates FOR EACH ROW EXECUTE FUNCTION accounting.ledger_scope_guard();
CREATE TRIGGER ledger_command_immutable BEFORE UPDATE OR DELETE ON accounting.ledger_commands FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();
CREATE TRIGGER master_history_immutable BEFORE UPDATE OR DELETE ON accounting.company_master_history FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();
CREATE TRIGGER master_entity_nodelete BEFORE DELETE ON accounting.company_master_entities FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();
CREATE TRIGGER master_alias_nodelete BEFORE DELETE ON accounting.company_master_aliases FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();
CREATE TRIGGER ledger_candidate_nodelete BEFORE DELETE ON accounting.ledger_rule_candidates FOR EACH ROW EXECUTE FUNCTION accounting.template_history_guard();
DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['ledgers','ledger_managers','ledger_periods','ledger_commands','ledger_rule_candidates','company_master_entities','company_master_aliases','company_master_history'] LOOP
 EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);
 EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
 EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_worker',tab);
 END LOOP;
END $$;
GRANT SELECT,INSERT ON accounting.ledgers,accounting.ledger_managers,accounting.ledger_commands,accounting.company_master_history TO accounting_app;
GRANT DELETE ON accounting.ledger_managers TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.ledger_periods,accounting.ledger_rule_candidates,accounting.company_master_entities,accounting.company_master_aliases TO accounting_app;
CREATE POLICY ledgers_read ON accounting.ledgers FOR SELECT TO accounting_app USING(accounting.ledger_visible(id));
CREATE POLICY ledgers_insert ON accounting.ledgers FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.company_manager(company_id) AND EXISTS(SELECT 1 FROM accounting.books b WHERE b.firm_id=ledgers.firm_id AND b.company_id=ledgers.company_id AND accounting.catalog_manage(b.id)));
CREATE POLICY ledger_managers_read ON accounting.ledger_managers FOR SELECT TO accounting_app USING(accounting.ledger_visible(ledger_id));
CREATE POLICY ledger_managers_insert ON accounting.ledger_managers FOR INSERT TO accounting_app WITH CHECK(granted_by=accounting.actor_user() AND accounting.ledger_manager_grant_allowed(ledger_id,user_id));
CREATE POLICY ledger_managers_delete ON accounting.ledger_managers FOR DELETE TO accounting_app USING(accounting.ledger_manager_grant_allowed(ledger_id,user_id));
CREATE POLICY ledger_periods_read ON accounting.ledger_periods FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY ledger_periods_insert ON accounting.ledger_periods FOR INSERT TO accounting_app WITH CHECK(accounting.ledger_mapping_write(ledger_id,book_id));
CREATE POLICY ledger_periods_update ON accounting.ledger_periods FOR UPDATE TO accounting_app USING(accounting.catalog_manage(book_id)) WITH CHECK(accounting.ledger_mapping_write(ledger_id,book_id));
CREATE POLICY ledger_commands_read ON accounting.ledger_commands FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY ledger_commands_insert ON accounting.ledger_commands FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.ledger_manage(ledger_id) AND accounting.catalog_manage(book_id));
CREATE POLICY ledger_candidates_read ON accounting.ledger_rule_candidates FOR SELECT TO accounting_app USING(accounting.book_allowed(target_book_id));
CREATE POLICY ledger_candidates_insert ON accounting.ledger_rule_candidates FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.ledger_manage(ledger_id) AND accounting.catalog_manage(source_book_id) AND accounting.catalog_manage(target_book_id));
CREATE POLICY ledger_candidates_update ON accounting.ledger_rule_candidates FOR UPDATE TO accounting_app USING(accounting.catalog_manage(target_book_id)) WITH CHECK(accounting.catalog_manage(target_book_id));
CREATE POLICY master_entities_read ON accounting.company_master_entities FOR SELECT TO accounting_app USING(accounting.master_company_read(firm_id,company_id));
CREATE POLICY master_entities_insert ON accounting.company_master_entities FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(recorded_book_id));
CREATE POLICY master_entities_update ON accounting.company_master_entities FOR UPDATE TO accounting_app USING(accounting.catalog_manage(recorded_book_id)) WITH CHECK(accounting.catalog_manage(recorded_book_id));
CREATE POLICY master_aliases_read ON accounting.company_master_aliases FOR SELECT TO accounting_app USING(accounting.master_company_read(firm_id,company_id));
CREATE POLICY master_aliases_insert ON accounting.company_master_aliases FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(recorded_book_id));
CREATE POLICY master_aliases_update ON accounting.company_master_aliases FOR UPDATE TO accounting_app USING(accounting.catalog_manage(recorded_book_id)) WITH CHECK(accounting.catalog_manage(recorded_book_id));
CREATE POLICY master_history_read ON accounting.company_master_history FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY master_history_insert ON accounting.company_master_history FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
