-- Historical reference settlement has no business source/event foreign keys.
CREATE TABLE accounting.migration_open_items(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES accounting.migration_projects(id),cutover_id TEXT NOT NULL REFERENCES accounting.migration_cutovers(id),record_id TEXT NOT NULL REFERENCES accounting.migration_records(id),
 ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,source_system_key TEXT NOT NULL,external_book_id TEXT NOT NULL,external_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('receivable','payable')),source_account_code TEXT NOT NULL,target_account_code TEXT NOT NULL,counterparty_code TEXT NOT NULL,
 counterparty_id TEXT NOT NULL REFERENCES accounting.company_master_entities(id),amount_cents BIGINT NOT NULL CHECK(amount_cents>0 AND amount_cents<=99999999999999),
 occurred_on TEXT NOT NULL,due_on TEXT,master_bindings_json TEXT NOT NULL CHECK(jsonb_typeof(master_bindings_json::jsonb)='array'),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,external_id),UNIQUE(firm_id,ledger_id,source_system_key,external_book_id,external_id),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.migration_clearance_previews(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,book_id TEXT NOT NULL,bank_book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),
 request_json TEXT NOT NULL CHECK(jsonb_typeof(request_json::jsonb)='object'),plan_json TEXT NOT NULL CHECK(jsonb_typeof(plan_json::jsonb)='object'),fingerprint TEXT NOT NULL CHECK(fingerprint~'^[0-9a-f]{64}$'),created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(bank_book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.migration_clearance_commands(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,book_id TEXT NOT NULL,bank_book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,preview_id TEXT NOT NULL UNIQUE,
 bank_source_id TEXT NOT NULL,bank_draft_id TEXT NOT NULL,bank_account_code TEXT NOT NULL,expected_draft_version INTEGER NOT NULL CHECK(expected_draft_version>0),applied_draft_version INTEGER NOT NULL CHECK(applied_draft_version=expected_draft_version+1),
 previous_draft_json TEXT NOT NULL CHECK(jsonb_typeof(previous_draft_json::jsonb)='object'),plan_json TEXT NOT NULL CHECK(jsonb_typeof(plan_json::jsonb)='object'),created_by TEXT NOT NULL REFERENCES accounting.users(id),idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(request_fingerprint~'^[0-9a-f]{64}$'),created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(preview_id,project_id,firm_id,company_id) REFERENCES accounting.migration_clearance_previews(id,project_id,firm_id,company_id),FOREIGN KEY(bank_source_id,bank_book_id) REFERENCES accounting.sources(id,book_id),FOREIGN KEY(bank_draft_id,bank_book_id) REFERENCES accounting.drafts(id,book_id)
);
CREATE TABLE accounting.migration_clearance_allocations(
 id TEXT PRIMARY KEY,command_id TEXT NOT NULL,project_id TEXT NOT NULL,bank_book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,item_id TEXT NOT NULL,
 bank_source_id TEXT NOT NULL,amount_cents BIGINT NOT NULL CHECK(amount_cents>0 AND amount_cents<=99999999999999),target_account_code TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(command_id,item_id),FOREIGN KEY(command_id,project_id,firm_id,company_id) REFERENCES accounting.migration_clearance_commands(id,project_id,firm_id,company_id),FOREIGN KEY(item_id,project_id,firm_id,company_id) REFERENCES accounting.migration_open_items(id,project_id,firm_id,company_id),FOREIGN KEY(bank_source_id,bank_book_id) REFERENCES accounting.sources(id,book_id)
);
CREATE TABLE accounting.migration_clearance_reversals(
 id TEXT PRIMARY KEY,command_id TEXT NOT NULL UNIQUE,project_id TEXT NOT NULL,bank_book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),expected_fingerprint TEXT NOT NULL CHECK(expected_fingerprint~'^[0-9a-f]{64}$'),UNIQUE(firm_id,created_by,idempotency_key),
 FOREIGN KEY(command_id,project_id,firm_id,company_id) REFERENCES accounting.migration_clearance_commands(id,project_id,firm_id,company_id)
);
CREATE INDEX migration_open_items_page ON accounting.migration_open_items(project_id,occurred_on,external_id,id);
CREATE INDEX migration_clearance_item_balance ON accounting.migration_clearance_allocations(item_id,command_id);
CREATE INDEX migration_clearance_bank_balance ON accounting.migration_clearance_allocations(bank_source_id,command_id);
CREATE INDEX migration_clearance_commands_page ON accounting.migration_clearance_commands(project_id,created_at DESC,id);

