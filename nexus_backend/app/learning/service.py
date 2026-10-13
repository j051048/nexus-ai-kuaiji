"""Learning means reviewed lineage and explicit publication, never online training.

Rules are deliberately restricted to the source book and period. No sample from
another book is opened by consuming a rule, and no correction publishes itself.
"""
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.catalog import CatalogService
from ..accounting.domain import AccountingError
from ..accounting.models import cents_to_money, money_to_cents
from ..accounting.queries import page_bounds
from ..accounting.service import fingerprint, now
from .models import RuleConditions, RuleDefinition, RuleUpdate
from .governance import semantic_fingerprint


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def canonical_lines(lines):
    return sorted((row["account_code"], money_to_cents(row["debit"]), money_to_cents(row["credit"]),
                   encode(row.get("auxiliary_values", {}))) for row in lines)


class LearningService:
    def __init__(self, db):
        self.db, self.access, self.catalog = db, AccessControl(), CatalogService(db)

    def _book(self, conn, actor, book_id, write=False):
        book = self.access.book(conn, actor, book_id, "review" if write else "read")
        if write and book["period_status"] != "open":
            raise AccountingError("已关闭期间不能发布或修订企业规则", 403)
        return book

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute("INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                     (actor.firm_id, book_id, actor.user_id, action, object_id, encode(details), now()))

    def resolve_counterparty_in_transaction(self, conn, actor, book_id, original):
        """Resolve exact original names and independently approved aliases, never fuzzy text."""
        book = self.access.book(conn, actor, book_id)
        if self.db.is_postgres:
            value = conn.execute("SELECT accounting.learning_counterparty_identity(?,?)", (book_id, original)).fetchone()[0]
            return json.loads(value) if isinstance(value, str) else value
        rows = conn.execute("SELECT e.id,e.name,e.version,a.id AS alias_id,a.version AS alias_version FROM company_master_entities e LEFT JOIN company_master_aliases a ON a.entity_id=e.id AND a.firm_id=e.firm_id AND a.company_id=e.company_id AND a.kind='counterparty' AND a.original=? AND a.status='approved' AND a.reviewed_by IS NOT NULL AND a.reviewed_by<>a.created_by WHERE e.firm_id=? AND e.company_id=? AND e.kind='counterparty' AND e.active=1 AND (e.name=? OR a.id IS NOT NULL) ORDER BY e.id,a.id", (original, actor.firm_id, book['company_id'], original)).fetchall()
        ids = sorted({row['id'] for row in rows})
        status = 'resolved' if len(ids) == 1 else 'ambiguous' if ids else 'unresolved'
        return {'status': status, 'original': original, 'entity_id': ids[0] if len(ids) == 1 else None,
                'canonical_value': rows[0]['name'] if len(ids) == 1 else None,
                'entity_version': rows[0]['version'] if len(ids) == 1 else None,
                'alias_ids': sorted({row['alias_id'] for row in rows if row['alias_id']}),
                'alias_versions': sorted([[row['alias_id'], row['alias_version']] for row in rows if row['alias_id']])}

    def _identity(self, conn, actor, book_id, original, cache=None):
        if cache is None:
            return self.resolve_counterparty_in_transaction(conn, actor, book_id, original)
        if original not in cache:
            cache[original] = self.resolve_counterparty_in_transaction(conn, actor, book_id, original)
        return cache[original]

    def _identity_valid(self, conn, actor, book_id, definition, cache=None):
        if not definition.conditions.counterparty_id:
            return True
        identity = self._identity(conn, actor, book_id, definition.conditions.counterparty, cache)
        return identity['status'] == 'resolved' and identity['entity_id'] == definition.conditions.counterparty_id

    def matches_in_transaction(self, conn, actor, book_id, definition, snapshot, identity_cache=None):
        if not isinstance(definition, RuleDefinition):
            definition = RuleDefinition.model_validate(definition)
        c, payload = definition.conditions, snapshot['payload']
        base = definition.source_kind == snapshot['source_kind'] and c.business_type == snapshot['business_type'] and c.direction == payload.get('direction') and c.payment_status == payload.get('payment_status') and (c.category is None or c.category == payload.get('category'))
        if not base:
            return False
        if not c.counterparty_id:
            return c.counterparty == payload.get('counterparty')
        if not self._identity_valid(conn, actor, book_id, definition, identity_cache):
            return False
        identity = self._identity(conn, actor, book_id, payload.get('counterparty', ''), identity_cache)
        return identity['status'] == 'resolved' and identity['entity_id'] == c.counterparty_id

    def _master_fingerprint(self, conn, actor, book_id):
        book = self.access.book(conn, actor, book_id)
        if self.db.is_postgres:
            return conn.execute("SELECT accounting.learning_master_context(?)", (book_id,)).fetchone()[0]
        entities = [dict(row) for row in conn.execute("SELECT id,name,active,version FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind='counterparty' ORDER BY id", (actor.firm_id, book['company_id']))]
        aliases = [dict(row) for row in conn.execute("SELECT id,entity_id,original,status,version,created_by,reviewed_by FROM company_master_aliases WHERE firm_id=? AND company_id=? AND kind='counterparty' ORDER BY id", (actor.firm_id, book['company_id']))]
        return fingerprint({'entities': entities, 'aliases': aliases})

    def _overlaps(self, conn, actor, book_id, left, right, identity_cache=None):
        if left.source_kind != right.source_kind:
            return False
        a, b = left.conditions, right.conditions
        if any(getattr(a, key) != getattr(b, key) for key in ('business_type', 'direction', 'payment_status')) or (a.category is not None and b.category is not None and a.category != b.category):
            return False
        if not a.counterparty_id and not b.counterparty_id:
            return a.counterparty == b.counterparty
        if not self._identity_valid(conn, actor, book_id, left, identity_cache) or not self._identity_valid(conn, actor, book_id, right, identity_cache):
            return False
        left_id = a.counterparty_id or self._identity(conn, actor, book_id, a.counterparty, identity_cache)['entity_id']
        right_id = b.counterparty_id or self._identity(conn, actor, book_id, b.counterparty, identity_cache)['entity_id']
        return bool(left_id and left_id == right_id)

    def _rule_conflicts(self, conn, actor, book_id, definition, exclude_id=None, published_only=True, rows=None, identity_cache=None):
        rows = rows if rows is not None else conn.execute("SELECT r.id,r.current_version,r.status,v.definition_json FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version WHERE r.book_id=? AND r.firm_id=? AND r.status<>'retired' ORDER BY r.id", (book_id, actor.firm_id)).fetchall()
        return [{'rule_id': row['id'], 'version': row['current_version'], 'status': row['status'], 'name': json.loads(row['definition_json'])['name'],
                 'same_semantics': semantic_fingerprint(definition) == semantic_fingerprint(json.loads(row['definition_json']))}
                for row in rows if row['id'] != exclude_id and row['status'] != 'retired' and (not published_only or row['status'] == 'published') and self._overlaps(conn, actor, book_id, definition, RuleDefinition.model_validate_json(row['definition_json']), identity_cache)]

    def capture_in_transaction(self, conn, actor, book, draft, reviewed_value, reviewed_version):
        """Approval core calls this AFTER approval, in the very same transaction."""
        book = self._book(conn, actor, book["id"], True)
        current = conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft["id"], book["id"])).fetchone()
        if not current or current["status"] != "approved" or current["version"] != reviewed_version + 1 or current["approved_by"] != actor.user_id or reviewed_value.get("issues"):
            raise AccountingError("只能从当前完整审核通过的凭证捕获样例", 409)
        existing = conn.execute("SELECT id FROM learning_samples WHERE draft_id=? AND book_id=?", (draft["id"], book["id"])).fetchone()
        if existing:
            return {"id": existing["id"], "captured": False}
        source = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], book["id"])).fetchone()
        if source is None:
            raise AccountingError("审核样例缺少来源", 409)
        payload = {"occurred_on": source["date"], "description": source["description"], "counterparty": source["counterparty"],
                   "amount": cents_to_money(source["amount_cents"]), "direction": source["direction"],
                   "payment_status": "paid", "category": "other", "project": ""}
        event = None
        if source["source_kind"] == "business":
            event = conn.execute("SELECT e.* FROM business_mappings m JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id WHERE m.draft_id=? AND m.book_id=? AND m.source_id=?", (draft["id"], book["id"], source["id"])).fetchone()
            if not event or event["company_id"] != book["company_id"] or event["firm_id"] != actor.firm_id:
                raise AccountingError("审核样例业务归属无效", 409)
            payload = json.loads(event["payload"])
        edits = conn.execute("SELECT user_id,details FROM audit_events WHERE book_id=? AND object_id=? AND action='draft.update' ORDER BY id", (book["id"], draft["id"])).fetchall()
        final = {"description": reviewed_value["description"], "lines": reviewed_value["lines"]}
        original = json.loads(edits[0]["details"]).get("before", final) if edits else final
        original = {"description": original.get("description", final["description"]), "lines": original.get("lines", final["lines"])}
        reasons = [{"user_id": row["user_id"], "reason": json.loads(row["details"]).get("reason", ""),
                    "version": json.loads(row["details"]).get("after", {}).get("version")} for row in edits]
        changed = canonical_lines(original["lines"]) != canonical_lines(final["lines"]) or original["description"] != final["description"]
        attachments = [dict(row) for row in conn.execute("SELECT id,sha256 FROM business_attachments WHERE event_id=? ORDER BY id", (event["id"],))] if event else []
        recognized = self._recognized(conn, event, source["amount_cents"])
        snapshot = {"source_kind": source["source_kind"], "source": {key: source[key] for key in ("id", "date", "counterparty", "direction", "amount_cents", "fingerprint")},
                    "business_type": event["template_id"] if event else "daily_cashflow", "payload": payload,
                    "context": {"department_id": event["department_id"], "applicant_id": event["applicant_id"]} if event else {},
                    "fact_version": event["fact_version"] if event else None, "recognized": recognized}
        snapshot['counterparty_identity'] = self.resolve_counterparty_in_transaction(conn, actor, book['id'], payload['counterparty'])
        extraction_facts = [dict(row) for row in conn.execute("SELECT id,input_fingerprint,model,prompt_version,status FROM ai_runs WHERE event_id=? AND kind='ocr' ORDER BY id", (event["id"],))] if event else []
        evidence = fingerprint({"evidence_ref": reviewed_value["evidence_ref"], "attachments": attachments, "extraction_facts": extraction_facts,
                                "fact_version": snapshot["fact_version"], "source": source["fingerprint"]})
        sample_id = uuid4().hex
        conn.execute("INSERT INTO learning_samples(id,firm_id,company_id,book_id,period,draft_id,source_id,reviewed_version,approved_version,snapshot_json,original_json,final_json,corrections_json,evidence_fingerprint,sample_fingerprint,corrected,reviewed_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (sample_id, actor.firm_id, book["company_id"], book["id"], book["period"], draft["id"], source["id"], reviewed_version, current["version"],
                      encode(snapshot), encode(original), encode(final), encode(reasons), evidence, fingerprint({"snapshot": snapshot, "final": final, "evidence": evidence}), int(changed), actor.user_id, now()))
        self._audit(conn, actor, book["id"], "learning.sample.capture", sample_id, {"draft_id": draft["id"], "reviewed_version": reviewed_version, "corrected": changed})
        # Suggest only an unambiguous fixed formula faithfully reproducing the
        # reviewed result. Unknown or equal net/tax values are never guessed.
        if changed and self._ordinary_sample(conn, book["id"], sample_id) and payload["counterparty"].strip() and payload["counterparty"] == payload["counterparty"].strip():
            lines = final["lines"]
            amounts = {"gross": source["amount_cents"]} | {key: money_to_cents(value) for key, value in recognized.items()}
            inferred = []
            for line in lines:
                amount = money_to_cents(line["debit"]) or money_to_cents(line["credit"])
                fields = [key for key, value in amounts.items() if value == amount]
                # A zero-tax invoice may make gross==net; gross is sufficient
                # only when every line is the reviewed full-amount journal.
                field = "gross" if amount == source["amount_cents"] and len(lines) == 2 else fields[0] if len(fields) == 1 else None
                if field is None:
                    inferred = []
                    break
                inferred.append({"account_code": line["account_code"], "side": "debit" if money_to_cents(line["debit"]) else "credit", "amount_field": field})
            if 2 <= len(inferred) <= 20:
                definition = RuleDefinition(name=f"修正样例 · {payload['counterparty']}"[:120], source_kind=source["source_kind"],
                    conditions=RuleConditions(business_type=snapshot["business_type"], direction=payload["direction"], category=payload["category"] if event else None,
                                              payment_status=payload["payment_status"], counterparty=payload["counterparty"]),
                    lines=inferred)
                if snapshot['counterparty_identity']['status'] == 'resolved':
                    definition.conditions.counterparty_id = snapshot['counterparty_identity']['entity_id']
                rendered = self._render_sample(conn, actor, book["id"], definition, snapshot)
                if not rendered["issues"] and canonical_lines(self._public_lines(rendered)) == canonical_lines(lines):
                    duplicates = [row for row in self._rule_conflicts(conn, actor, book['id'], definition, published_only=False) if row['same_semantics']]
                    inherited = [row['id'] for row in conn.execute("SELECT id,definition_json FROM ledger_rule_candidates WHERE target_book_id=? AND status='pending'", (book['id'],)) if semantic_fingerprint(json.loads(row['definition_json'])) == semantic_fingerprint(definition)]
                    if duplicates or inherited:
                        self._audit(conn, actor, book['id'], 'learning.candidate.deduplicated', sample_id, {'rule_ids': [row['rule_id'] for row in duplicates], 'inherited_candidate_ids': inherited, 'sample_ids_merged': False, 'semantic_fingerprint': semantic_fingerprint(definition)})
                    else:
                        self._save_in_transaction(conn, actor, book, definition, [sample_id], "从已审核人工修正生成的待发布建议；须补充处理依据并完成正反例评估", None)
        return {"id": sample_id, "captured": True}

    @staticmethod
    def _recognized(conn, event, amount):
        if not event:
            return {}
        rows = conn.execute("SELECT r.result_json FROM ai_runs r WHERE r.event_id=? AND r.kind='ocr' AND r.status='succeeded' AND r.input_version=? AND NOT EXISTS(SELECT 1 FROM case_supersessions retired WHERE retired.old_attachment_id=r.attachment_id AND retired.event_id=r.event_id) AND NOT EXISTS(SELECT 1 FROM ai_runs n WHERE n.event_id=r.event_id AND n.attachment_id=r.attachment_id AND n.kind='ocr' AND (n.created_at>r.created_at OR n.created_at=r.created_at AND n.id>r.id))", (event["id"], event["fact_version"])).fetchall()
        documents = [doc for row in rows for doc in json.loads(row["result_json"])["documents"]]
        try:
            if not documents or any(doc.get("currency") != "CNY" or any(doc.get(key) is None for key in ("gross_amount", "net_amount", "tax_amount")) for doc in documents):
                return {}
            gross, net, tax = (sum(money_to_cents(doc[key]) for doc in documents) for key in ("gross_amount", "net_amount", "tax_amount"))
            return {"net": cents_to_money(net), "tax": cents_to_money(tax)} if gross == amount == net + tax else {}
        except (KeyError, ValueError, AccountingError):
            return {}

    @staticmethod
    def _matches(definition, snapshot):
        c, payload = definition.conditions, snapshot["payload"]
        return definition.source_kind == snapshot["source_kind"] and c.business_type == snapshot["business_type"] and c.direction == payload.get("direction") and c.payment_status == payload.get("payment_status") and (c.category is None or c.category == payload.get("category")) and c.counterparty == payload.get("counterparty")

    @staticmethod
    def _public_lines(result):
        return [{"account_code": code, "debit": cents_to_money(debit), "credit": cents_to_money(credit), "auxiliary_values": aux}
                for (code, debit, credit), aux in zip(result["lines"], result["auxiliaries"])]

    def _render_sample(self, conn, actor, book_id, definition, snapshot):
        return self.catalog._render(conn, actor, book_id, definition.as_template(), snapshot["payload"],
                                    money_to_cents(snapshot["payload"]["amount"]), snapshot["context"], snapshot["recognized"])

    def _sample(self, conn, actor, book_id, sample_id):
        self._book(conn, actor, book_id)
        row = conn.execute("SELECT s.* FROM learning_samples s JOIN drafts d ON d.id=s.draft_id AND d.book_id=s.book_id WHERE s.id=? AND s.book_id=? AND s.firm_id=? AND d.status IN ('approved','exported') AND d.approved_by=s.reviewed_by AND d.version>=s.approved_version", (sample_id, book_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError("审核样例不存在或无权访问", 404)
        return row

    def _ordinary_sample(self, conn, book_id, sample_id):
        if self.db.is_postgres:
            return bool(conn.execute("SELECT accounting.learning_sample_ordinary(?,?)", (book_id, sample_id)).fetchone()[0])
        return conn.execute("SELECT 1 FROM learning_samples x WHERE x.id=? AND x.book_id=? AND NOT EXISTS(SELECT 1 FROM sources s JOIN settlement_allocations a ON a.bank_source_id=s.id WHERE s.id=x.source_id AND s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM settlement_reversals r WHERE r.allocation_id=a.id))", (sample_id, book_id)).fetchone() is not None

    def _require_ordinary_samples(self, conn, book_id, sample_ids):
        if any(not self._ordinary_sample(conn, book_id, sample_id) for sample_id in sample_ids):
            raise AccountingError("银行清偿样例保留供追溯，但不能用于普通分类规则的正例、反例或发布；清偿须绑定实际核销和原应收应付凭证", 409)

    def _eligibility(self, conn, book_id, row):
        eligible = self._ordinary_sample(conn, book_id, row["id"])
        return {"ordinary_rule_eligible": eligible, "rule_scope_reason": None if eligible else "核销清偿样例仅供追溯，不可推广为普通流水分类规则"}

    @staticmethod
    def _sample_summary(row):
        snapshot = json.loads(row["snapshot_json"])
        return {key: row[key] for key in ("id", "draft_id", "reviewed_version", "reviewed_by", "created_at", "sample_fingerprint")} | {
            "corrected": bool(row["corrected"]), "source_kind": snapshot["source_kind"], "business_type": snapshot["business_type"],
            "counterparty": snapshot["payload"]["counterparty"], "amount": snapshot["payload"]["amount"], "direction": snapshot["payload"]["direction"],
            'counterparty_identity': snapshot.get('counterparty_identity')}

    def get_sample(self, actor, book_id, sample_id):
        with self.db.transaction(actor=actor) as conn:
            row = self._sample(conn, actor, book_id, sample_id)
            return self._sample_summary(row) | self._eligibility(conn,book_id,row) | {key[:-5]: json.loads(row[key]) for key in ("snapshot_json", "original_json", "final_json", "corrections_json")} | {"evidence_fingerprint": row["evidence_fingerprint"]}

    @staticmethod
    def _rule_json(row):
        return {key: row[key] for key in ("id", "book_id", "company_id", "period", "current_version", "state_version", "status", "created_at", "updated_at")} | {
            "definition": json.loads(row["definition_json"]), "sample_ids": json.loads(row["sample_ids_json"]), "reason": row["reason"], "evaluation_id": row["evaluation_id"],
            'semantic_fingerprint': semantic_fingerprint(json.loads(row['definition_json']))}

    def overview(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            total = conn.execute("SELECT count(*) FROM learning_samples WHERE book_id=? AND firm_id=?", (book_id, actor.firm_id)).fetchone()[0]
            rows = conn.execute("SELECT * FROM learning_samples WHERE book_id=? AND firm_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (book_id, actor.firm_id, limit, offset)).fetchall()
            rules = conn.execute("SELECT r.*,v.definition_json,v.sample_ids_json,v.reason FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version AND v.book_id=r.book_id WHERE r.book_id=? AND r.firm_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT 100", (book_id, actor.firm_id)).fetchall()
            return {"book_id": book_id, "company_id": book["company_id"], "book_name": book["name"], "period": book["period"],
                    "can_manage": bool(book["permissions"]["review"] and book["period_status"] == "open"), "samples": [self._sample_summary(row) | self._eligibility(conn,book_id,row) for row in rows],
                    "rules": [self._rule_json(row) for row in rules], "pagination": {"limit": limit, "offset": offset, "total": total, "has_more": offset + len(rows) < total}}

    def _rule(self, conn, actor, book_id, rule_id, write=False):
        self._book(conn, actor, book_id, write)
        row = conn.execute("SELECT r.*,v.definition_json,v.definition_fingerprint,v.sample_ids_json,v.reason FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version AND v.book_id=r.book_id WHERE r.id=? AND r.book_id=? AND r.firm_id=?", (rule_id, book_id, actor.firm_id)).fetchone()
        if not row:
            raise AccountingError("企业规则不存在或无权访问", 404)
        return row

    def get_rule(self, actor, book_id, rule_id):
        with self.db.transaction(actor=actor) as conn:
            row = self._rule(conn, actor, book_id, rule_id)
            versions = [dict(item) for item in conn.execute("SELECT version,definition_json,sample_ids_json,reason,created_by,created_at FROM learning_rule_versions WHERE rule_id=? AND book_id=? ORDER BY version DESC LIMIT 100", (rule_id, book_id))]
            return self._rule_json(row) | {"versions": [{key: value for key, value in item.items() if key not in {"definition_json", "sample_ids_json"}} | {
                "definition": json.loads(item["definition_json"]), "sample_ids": json.loads(item["sample_ids_json"])} for item in versions]}

    def save_rule(self, actor, book_id, request, rule_id=None):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, True)
            if rule_id:
                prior = self._rule(conn, actor, book_id, rule_id, True)
                if not isinstance(request, RuleUpdate) or prior["current_version"] != request.expected_version or prior["state_version"] != request.expected_state_version:
                    raise AccountingError("企业规则版本或发布状态已变化", 409)
            return self._save_in_transaction(conn, actor, book, request.definition, request.sample_ids, request.reason, rule_id)

    def _save_in_transaction(self, conn, actor, book, definition, sample_ids, reason, rule_id):
        self.catalog._validate_template(conn, book["id"], definition.as_template())
        if not self._identity_valid(conn, actor, book['id'], definition):
            raise AccountingError('稳定往来方必须属于本企业且原文可由当前有效主数据或独立审核别名唯一匹配；请处理歧义后再保存', 409)
        for sample_id in sample_ids:
            sample = self._sample(conn, actor, book["id"], sample_id)
            self._require_ordinary_samples(conn, book["id"], [sample_id])
            snapshot = json.loads(sample["snapshot_json"])
            if not self.matches_in_transaction(conn, actor, book['id'], definition, snapshot):
                raise AccountingError("规则条件不匹配选中的已审核正例")
        duplicates = [row for row in self._rule_conflicts(conn, actor, book['id'], definition, rule_id, False) if row['same_semantics']]
        if duplicates:
            raise AccountingError(f"已有同语义规则 {duplicates[0]['name']}（{duplicates[0]['rule_id']}），请复用或显式修订；审核样例不会自动合并", 409)
        stamp = now()
        if rule_id:
            previous = self._rule(conn, actor, book["id"], rule_id, True)
            version = previous["current_version"] + 1
            conn.execute("UPDATE learning_rules SET current_version=?,state_version=state_version+1,status='draft',evaluation_id=NULL,updated_at=? WHERE id=? AND book_id=?", (version, stamp, rule_id, book["id"]))
        else:
            rule_id, version = uuid4().hex, 1
            conn.execute("INSERT INTO learning_rules(id,firm_id,company_id,book_id,period,current_version,state_version,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,1,1,'draft',?,?,?)", (rule_id, actor.firm_id, book["company_id"], book["id"], book["period"], actor.user_id, stamp, stamp))
        conn.execute("INSERT INTO learning_rule_versions(rule_id,book_id,version,definition_json,definition_fingerprint,sample_ids_json,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (rule_id, book["id"], version, encode(definition.model_dump()), fingerprint(definition.model_dump()), encode(sorted(sample_ids)), reason, actor.user_id, stamp))
        self._audit(conn, actor, book["id"], "learning.rule.revise" if version > 1 else "learning.rule.create", rule_id, {"version": version, "sample_ids": sample_ids, "reason": reason})
        return self._rule_json(self._rule(conn, actor, book["id"], rule_id))

    def _evaluation(self, conn, actor, book_id, rule, negative_ids):
        definition = RuleDefinition.model_validate_json(rule["definition_json"])
        positive_ids = json.loads(rule["sample_ids_json"])
        if set(positive_ids) & set(negative_ids):
            raise AccountingError("正例和反例必须为不同已审核事项")
        positives = [self._sample(conn, actor, book_id, sample_id) for sample_id in positive_ids]
        negatives = [self._sample(conn, actor, book_id, sample_id) for sample_id in negative_ids]
        self._require_ordinary_samples(conn, book_id, [*positive_ids, *negative_ids])
        rows = conn.execute("SELECT * FROM learning_samples WHERE book_id=? AND firm_id=? ORDER BY id LIMIT 2001", (book_id, actor.firm_id)).fetchall()
        if len(rows) > 2000:
            raise AccountingError("样例超过本期 MVP 评估上限，请先划分规则评估范围", 409)
        failures, matched, identity_cache = [], 0, {}
        for sample in rows:
            snapshot = json.loads(sample["snapshot_json"])
            if not self.matches_in_transaction(conn, actor, book_id, definition, snapshot, identity_cache):
                continue
            matched += 1
            try:
                result = self._render_sample(conn, actor, book_id, definition, snapshot)
                if result["issues"] or canonical_lines(self._public_lines(result)) != canonical_lines(json.loads(sample["final_json"])["lines"]):
                    failures.append({"sample_id": sample["id"], "reason": "规则分录与已审核处理或辅助核算不一致"})
            except AccountingError as error:
                failures.append({"sample_id": sample["id"], "reason": str(error)})
        for sample in negatives:
            if self.matches_in_transaction(conn, actor, book_id, definition, json.loads(sample["snapshot_json"]), identity_cache):
                failures.append({"sample_id": sample["id"], "reason": "规则错误匹配会计选定的反例"})
        if any(not self.matches_in_transaction(conn, actor, book_id, definition, json.loads(sample["snapshot_json"]), identity_cache) for sample in positives):
            failures.append({"sample_id": "", "reason": "规则未匹配全部选定正例"})
        conflicts = self._rule_conflicts(conn, actor, book_id, definition, rule['id'], identity_cache=identity_cache)
        if conflicts:
            failures.append({'sample_id': '', 'reason': '与当前已发布规则适用范围重叠，须先停用或修订冲突规则'})
        catalog = [dict(row) for row in conn.execute("SELECT code,active,version,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code", (book_id,))]
        digest = fingerprint({"definition": rule["definition_fingerprint"], "catalog": catalog,
                              "samples": [(row["id"], row["sample_fingerprint"]) for row in rows], "positive": positive_ids, "negative": sorted(negative_ids),
                              'master': self._master_fingerprint(conn, actor, book_id), 'conflicts': conflicts})
        return {"passed": not failures, "positive_count": len(positives), "negative_count": len(negatives), "scanned_count": len(rows), "matched_count": matched, "failures": failures, "input_fingerprint": digest, 'conflicts': conflicts}

    def evaluate(self, actor, book_id, rule_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            rule = self._rule(conn, actor, book_id, rule_id, True)
            if rule["current_version"] != request.expected_version or rule["status"] != "draft":
                raise AccountingError("仅可评估当前待发布版本", 409)
            result = self._evaluation(conn, actor, book_id, rule, request.negative_sample_ids)
            evaluation_id = uuid4().hex
            conn.execute("INSERT INTO learning_rule_evaluations(id,rule_id,book_id,rule_version,negative_sample_ids_json,input_fingerprint,report_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (evaluation_id, rule_id, book_id, rule["current_version"], encode(request.negative_sample_ids), result["input_fingerprint"], encode(result), actor.user_id, now()))
            self._audit(conn, actor, book_id, "learning.rule.evaluate", rule_id, {"evaluation_id": evaluation_id, "passed": result["passed"], "version": rule["current_version"]})
            return {"id": evaluation_id, **result}

    def publish(self, actor, book_id, rule_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            rule = self._rule(conn, actor, book_id, rule_id, True)
            self._version(rule, request)
            if rule["status"] != "draft":
                raise AccountingError("仅可显式发布待发布规则", 409)
            evaluation = conn.execute("SELECT * FROM learning_rule_evaluations WHERE id=? AND rule_id=? AND book_id=? AND rule_version=?", (request.evaluation_id, rule_id, book_id, rule["current_version"])).fetchone()
            if not evaluation or not json.loads(evaluation["report_json"])["passed"]:
                raise AccountingError("发布规则须先通过已审核正例与反例评估", 409)
            current = self._evaluation(conn, actor, book_id, rule, json.loads(evaluation["negative_sample_ids_json"]))
            if not current["passed"] or current["input_fingerprint"] != evaluation["input_fingerprint"]:
                raise AccountingError("样例或科目已变化，请重新评估后发布", 409)
            conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1,evaluation_id=?,updated_at=? WHERE id=? AND book_id=?", (evaluation["id"], now(), rule_id, book_id))
            self._audit(conn, actor, book_id, "learning.rule.publish", rule_id, {"version": rule["current_version"], "evaluation_id": evaluation["id"], "reason": request.reason})
            return self._rule_json(self._rule(conn, actor, book_id, rule_id))

    @staticmethod
    def _version(rule, request):
        if rule["current_version"] != request.expected_version or rule["state_version"] != request.expected_state_version:
            raise AccountingError("企业规则版本或状态已变化，请刷新", 409)

    def retire(self, actor, book_id, rule_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            rule = self._rule(conn, actor, book_id, rule_id, True)
            self._version(rule, request)
            if rule["status"] == "retired":
                raise AccountingError("规则已停用", 409)
            conn.execute("UPDATE learning_rules SET status='retired',state_version=state_version+1,updated_at=? WHERE id=? AND book_id=?", (now(), rule_id, book_id))
            self._audit(conn, actor, book_id, "learning.rule.retire", rule_id, {"version": rule["current_version"], "reason": request.reason})
            return self._rule_json(self._rule(conn, actor, book_id, rule_id))

    def policy_fingerprint(self, conn, actor, book_id):
        self._book(conn, actor, book_id)
        rows = [dict(row) for row in conn.execute("SELECT r.id,r.current_version,r.state_version,v.definition_fingerprint FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version WHERE r.book_id=? AND r.firm_id=? AND r.status='published' ORDER BY r.id", (book_id, actor.firm_id))]
        return fingerprint({'rules': rows, 'master': self._master_fingerprint(conn, actor, book_id)})

    def shadow_replay_in_transaction(self, conn, actor, book_id, definition, sample_ids=None):
        """Read-only replay against frozen reviewed answers; never updates samples or drafts."""
        self._book(conn, actor, book_id)
        definition = definition if isinstance(definition, RuleDefinition) else RuleDefinition.model_validate(definition)
        rows = conn.execute('SELECT * FROM learning_samples WHERE book_id=? AND firm_id=? ORDER BY id LIMIT 2001', (book_id, actor.firm_id)).fetchall()
        if len(rows) > 2000:
            raise AccountingError('本期回放样例超过 2000 条，请缩小试点范围', 409)
        if sample_ids is not None:
            chosen = set(sample_ids)
            available = {row['id'] for row in rows}
            if not chosen <= available:
                raise AccountingError('回放样例不存在或无权访问', 404)
            rows = [row for row in rows if row['id'] in chosen]
        outcomes, identity_cache = [], {}
        for sample in rows:
            if not self._ordinary_sample(conn, book_id, sample['id']):
                outcomes.append({'sample_id': sample['id'], 'status': 'excluded_clearance', 'matched': False, 'equal': None})
                continue
            snapshot = json.loads(sample['snapshot_json'])
            matched = self.matches_in_transaction(conn, actor, book_id, definition, snapshot, identity_cache)
            result = None
            issues = []
            if matched:
                try:
                    rendered = self._render_sample(conn, actor, book_id, definition, snapshot)
                    issues = rendered['issues']
                    result = self._public_lines(rendered)
                except AccountingError as error:
                    issues = [str(error)]
            equal = not issues and result is not None and canonical_lines(result) == canonical_lines(json.loads(sample['final_json'])['lines']) if matched else None
            outcomes.append({'sample_id': sample['id'], 'status': 'matched' if matched else 'uncovered', 'matched': matched, 'equal': equal, 'lines': result, 'issues': issues})
        return {'semantic_fingerprint': semantic_fingerprint(definition), 'scanned_count': len(outcomes),
                'matched_count': sum(row['matched'] for row in outcomes), 'equal_count': sum(row['equal'] is True for row in outcomes),
                'different_count': sum(row['equal'] is False for row in outcomes), 'outcomes': outcomes,
                'input_fingerprint': fingerprint({'definition': definition.model_dump(), 'master': self._master_fingerprint(conn, actor, book_id), 'samples': [(row['id'], row['sample_fingerprint']) for row in rows]})}

    def governance(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            rows = conn.execute("SELECT r.*,v.definition_json,v.sample_ids_json,v.reason FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version WHERE r.book_id=? AND r.firm_id=? ORDER BY r.id LIMIT 201", (book_id, actor.firm_id)).fetchall()
            if len(rows) > 200:
                raise AccountingError('本期规则超过 200 条治理上限，请先整理试点规则', 409)
            groups, identity_cache = {}, {}
            conflicts = []
            for row in rows:
                definition = RuleDefinition.model_validate_json(row['definition_json'])
                semantic = semantic_fingerprint(definition)
                if row['status'] != 'retired':
                    groups.setdefault(semantic, []).append(row['id'])
                overlaps = self._rule_conflicts(conn, actor, book_id, definition, row['id'], rows=rows, identity_cache=identity_cache)
                if overlaps:
                    conflicts.append({'rule_id': row['id'], 'version': row['current_version'], 'status': row['status'], 'conflicts': overlaps})
            # Only observed, reviewed lineage counts as a hit. Suggestions awaiting
            # review and historical replay are reported separately from production use.
            samples = conn.execute('SELECT * FROM learning_samples WHERE book_id=? AND firm_id=? ORDER BY id LIMIT 2001', (book_id, actor.firm_id)).fetchall()
            if len(samples) > 2000:
                raise AccountingError('本期样例超过 2000 条治理上限，请缩小试点范围', 409)
            events = conn.execute("SELECT object_id,details,created_at,id FROM audit_events WHERE book_id=? AND action IN ('draft.company_rule.apply','draft.suggest') ORDER BY id", (book_id,)).fetchall()
            origins = {}
            for event in events:
                value = json.loads(event['details'])
                rule_id = value.get('rule_id') or value.get('company_rule_id')
                version = value.get('rule_version') or value.get('company_rule_version')
                if rule_id and type(version) is int:
                    origins.setdefault(event['object_id'], []).append((event['created_at'], value.get('applied_version', 1), rule_id, version))
            stats = {}
            versions = {(row['rule_id'], row['version']): RuleDefinition.model_validate_json(row['definition_json']) for row in conn.execute('SELECT rule_id,version,definition_json FROM learning_rule_versions WHERE book_id=?', (book_id,))}
            for sample in samples:
                lineage = [row for row in origins.get(sample['draft_id'], []) if row[0] <= sample['created_at'] and row[1] <= sample['reviewed_version']]
                if not lineage:
                    continue
                _, _, rule_id, version = lineage[-1]
                definition = versions.get((rule_id, version))
                if not definition:
                    continue
                item = stats.setdefault((rule_id, version), {'rule_id': rule_id, 'version': version, 'reviewed_hits': 0, 'accountant_modified': 0, 'unchanged': 0, 'render_unavailable': 0})
                item['reviewed_hits'] += 1
                try:
                    rendered = self._render_sample(conn, actor, book_id, definition, json.loads(sample['snapshot_json']))
                    if rendered['issues']:
                        item['render_unavailable'] += 1
                    elif canonical_lines(self._public_lines(rendered)) == canonical_lines(json.loads(sample['final_json'])['lines']):
                        item['unchanged'] += 1
                    else:
                        item['accountant_modified'] += 1
                except AccountingError:
                    item['render_unavailable'] += 1
            return {'book_id': book_id, 'company_id': book['company_id'], 'period': book['period'], 'conflicts': conflicts,
                    'duplicate_groups': [{'semantic_fingerprint': key, 'rule_ids': values} for key, values in groups.items() if len(values) > 1],
                    'usage': [item | {'modified_rate': item['accountant_modified'] / item['reviewed_hits'] if item['reviewed_hits'] else None} for _, item in sorted(stats.items())],
                    'master_fingerprint': self._master_fingerprint(conn, actor, book_id), 'sample_count': len(samples),
                    'counterparties': [dict(row) for row in conn.execute("SELECT id,code,name,version FROM company_master_entities WHERE firm_id=? AND company_id=? AND kind='counterparty' AND active=1 ORDER BY code,id LIMIT 500", (actor.firm_id, book['company_id']))]}

    def rule_generate_in_transaction(self, conn, actor, book_id, business_type, payload, amount, context=None, recognized=None, source_kind="business"):
        book = self.access.book(conn, actor, book_id, "edit")
        if book["period_status"] != "open":
            raise AccountingError("期间已关闭，不能生成规则草稿", 403)
        snapshot = {"source_kind": source_kind, "business_type": business_type, "payload": payload, "context": context or {}, "recognized": recognized or {}}
        rows = conn.execute("SELECT r.id,r.current_version,v.definition_json,v.sample_ids_json FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version WHERE r.book_id=? AND r.firm_id=? AND r.company_id=? AND r.period=? AND r.status='published' ORDER BY r.id", (book_id, actor.firm_id, book["company_id"], book["period"])).fetchall()
        candidates = [(row, RuleDefinition.model_validate_json(row["definition_json"])) for row in rows if all(self._ordinary_sample(conn,book_id,sample_id) for sample_id in json.loads(row["sample_ids_json"]))]
        identity_cache = {}
        candidates = [(row, definition) for row, definition in candidates if self.matches_in_transaction(conn, actor, book_id, definition, snapshot, identity_cache)]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise AccountingError("多个已发布企业规则匹配事项，请会计处理规则冲突", 409)
        row, definition = candidates[0]
        if type(amount) is not int or amount != money_to_cents(payload["amount"]):
            raise AccountingError("规则金额与来源不一致")
        result = self._render_sample(conn, actor, book_id, definition, snapshot)
        return {**result, "rule_id": row["id"], "rule_version": row["current_version"],
                "template_id": "company-rule:" + row["id"], "template_version": row["current_version"],
                "explanation": f"经会计发布的企业规则“{definition.name}”第 {row['current_version']} 版；限定本账套及期间，仍须会计独立复核。"}