CREATE FUNCTION accounting.legacy_clearance_source_locked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND EXISTS(SELECT 1 FROM accounting.migration_clearance_allocations a WHERE a.bank_book_id=target_book AND a.bank_source_id=target_source AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id))
$$;
CREATE FUNCTION accounting.legacy_cent_text(amount BIGINT) RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT (amount/100)::text||'.'||lpad((amount%100)::text,2,'0') $$;
CREATE FUNCTION accounting.legacy_open_item_used(target TEXT) RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT coalesce((SELECT sum(a.amount_cents) FROM accounting.migration_clearance_allocations a WHERE a.item_id=i.id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)),0)::bigint FROM accounting.migration_open_items i WHERE i.id=target AND accounting.migration_allowed(i.project_id)
$$;
CREATE FUNCTION accounting.legacy_draft_json(target TEXT) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT jsonb_build_object('description',d.description,'lines',coalesce((SELECT jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit',accounting.legacy_cent_text(l.debit_cents),'credit',accounting.legacy_cent_text(l.credit_cents),'auxiliary_values',coalesce(a.values_json::jsonb,'{}'::jsonb)) ORDER BY l.ordinal) FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal WHERE l.draft_id=d.id),'[]'::jsonb)) FROM accounting.drafts d WHERE d.id=target
$$;
CREATE FUNCTION accounting.legacy_clearance_fingerprint(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT encode(sha256(convert_to(jsonb_build_object('command_id',c.id,'plan',c.plan_json::jsonb,'draft_version',d.version,'draft_status',d.status,'draft',accounting.legacy_draft_json(d.id),'reversed',EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=c.id))::text,'UTF8')),'hex') FROM accounting.migration_clearance_commands c JOIN accounting.drafts d ON d.id=c.bank_draft_id WHERE c.id=target AND accounting.migration_allowed(c.project_id) AND accounting.ai_book_reviewer(c.bank_book_id)
$$;
CREATE FUNCTION accounting.legacy_open_item_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF NOT accounting.migration_manage(NEW.project_id) OR NEW.created_by<>accounting.actor_user() OR NOT EXISTS(
  SELECT 1 FROM accounting.migration_projects p JOIN accounting.migration_cutovers c ON c.project_id=p.id JOIN accounting.migration_validations v ON v.id=c.validation_id JOIN accounting.migration_records r ON r.id=NEW.record_id
  JOIN accounting.company_master_entities e ON e.id=NEW.counterparty_id AND e.firm_id=p.firm_id AND e.company_id=p.company_id AND e.kind='counterparty' AND e.active=1
  WHERE p.id=NEW.project_id AND p.status='active' AND c.id=NEW.cutover_id AND p.book_id=NEW.book_id AND p.ledger_id=NEW.ledger_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND r.project_id=p.id AND r.kind='open_item' AND r.external_id=NEW.external_id
  AND p.source_system_key=NEW.source_system_key AND p.external_book_id=NEW.external_book_id
  AND v.report_json::jsonb->'coverage'->>'open_item_reconciliation_complete'='true'
  AND r.payload_json::jsonb->>'kind'=NEW.kind AND r.payload_json::jsonb->>'counterparty_code'=NEW.counterparty_code AND r.payload_json::jsonb->>'remaining_amount'=accounting.legacy_cent_text(NEW.amount_cents)
  AND r.payload_json::jsonb->>'occurred_on'=NEW.occurred_on AND r.payload_json::jsonb->>'due_on' IS NOT DISTINCT FROM NEW.due_on AND NEW.master_bindings_json::jsonb=c.payload_json::jsonb->'master_bindings'
  AND EXISTS(SELECT 1 FROM jsonb_array_elements(v.report_json::jsonb->'open_item_bindings') x WHERE x->>'external_id'=NEW.external_id AND x->>'account_code'=NEW.source_account_code AND x->>'counterparty_code'=NEW.counterparty_code)
  AND EXISTS(SELECT 1 FROM jsonb_array_elements(c.payload_json::jsonb->'master_bindings') x WHERE x->>'kind'='counterparty' AND x->>'source_code'=NEW.counterparty_code AND x->>'target_id'=NEW.counterparty_id)
  AND EXISTS(SELECT 1 FROM accounting.migration_mapping_versions m CROSS JOIN LATERAL jsonb_array_elements(m.payload_json::jsonb->'accounts') a WHERE m.project_id=p.id AND m.version=(SELECT max(version) FROM accounting.migration_mapping_versions WHERE project_id=p.id) AND a->>'source_code'=NEW.source_account_code AND a->>'target_code'=NEW.target_account_code)
 ) THEN RAISE EXCEPTION 'Historical item requires active verified cutover and bindings' USING ERRCODE='42501';END IF;
 RETURN NEW;END $$;

CREATE FUNCTION accounting.legacy_clearance_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_project RECORD;v_preview RECORD;v_bank RECORD;v_item RECORD;v_command RECORD;part JSONB;party RECORD;amount BIGINT;total BIGINT:=0;v_kind TEXT;previous_kind TEXT;BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NEW.created_by<>accounting.actor_user() OR NEW.firm_id<>accounting.actor_firm() OR NOT accounting.migration_allowed(NEW.project_id) OR NOT accounting.ai_book_reviewer(NEW.bank_book_id) THEN RAISE EXCEPTION 'Current explicit history and bank review scope required' USING ERRCODE='42501';END IF;
 SELECT * INTO v_project FROM accounting.migration_projects WHERE id=NEW.project_id;
 IF v_project.status<>'active' OR v_project.company_id<>NEW.company_id OR NOT EXISTS(SELECT 1 FROM accounting.ledger_periods l JOIN accounting.books b ON b.id=l.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE l.ledger_id=v_project.ledger_id AND l.book_id=NEW.bank_book_id AND p.status='open' AND b.period>=left(v_project.cutover_date,7)) THEN RAISE EXCEPTION 'Same open ledger after cutover required';END IF;
 IF TG_TABLE_NAME='migration_clearance_previews' THEN
  IF NEW.book_id<>v_project.book_id OR NEW.expires_at::timestamptz<=clock_timestamp() OR NEW.expires_at::timestamptz>clock_timestamp()+interval '11 minutes' OR NEW.plan_json::jsonb->>'project_id' IS DISTINCT FROM NEW.project_id OR NEW.plan_json::jsonb->>'bank_book_id' IS DISTINCT FROM NEW.bank_book_id THEN RAISE EXCEPTION 'Bounded private preview scope required';END IF;
 ELSIF TG_TABLE_NAME='migration_clearance_commands' THEN
  SELECT * INTO v_preview FROM accounting.migration_clearance_previews WHERE id=NEW.preview_id AND project_id=NEW.project_id AND created_by=accounting.actor_user();
  IF NOT FOUND OR v_preview.expires_at::timestamptz<=clock_timestamp() OR v_preview.plan_json::jsonb IS DISTINCT FROM NEW.plan_json::jsonb OR NEW.book_id<>v_project.book_id OR NEW.bank_source_id IS DISTINCT FROM NEW.plan_json::jsonb->>'bank_source_id' OR NEW.bank_draft_id IS DISTINCT FROM NEW.plan_json::jsonb->>'bank_draft_id' OR NEW.expected_draft_version IS DISTINCT FROM (NEW.plan_json::jsonb->>'bank_draft_version')::integer OR NEW.bank_account_code IS DISTINCT FROM NEW.plan_json::jsonb->>'bank_account_code' OR jsonb_typeof(NEW.plan_json::jsonb->'lines') IS DISTINCT FROM 'array' OR jsonb_typeof(NEW.plan_json::jsonb->'description') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'Expired, incomplete or copied legacy preview';END IF;
  SELECT s.*,d.id AS draft_id,d.status AS draft_status,d.version AS draft_version INTO v_bank FROM accounting.sources s JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=NEW.bank_source_id AND s.book_id=NEW.bank_book_id AND s.source_kind='bank';
  IF NOT FOUND OR v_bank.draft_id<>NEW.bank_draft_id OR v_bank.draft_status<>'draft' OR v_bank.draft_version<>NEW.expected_draft_version OR v_bank.fingerprint IS DISTINCT FROM NEW.plan_json::jsonb->>'bank_fingerprint' OR v_bank.amount_cents IS DISTINCT FROM (NEW.plan_json::jsonb->>'total_cents')::bigint OR v_bank.direction IS DISTINCT FROM NEW.plan_json::jsonb->>'direction' OR accounting.source_inactive(NEW.bank_book_id,v_bank.id) OR accounting.revision_source_blocked(NEW.bank_book_id,v_bank.id) OR accounting.bank_source_import_blocked(NEW.bank_book_id,v_bank.id) OR accounting.case_source_blocked(NEW.bank_book_id,v_bank.id)
   OR accounting.legacy_clearance_source_locked(NEW.bank_book_id,v_bank.id) OR accounting.settlement_used(NULL,v_bank.id)>0 OR EXISTS(SELECT 1 FROM accounting.ai_bank_links WHERE bank_source_id=v_bank.id) OR NEW.previous_draft_json::jsonb IS DISTINCT FROM accounting.legacy_draft_json(v_bank.draft_id) THEN RAISE EXCEPTION 'Frozen unused unreviewed current bank facts required';END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.accounts a WHERE a.book_id=NEW.bank_book_id AND a.code=NEW.bank_account_code AND a.code LIKE '1002%' AND a.active=1 AND a.category='asset' AND a.normal_balance='debit' AND a.auxiliary_requirements::jsonb='[]'::jsonb AND a.version=(NEW.plan_json::jsonb->>'bank_account_version')::integer) THEN RAISE EXCEPTION 'Current bank account snapshot required';END IF;
  SELECT * INTO party FROM accounting.master_resolve(v_project.company_id,'counterparty',v_bank.counterparty);
  IF NOT FOUND OR party.entity_id IS DISTINCT FROM NEW.plan_json::jsonb->>'bank_party_id' OR party.entity_version::text||':'||party.alias_version::text IS DISTINCT FROM NEW.plan_json::jsonb->>'bank_party_version' THEN RAISE EXCEPTION 'Current approved party mapping required';END IF;
  IF jsonb_typeof(NEW.plan_json::jsonb->'parts') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.plan_json::jsonb->'parts') NOT BETWEEN 1 AND 50 OR (SELECT count(DISTINCT x->>'item_id') FROM jsonb_array_elements(NEW.plan_json::jsonb->'parts') x)<>jsonb_array_length(NEW.plan_json::jsonb->'parts') THEN RAISE EXCEPTION 'Bounded unique historical items required';END IF;
  FOR part IN SELECT value FROM jsonb_array_elements(NEW.plan_json::jsonb->'parts') LOOP
   IF jsonb_typeof(part) IS DISTINCT FROM 'object' OR jsonb_typeof(part->'item_id') IS DISTINCT FROM 'string' OR jsonb_typeof(part->'amount_cents') IS DISTINCT FROM 'number' OR coalesce(part->>'amount_cents','') !~ '^[1-9][0-9]{0,13}$' OR jsonb_typeof(part->'remaining_before') IS DISTINCT FROM 'number' OR coalesce(part->>'remaining_before','') !~ '^[1-9][0-9]{0,13}$' THEN RAISE EXCEPTION 'Exact integer amount and current remaining snapshot required';END IF;
   SELECT * INTO v_item FROM accounting.migration_open_items WHERE id=part->>'item_id' AND project_id=NEW.project_id AND company_id=NEW.company_id;
   amount:=(part->>'amount_cents')::bigint;v_kind:=v_item.kind;
   IF NOT FOUND OR amount<=0 OR amount>v_item.amount_cents-coalesce((SELECT sum(a.amount_cents) FROM accounting.migration_clearance_allocations a WHERE a.item_id=v_item.id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)),0) OR (part->>'remaining_before')::bigint<>v_item.amount_cents-coalesce((SELECT sum(a.amount_cents) FROM accounting.migration_clearance_allocations a WHERE a.item_id=v_item.id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)),0) OR v_item.counterparty_id<>party.entity_id OR v_item.target_account_code=NEW.bank_account_code OR v_item.target_account_code IS DISTINCT FROM part->>'clearing_account' OR v_kind IS DISTINCT FROM part->>'kind' OR ((v_kind='payable') IS DISTINCT FROM (v_bank.direction='expense')) OR (previous_kind IS NOT NULL AND previous_kind<>v_kind) THEN RAISE EXCEPTION 'Same kind, current party and exact remaining balance required';END IF;
   IF NOT EXISTS(SELECT 1 FROM accounting.accounts a JOIN accounting.company_master_entities e ON e.id=v_item.counterparty_id AND e.active=1 WHERE a.book_id=NEW.bank_book_id AND a.code=v_item.target_account_code AND a.active=1 AND a.version=(part->>'clearing_account_version')::integer AND e.version=(part->>'counterparty_version')::integer AND e.name=part->>'counterparty_name' AND a.category=CASE WHEN v_kind='payable' THEN 'liability' ELSE 'asset' END AND a.normal_balance=CASE WHEN v_kind='payable' THEN 'credit' ELSE 'debit' END AND a.auxiliary_requirements::jsonb <@ '["counterparty"]'::jsonb AND a.auxiliary_requirements::jsonb=part->'auxiliary_requirements') THEN RAISE EXCEPTION 'Current clearing account and master snapshot required';END IF;
   previous_kind:=v_kind;total:=total+amount;
  END LOOP;
  IF total IS NULL OR total IS DISTINCT FROM v_bank.amount_cents THEN RAISE EXCEPTION 'Legacy allocation must cover whole bank amount exactly';END IF;
 ELSIF TG_TABLE_NAME='migration_clearance_allocations' THEN
  SELECT * INTO v_command FROM accounting.migration_clearance_commands WHERE id=NEW.command_id AND project_id=NEW.project_id AND bank_book_id=NEW.bank_book_id AND bank_source_id=NEW.bank_source_id AND created_by=NEW.created_by;
  SELECT * INTO v_item FROM accounting.migration_open_items WHERE id=NEW.item_id AND project_id=NEW.project_id;
  IF v_command.id IS NULL OR v_item.id IS NULL OR NEW.target_account_code<>v_item.target_account_code OR EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals WHERE command_id=v_command.id) OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_command.plan_json::jsonb->'parts') x WHERE x->>'item_id'=NEW.item_id AND (x->>'amount_cents')::bigint=NEW.amount_cents) OR NEW.amount_cents+coalesce((SELECT sum(a.amount_cents) FROM accounting.migration_clearance_allocations a WHERE a.item_id=v_item.id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)),0)>v_item.amount_cents THEN RAISE EXCEPTION 'Historical allocation overspends verified item or changes command';END IF;
 ELSIF TG_TABLE_NAME='migration_clearance_reversals' THEN
  SELECT * INTO v_command FROM accounting.migration_clearance_commands WHERE id=NEW.command_id AND project_id=NEW.project_id AND bank_book_id=NEW.bank_book_id;
  IF v_command.id IS NULL OR v_command.created_by=NEW.created_by OR NEW.expected_fingerprint IS DISTINCT FROM accounting.legacy_clearance_fingerprint(v_command.id) OR NOT EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.id=v_command.bank_draft_id AND d.status='draft' AND d.version=v_command.applied_draft_version) THEN RAISE EXCEPTION 'Independent current unreviewed reversal requires exact fingerprint';END IF;
 END IF;
 RETURN NEW;END $$;

CREATE FUNCTION accounting.legacy_clearance_journal_valid(target_command TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c RECORD;d RECORD;v_source RECORD;actual JSONB;expected JSONB;BEGIN
 SELECT * INTO c FROM accounting.migration_clearance_commands WHERE id=target_command;IF NOT FOUND THEN RETURN false;END IF;
 SELECT * INTO d FROM accounting.drafts WHERE id=c.bank_draft_id;SELECT * INTO v_source FROM accounting.sources WHERE id=c.bank_source_id;
 IF EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals WHERE command_id=c.id) THEN RETURN true;END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.master_resolve(c.company_id,'counterparty',v_source.counterparty) p WHERE p.entity_id=c.plan_json::jsonb->>'bank_party_id' AND p.entity_version::text||':'||p.alias_version::text=c.plan_json::jsonb->>'bank_party_version')
 OR NOT EXISTS(SELECT 1 FROM accounting.accounts a WHERE a.book_id=c.bank_book_id AND a.code=c.bank_account_code AND a.active=1 AND a.version=(c.plan_json::jsonb->>'bank_account_version')::integer)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(c.plan_json::jsonb->'parts') p WHERE NOT EXISTS(SELECT 1 FROM accounting.accounts a JOIN accounting.company_master_entities e ON e.id=p->>'counterparty_id' WHERE a.book_id=c.bank_book_id AND a.code=p->>'clearing_account' AND a.active=1 AND e.active=1 AND a.version=(p->>'clearing_account_version')::integer AND e.version=(p->>'counterparty_version')::integer)) THEN RETURN false;END IF;
 IF d.id IS NULL OR d.version<>c.applied_draft_version+(CASE d.status WHEN 'draft' THEN 0 WHEN 'approved' THEN 1 WHEN 'exported' THEN 2 ELSE 999 END) OR d.description IS DISTINCT FROM c.plan_json::jsonb->>'description' OR (SELECT coalesce(sum(amount_cents),0) FROM accounting.migration_clearance_allocations WHERE command_id=c.id)<>v_source.amount_cents OR (SELECT count(*) FROM accounting.migration_clearance_allocations WHERE command_id=c.id)<>jsonb_array_length(c.plan_json::jsonb->'parts') THEN RETURN false;END IF;
 actual:=accounting.legacy_draft_json(d.id)->'lines';
 -- Recompute expected journal from immutable allocations rather than trust JSON lines.
 SELECT coalesce(jsonb_agg(jsonb_build_object('account_code',q.code,'debit',accounting.legacy_cent_text(q.debit),'credit',accounting.legacy_cent_text(q.credit),'auxiliary_values',q.aux) ORDER BY q.ordinal,q.code),'[]'::jsonb) INTO expected FROM (
  SELECT a.target_account_code AS code,CASE WHEN v_source.direction='expense' THEN sum(a.amount_cents) ELSE 0 END::bigint AS debit,CASE WHEN v_source.direction='income' THEN sum(a.amount_cents) ELSE 0 END::bigint AS credit,
   CASE WHEN ac.auxiliary_requirements::jsonb ? 'counterparty' THEN jsonb_build_object('counterparty',e.name) ELSE '{}'::jsonb END AS aux,0 AS ordinal
  FROM accounting.migration_clearance_allocations a JOIN accounting.migration_open_items i ON i.id=a.item_id JOIN accounting.company_master_entities e ON e.id=i.counterparty_id JOIN accounting.accounts ac ON ac.book_id=a.bank_book_id AND ac.code=a.target_account_code WHERE a.command_id=c.id GROUP BY a.target_account_code,ac.auxiliary_requirements,e.name
  UNION ALL SELECT c.bank_account_code,CASE WHEN v_source.direction='income' THEN v_source.amount_cents ELSE 0 END,CASE WHEN v_source.direction='expense' THEN v_source.amount_cents ELSE 0 END,'{}'::jsonb,1
 ) q;
 RETURN actual IS NOT DISTINCT FROM expected AND actual IS NOT DISTINCT FROM c.plan_json::jsonb->'lines';END $$;
CREATE FUNCTION accounting.legacy_clearance_deferred_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE target_draft TEXT;c RECORD;BEGIN
 IF TG_TABLE_NAME='migration_clearance_commands' THEN
  IF NOT accounting.legacy_clearance_journal_valid(NEW.id) THEN RAISE EXCEPTION 'Historical clearance command must atomically publish complete exact bank journal';END IF;
 ELSIF TG_TABLE_NAME='migration_clearance_allocations' THEN
  IF NOT accounting.legacy_clearance_journal_valid(NEW.command_id) THEN RAISE EXCEPTION 'Historical allocation and bank journal disagree';END IF;
 ELSIF TG_TABLE_NAME='migration_clearance_reversals' THEN
  SELECT * INTO c FROM accounting.migration_clearance_commands WHERE id=NEW.command_id;
  IF accounting.legacy_draft_json(c.bank_draft_id) IS DISTINCT FROM c.previous_draft_json::jsonb OR NOT EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.id=c.bank_draft_id AND d.status='draft' AND d.version=c.applied_draft_version+1) THEN RAISE EXCEPTION 'Reversal must atomically restore original unreviewed bank journal';END IF;
 ELSE
  IF TG_TABLE_NAME='drafts' THEN target_draft:=NEW.id;
  ELSIF TG_OP='DELETE' THEN target_draft:=OLD.draft_id;
  ELSE target_draft:=NEW.draft_id;END IF;
  FOR c IN SELECT x.* FROM accounting.migration_clearance_commands x WHERE x.bank_draft_id=target_draft AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=x.id) LOOP
   IF NOT accounting.migration_allowed(c.project_id) OR NOT accounting.ai_book_reviewer(c.bank_book_id) THEN RAISE EXCEPTION 'Explicit historical and current bank review authorization required' USING ERRCODE='42501';END IF;
   IF NOT accounting.legacy_clearance_journal_valid(c.id) THEN RAISE EXCEPTION 'Confirmed historical bank clearance journal cannot be overwritten';END IF;
  END LOOP;
 END IF;RETURN NULL;END $$;
CREATE FUNCTION accounting.legacy_clearance_exclusive_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE source_id TEXT;BEGIN
 IF TG_TABLE_NAME='bank_source_dispositions' THEN source_id:=NEW.source_id;
 ELSIF TG_TABLE_NAME='fact_revisions' THEN source_id:=NEW.old_source_id;
 ELSE source_id:=NEW.bank_source_id;END IF;
 IF EXISTS(SELECT 1 FROM accounting.migration_clearance_allocations a WHERE a.bank_source_id=source_id AND NOT EXISTS(SELECT 1 FROM accounting.migration_clearance_reversals r WHERE r.command_id=a.command_id)) THEN RAISE EXCEPTION 'Active historical clearance excludes business matching, source hiding and current settlement';END IF;
 RETURN NEW;END $$;

CREATE TRIGGER legacy_open_item_scope BEFORE INSERT ON accounting.migration_open_items FOR EACH ROW EXECUTE FUNCTION accounting.legacy_open_item_guard();
CREATE TRIGGER legacy_preview_scope BEFORE INSERT ON accounting.migration_clearance_previews FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_guard();
CREATE TRIGGER legacy_command_scope BEFORE INSERT ON accounting.migration_clearance_commands FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_guard();
CREATE TRIGGER legacy_allocation_scope BEFORE INSERT ON accounting.migration_clearance_allocations FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_guard();
CREATE TRIGGER legacy_reversal_scope BEFORE INSERT ON accounting.migration_clearance_reversals FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_guard();
CREATE CONSTRAINT TRIGGER legacy_command_atomic AFTER INSERT ON accounting.migration_clearance_commands DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_deferred_guard();
CREATE CONSTRAINT TRIGGER legacy_allocation_atomic AFTER INSERT ON accounting.migration_clearance_allocations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_deferred_guard();
CREATE CONSTRAINT TRIGGER legacy_reversal_atomic AFTER INSERT ON accounting.migration_clearance_reversals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_deferred_guard();
CREATE CONSTRAINT TRIGGER legacy_journal_lines AFTER INSERT OR UPDATE OR DELETE ON accounting.draft_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_deferred_guard();
CREATE CONSTRAINT TRIGGER legacy_journal_aux AFTER INSERT OR UPDATE OR DELETE ON accounting.draft_line_auxiliaries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_deferred_guard();
CREATE CONSTRAINT TRIGGER legacy_journal_status AFTER UPDATE ON accounting.drafts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_deferred_guard();
CREATE TRIGGER legacy_bank_source_exclusive BEFORE INSERT ON accounting.bank_source_dispositions FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_exclusive_guard();
CREATE TRIGGER legacy_bank_link_exclusive BEFORE INSERT ON accounting.ai_bank_links FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_exclusive_guard();
CREATE TRIGGER legacy_current_settlement_exclusive BEFORE INSERT ON accounting.settlement_allocations FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_exclusive_guard();
CREATE TRIGGER legacy_fact_revision_exclusive BEFORE INSERT ON accounting.fact_revisions FOR EACH ROW EXECUTE FUNCTION accounting.legacy_clearance_exclusive_guard();

DO $$ DECLARE tab TEXT;BEGIN
 FOREACH tab IN ARRAY ARRAY['migration_open_items','migration_clearance_previews','migration_clearance_commands','migration_clearance_allocations','migration_clearance_reversals'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',tab);EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',tab);
  EXECUTE format('CREATE POLICY legacy_settlement_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.migration_allowed(project_id))',tab);
  EXECUTE format('CREATE POLICY legacy_settlement_write ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND accounting.migration_allowed(project_id) AND created_by=accounting.actor_user())',tab);
  EXECUTE format('CREATE TRIGGER legacy_settlement_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',tab);
  EXECUTE format('CREATE TRIGGER legacy_settlement_dirty AFTER INSERT ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ops_mark_changed()',tab);
 END LOOP;
END $$;
DROP POLICY legacy_settlement_read ON accounting.migration_clearance_previews;
CREATE POLICY legacy_settlement_read ON accounting.migration_clearance_previews FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.migration_allowed(project_id) AND accounting.ai_book_reviewer(bank_book_id) AND created_by=accounting.actor_user());
DO $$ DECLARE tab TEXT;BEGIN
 FOREACH tab IN ARRAY ARRAY['migration_clearance_commands','migration_clearance_allocations','migration_clearance_reversals'] LOOP
  EXECUTE format('DROP POLICY legacy_settlement_read ON accounting.%I',tab);
  EXECUTE format('CREATE POLICY legacy_settlement_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.migration_allowed(project_id) AND accounting.ai_book_reviewer(bank_book_id))',tab);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION accounting.legacy_open_item_guard(),accounting.legacy_clearance_guard(),accounting.legacy_clearance_deferred_guard(),accounting.legacy_clearance_exclusive_guard(),accounting.legacy_draft_json(TEXT),accounting.legacy_clearance_journal_valid(TEXT),accounting.legacy_cent_text(BIGINT),accounting.legacy_clearance_fingerprint(TEXT),accounting.legacy_clearance_source_locked(TEXT,TEXT),accounting.legacy_open_item_used(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.legacy_clearance_fingerprint(TEXT),accounting.legacy_clearance_source_locked(TEXT,TEXT),accounting.legacy_open_item_used(TEXT) TO accounting_app;

