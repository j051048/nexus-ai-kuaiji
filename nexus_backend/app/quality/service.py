"""Bounded evidence replay. Human labels are independent of model predictions."""

import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.catalog import CatalogService
from ..accounting.catalog_models import TemplateDefinition
from ..accounting.domain import AccountingError
from ..accounting.models import cents_to_money, money_to_cents
from ..accounting.queries import inactive_source_sql, page_bounds
from ..accounting.service import fingerprint, now
from ..learning.models import RuleDefinition
from ..learning.service import LearningService, canonical_lines, encode
from ..operations.efficiency import EfficiencyService
from ..delivery.service import DeliveryService
from .queries import report_groups


class QualityService:
    def __init__(self, db):
        self.db, self.access = db, AccessControl()
        self.learning, self.catalog = LearningService(db), CatalogService(db)

    def _sample(self, conn, actor, book_id, sample_id):
        self.access.book(conn, actor, book_id, "review")
        return self.learning._sample(conn, actor, book_id, sample_id)

    def _evidence(self, conn, sample):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.quality_evidence(?,?)", (sample["book_id"], sample["id"])
            ).fetchone()[0]
        mapping = conn.execute(
            "SELECT event_id FROM business_mappings WHERE book_id=? AND draft_id=? AND source_id=?",
            (sample["book_id"], sample["draft_id"], sample["source_id"]),
        ).fetchone()
        source = conn.execute(
            "SELECT * FROM sources WHERE book_id=? AND id=?",
            (sample["book_id"], sample["source_id"]),
        ).fetchone()
        addition = conn.execute(
            "SELECT evidence_ref FROM source_evidence WHERE source_id=? AND book_id=? ORDER BY id DESC LIMIT 1",
            (sample["source_id"], sample["book_id"]),
        ).fetchone()
        event_id = mapping["event_id"] if mapping else None
        attachments = [
            dict(row)
            for row in conn.execute(
                "SELECT id,sha256,size_bytes,filename,media_type FROM business_attachments WHERE event_id=? ORDER BY id",
                (event_id,),
            )
        ]
        fact = json.loads(sample["snapshot_json"]).get("fact_version")
        rows = conn.execute(
            "SELECT id,attachment_id,input_version,input_fingerprint,provider,model,prompt_version,result_json FROM ai_runs WHERE event_id=? AND kind='ocr' AND status='succeeded' AND input_version=? AND NOT EXISTS(SELECT 1 FROM case_supersessions x WHERE x.old_attachment_id=ai_runs.attachment_id AND x.event_id=ai_runs.event_id) ORDER BY id",
            (event_id, fact),
        ).fetchall()
        return {
            "event_id": event_id,
            "evidence_ref": addition["evidence_ref"] if addition else source["evidence_ref"],
            "attachments": attachments,
            "ocr": [
                {key: row[key] for key in row.keys() if key != "result_json"}
                | {"result": json.loads(row["result_json"])}
                for row in rows
            ],
        }

    def _current(self, conn, actor, standard):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.quality_standard_current(?)", (standard["id"],)
            ).fetchone()[0]
        sample = self._sample(conn, actor, standard["book_id"], standard["sample_id"])
        source = conn.execute(
            f"SELECT *,{inactive_source_sql()} AS inactive FROM sources s WHERE id=? AND book_id=?",
            (sample["source_id"], sample["book_id"]),
        ).fetchone()
        if (
            source["inactive"]
            or conn.execute(
                "SELECT 1 FROM fact_revisions WHERE old_book_id=? AND old_source_id=? AND state='pending'",
                (sample["book_id"], sample["source_id"]),
            ).fetchone()
        ):
            return False
        snapshot = json.loads(sample["snapshot_json"])
        if snapshot["fact_version"] is not None:
            event = conn.execute(
                "SELECT e.* FROM business_mappings m JOIN business_events e ON e.id=m.event_id WHERE m.book_id=? AND m.source_id=?",
                (sample["book_id"], sample["source_id"]),
            ).fetchone()
            if (
                not event
                or event["fact_version"] != snapshot["fact_version"]
                or json.loads(event["payload"]) != snapshot["payload"]
            ):
                return False
        return self._evidence(conn, sample) == json.loads(standard["evidence_json"])

    def _standard(self, conn, actor, book_id, standard_id):
        self.access.book(conn, actor, book_id, "review")
        row = conn.execute(
            "SELECT * FROM quality_standards WHERE id=? AND book_id=? AND firm_id=?",
            (standard_id, book_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("评测标准不存在或无权访问", 404)
        return row

    def _standard_json(self, conn, actor, row, detailed=False):
        confirmation = conn.execute(
            "SELECT confirmed_by,reason,created_at FROM quality_confirmations WHERE standard_id=? AND book_id=?",
            (row["id"], row["book_id"]),
        ).fetchone()
        snapshot = json.loads(row["snapshot_json"])
        value = {
            key: row[key]
            for key in (
                "id",
                "book_id",
                "sample_id",
                "draft_id",
                "source_id",
                "period",
                "created_by",
                "created_at",
                "reason",
            )
        } | {
            "confirmed": bool(confirmation),
            "confirmation": dict(confirmation) if confirmation else None,
            "stale": not self._current(conn, actor, row),
            "business_type": snapshot["business_type"],
            "amount": snapshot["payload"]["amount"],
            "counterparty": snapshot["payload"]["counterparty"],
            "label_count": len(json.loads(row["labels_json"])),
            "can_confirm": row["created_by"] != actor.user_id and not confirmation,
        }
        if detailed:
            value |= {
                "snapshot": snapshot,
                "final": json.loads(row["final_json"]),
                "evidence": json.loads(row["evidence_json"]),
                "field_labels": json.loads(row["labels_json"]),
            }
        return value

    def standard(self, actor, book_id, standard_id):
        with self.db.transaction(actor=actor) as conn:
            return self._standard_json(
                conn, actor, self._standard(conn, actor, book_id, standard_id), True
            )

    def preview(self, actor, book_id, sample_id):
        with self.db.transaction(actor=actor) as conn:
            sample = self._sample(conn, actor, book_id, sample_id)
            return {
                "sample_id": sample["id"],
                "draft_id": sample["draft_id"],
                "snapshot": json.loads(sample["snapshot_json"]),
                "final": json.loads(sample["final_json"]),
                "evidence": self._evidence(conn, sample),
                "reviewed_by": sample["reviewed_by"],
            }

    def create_standard(self, actor, book_id, request):
        digest = fingerprint(request.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            prior = conn.execute(
                "SELECT * FROM quality_standards WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["book_id"] != book_id or prior["request_fingerprint"] != digest:
                    raise AccountingError("标准幂等键已用于不同输入", 409)
                return self._standard_json(conn, actor, prior, True)
            sample = self._sample(conn, actor, book_id, request.sample_id)
            evidence = self._evidence(conn, sample)
            if not evidence["attachments"] and not evidence["evidence_ref"].strip():
                raise AccountingError("缺少可供真人核对的原件证据", 409)
            labels = [label.model_dump() for label in request.field_labels]
            ocr = {row["id"]: row for row in evidence["ocr"]}
            for label in labels:
                extraction = ocr.get(label["extraction_id"])
                documents = extraction["result"].get("documents", []) if extraction else []
                if label["document_index"] >= len(documents) or not any(
                    page["page"] == label["page"]
                    for page in documents[label["document_index"]].get("pages", [])
                ):
                    raise AccountingError("字段标注须引用本标准成功识别的实际票据及原件页码", 409)
            standard_id = uuid4().hex
            conn.execute(
                "INSERT INTO quality_standards(id,firm_id,company_id,book_id,period,sample_id,draft_id,source_id,snapshot_json,final_json,evidence_json,labels_json,reason,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    standard_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    book["period"],
                    sample["id"],
                    sample["draft_id"],
                    sample["source_id"],
                    sample["snapshot_json"],
                    sample["final_json"],
                    encode(evidence),
                    encode(labels),
                    request.reason,
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                ),
            )
            row = self._standard(conn, actor, book_id, standard_id)
            if not self._current(conn, actor, row):
                raise AccountingError("来源事实已变化，历史审核样例不能直接建立当前评测标准", 409)
            return self._standard_json(conn, actor, row, True)

    def confirm(self, actor, book_id, standard_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._standard(conn, actor, book_id, standard_id)
            if row["created_by"] == actor.user_id:
                raise AccountingError("评测标准必须由另一位会计核对原件后独立确认", 409)
            if not self._current(conn, actor, row):
                raise AccountingError("事实或证据已变化，请建立新标准", 409)
            prior = conn.execute(
                "SELECT * FROM quality_confirmations WHERE standard_id=? AND book_id=?",
                (standard_id, book_id),
            ).fetchone()
            if prior and (
                prior["confirmed_by"] != actor.user_id or prior["reason"] != request.reason
            ):
                raise AccountingError("此标准已有不可覆盖的独立确认", 409)
            if not prior:
                conn.execute(
                    "INSERT INTO quality_confirmations(standard_id,book_id,firm_id,confirmed_by,reason,evidence_checked,created_at) VALUES(?,?,?,?,?,1,?)",
                    (standard_id, book_id, actor.firm_id, actor.user_id, request.reason, now()),
                )
            return self._standard_json(conn, actor, row, True)

    def _context(self, conn, actor, book_id, request):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.quality_context(?,?,?)",
                (
                    book_id,
                    encode(request.standard_ids),
                    encode(request.model_dump(exclude={"idempotency_key", "standard_ids"})),
                ),
            ).fetchone()[0]
        return fingerprint(
            {
                "standards": [
                    dict(self._standard(conn, actor, book_id, item))
                    for item in request.standard_ids
                ],
                "accounts": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM accounts WHERE book_id=? ORDER BY code", (book_id,)
                    )
                ],
                "templates": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT t.*,v.definition FROM journal_templates t JOIN journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=? ORDER BY t.id",
                        (book_id,),
                    )
                ],
                "rules": self.learning.policy_fingerprint(conn, actor, book_id),
                "models": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT r.id,r.result_json,r.status,r.input_fingerprint FROM ai_runs r JOIN business_mappings m ON m.event_id=r.event_id WHERE m.book_id=? AND r.model=? AND r.prompt_version=? ORDER BY r.id",
                        (book_id, request.model, request.prompt_version),
                    )
                ],
                "current": [
                    self._current(conn, actor, self._standard(conn, actor, book_id, item))
                    for item in request.standard_ids
                ],
            }
        )

    def _candidate(self, conn, actor, book_id, row, request):
        snapshot = json.loads(row["snapshot_json"])
        if request.mode.startswith("stored_"):
            event_id = json.loads(row["evidence_json"])["event_id"]
            if request.mode == "stored_ocr":
                labels, documents, versions = json.loads(row["labels_json"]), [], []
                for label in labels:
                    original = next(
                        item
                        for item in json.loads(row["evidence_json"])["ocr"]
                        if item["id"] == label["extraction_id"]
                    )
                    run = conn.execute(
                        "SELECT * FROM ai_runs WHERE event_id=? AND attachment_id=? AND input_version=? AND kind='ocr' AND status='succeeded' AND model=? AND prompt_version=? ORDER BY created_at DESC,id DESC LIMIT 1",
                        (
                            event_id,
                            original["attachment_id"],
                            snapshot["fact_version"],
                            request.model,
                            request.prompt_version,
                        ),
                    ).fetchone()
                    if not run:
                        return None, {"reason": "指定版本缺少该原件的成功 OCR 结果"}
                    result = json.loads(run["result_json"])["documents"]
                    document = (
                        result[label["document_index"]]
                        if label["document_index"] < len(result)
                        else {}
                    )
                    documents.append(
                        {
                            **label,
                            "value": document.get(label["field"]),
                            "page_present": any(
                                entry["field"] == label["field"] and entry["page"] == label["page"]
                                for entry in document.get("evidence", [])
                            )
                            or label["value"] is None,
                        }
                    )
                    versions.append(run["id"])
                return ({"labels": documents} if labels else None), {
                    "model": request.model,
                    "prompt_version": request.prompt_version,
                    "run_ids": sorted(set(versions)),
                    "reason": None if labels else "尚无真人确认的字段标注",
                }
            reviewed_version = conn.execute(
                "SELECT reviewed_version FROM learning_samples WHERE id=? AND book_id=?",
                (row["sample_id"], book_id),
            ).fetchone()[0]
            run = conn.execute(
                "SELECT r.* FROM ai_runs r WHERE event_id=? AND draft_id=? AND book_id=? AND input_version<=? AND kind='proposal' AND status='succeeded' AND model=? AND prompt_version=? AND EXISTS(SELECT 1 FROM audit_events a WHERE a.firm_id=r.firm_id AND a.object_id=r.id AND a.action='intelligence.proposal.claim' AND json_extract(a.details,'$.fact_version')=? AND json_extract(a.details,'$.input_version')=r.input_version) ORDER BY created_at DESC,id DESC LIMIT 1",
                (
                    event_id,
                    row["draft_id"],
                    book_id,
                    reviewed_version,
                    request.model,
                    request.prompt_version,
                    snapshot["fact_version"],
                ),
            ).fetchone()
            return (json.loads(run["result_json"]) if run else None), {
                "model": request.model,
                "prompt_version": request.prompt_version,
                "run_id": run["id"] if run else None,
                "input_version": run["input_version"] if run else None,
                "input_fingerprint": run["input_fingerprint"] if run else None,
                "replay_basis": "审核前归档候选；不宣称候选在当前补件或规则上下文重新生成",
                "reason": None if run else "指定版本没有此事实版本的成功分录候选",
            }
        if not self.learning._ordinary_sample(conn, book_id, row["sample_id"]):
            return None, {"reason": "清偿分录须按实际核销关系评测，不属于普通分类模板"}
        candidates = []
        if request.mode == "rules":
            for rule in conn.execute(
                "SELECT r.id,r.current_version,v.definition_json,v.sample_ids_json FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=? AND r.status='published' ORDER BY r.id",
                (book_id,),
            ):
                if not all(
                    self.learning._ordinary_sample(conn, book_id, sample)
                    for sample in json.loads(rule["sample_ids_json"])
                ):
                    continue
                definition = RuleDefinition.model_validate_json(rule["definition_json"])
                matches = (
                    self.learning.matches_in_transaction(conn, actor, book_id, definition, snapshot)
                    if hasattr(self.learning, "matches_in_transaction")
                    else self.learning._matches(definition, snapshot)
                )
                if matches:
                    candidates.append(
                        (0, rule["id"], rule["current_version"], definition.as_template())
                    )
        elif snapshot["source_kind"] == "business":
            for template in self.catalog._templates(conn, book_id, True):
                definition = TemplateDefinition.model_validate_json(template["definition"])
                if self.catalog._matches(
                    definition, snapshot["business_type"], snapshot["payload"]
                ):
                    candidates.append(
                        (
                            int(definition.category is not None)
                            + int(definition.payment_status is not None),
                            template["id"],
                            template["current_version"],
                            definition,
                        )
                    )
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if not candidates or len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None, {"reason": "没有覆盖此输入的配置，或存在同优先级配置冲突"}
        _, config_id, version, definition = candidates[0]
        provenance = {"mode": request.mode, "configuration_id": config_id, "version": version}
        try:
            rendered = self.catalog._render(
                conn,
                actor,
                book_id,
                definition,
                snapshot["payload"],
                money_to_cents(snapshot["payload"]["amount"]),
                snapshot["context"],
                snapshot["recognized"],
            )
            if rendered["issues"]:
                return None, provenance | {"reason": "；".join(rendered["issues"])}
            return {"lines": self.learning._public_lines(rendered)}, provenance
        except (AccountingError, ValueError) as error:
            return None, provenance | {"reason": str(error)}

    @staticmethod
    def _differences(row, request, candidate):
        final, diffs = json.loads(row["final_json"]), []
        if request.mode == "stored_ocr":
            for expected, actual in zip(json.loads(row["labels_json"]), candidate["labels"]):
                left, right = expected["value"], actual["value"]
                if expected["field"] in {"gross_amount", "net_amount", "tax_amount"}:
                    try:
                        right = None if right is None else cents_to_money(money_to_cents(right))
                    except (ValueError, AccountingError):
                        pass
                if left != right:
                    diffs.append(
                        {
                            "field": expected["field"],
                            "reference": {
                                key: expected[key]
                                for key in ("extraction_id", "document_index", "page")
                            },
                            "expected": left,
                            "actual": right,
                        }
                    )
                if not actual["page_present"]:
                    diffs.append(
                        {"field": "reference", "expected": expected["page"], "actual": None}
                    )
            return diffs
        expected = canonical_lines(final["lines"])
        try:
            actual = canonical_lines(candidate.get("lines", []))
        except (ValueError, KeyError, AccountingError):
            return [
                {"field": "lines", "expected": final["lines"], "actual": candidate.get("lines")}
            ]
        noaux = lambda rows: sorted((code, debit, credit) for code, debit, credit, _ in rows)
        if noaux(expected) != noaux(actual):
            diffs.append(
                {"field": "lines", "expected": final["lines"], "actual": candidate.get("lines")}
            )
        if expected != actual and noaux(expected) == noaux(actual):
            diffs.append(
                {"field": "auxiliary", "expected": final["lines"], "actual": candidate.get("lines")}
            )
        amount = sum(line[1] for line in actual)
        expected_amount = json.loads(row["snapshot_json"])["source"]["amount_cents"]
        if amount != expected_amount:
            diffs.append(
                {
                    "field": "amount",
                    "expected": cents_to_money(expected_amount),
                    "actual": cents_to_money(amount),
                }
            )
        if request.mode == "stored_model":
            evidence = json.loads(row["evidence_json"])
            allowed = {
                (ocr["attachment_id"], ocr["id"], item["field"], item["page"])
                for ocr in evidence["ocr"]
                for doc in ocr["result"].get("documents", [])
                for item in doc.get("evidence", [])
            }
            refs = candidate.get("evidence_refs", [])
            if not refs or any(
                (
                    ref.get("attachment_id"),
                    ref.get("extraction_id"),
                    ref.get("field"),
                    ref.get("page"),
                )
                not in allowed
                for ref in refs
            ):
                diffs.append(
                    {
                        "field": "reference",
                        "expected": "本标准冻结原件中的有效字段与页码引用",
                        "actual": refs,
                    }
                )
        return diffs

    def _report(self, conn, actor, book_id, request):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT accounting.quality_report(?,?,?)",
                (
                    book_id,
                    encode(request.standard_ids),
                    encode(request.model_dump(exclude={"standard_ids", "idempotency_key"})),
                ),
            ).fetchone()[0]
        rows = []
        for selected in request.standard_ids:
            row = self._standard(conn, actor, book_id, selected)
            if not conn.execute(
                "SELECT 1 FROM quality_confirmations WHERE standard_id=? AND book_id=?",
                (selected, book_id),
            ).fetchone():
                raise AccountingError("评测只能选择另一位会计已独立确认的标准", 409)
            current = self._current(conn, actor, row)
            candidate, provenance = (
                self._candidate(conn, actor, book_id, row, request)
                if current
                else (None, {"reason": "事实或原件证据已失效"})
            )
            differences = (
                self._differences(row, request, candidate) if candidate is not None else []
            )
            rows.append(
                {
                    "standard_id": selected,
                    "draft_id": row["draft_id"],
                    "business_type": json.loads(row["snapshot_json"])["business_type"],
                    "status": "stale"
                    if not current
                    else "uncovered"
                    if candidate is None
                    else "different"
                    if differences
                    else "passed",
                    "provenance": provenance,
                    "candidate": candidate,
                    "differences": differences,
                }
            )
        return {
            "engine_version": 1,
            "mode": request.mode,
            "counts": {
                status: sum(row["status"] == status for row in rows)
                for status in ("passed", "different", "uncovered", "stale")
            },
            "items": rows,
            "scope_note": "规则/模板仅核对分录金额、科目及辅助项；分录模型增加证据引用核对；OCR仅核对真人独立确认的字段和页码。未覆盖项不计为通过，来源日期和交易对方仅在对应OCR标注存在时测量。",
        }

    def create_run(self, actor, book_id, request):
        digest = fingerprint(request.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            prior = conn.execute(
                "SELECT * FROM quality_runs WHERE firm_id=? AND created_by=? AND request_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["book_id"] != book_id or prior["request_fingerprint"] != digest:
                    raise AccountingError("评测幂等键已用于不同输入", 409)
                return self._run_json(conn, actor, prior)
            report = self._report(conn, actor, book_id, request)
            run_id = uuid4().hex
            settings = request.model_dump(exclude={"standard_ids", "idempotency_key"})
            conn.execute(
                "INSERT INTO quality_runs(id,firm_id,company_id,book_id,period,standard_ids_json,settings_json,input_fingerprint,report_json,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    book["period"],
                    encode(request.standard_ids),
                    encode(settings),
                    self._context(conn, actor, book_id, request),
                    encode(report),
                    actor.user_id,
                    now(),
                    request.idempotency_key,
                    digest,
                ),
            )
            return self._run_json(
                conn,
                actor,
                conn.execute(
                    "SELECT * FROM quality_runs WHERE id=? AND book_id=?", (run_id, book_id)
                ).fetchone(),
            )

    def _run_json(self, conn, actor, row):
        from .models import RunCreate

        request = RunCreate(
            **json.loads(row["settings_json"]),
            standard_ids=json.loads(row["standard_ids_json"]),
            idempotency_key=row["request_key"],
        )
        return {
            key: row[key]
            for key in ("id", "book_id", "period", "created_by", "created_at", "input_fingerprint")
        } | {
            "settings": json.loads(row["settings_json"]),
            "report": json.loads(row["report_json"]),
            "stale": row["input_fingerprint"]
            != self._context(conn, actor, row["book_id"], request),
        }

    def run(self, actor, book_id, run_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM quality_runs WHERE id=? AND book_id=? AND firm_id=?",
                (run_id, book_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("评测运行不存在或无权访问", 404)
            return self._run_json(conn, actor, row)

    def overview(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            standards = conn.execute(
                "SELECT * FROM quality_standards WHERE book_id=? AND firm_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (book_id, actor.firm_id, limit, offset),
            ).fetchall()
            candidates = conn.execute(
                "SELECT s.id,s.draft_id,s.snapshot_json,s.reviewed_by FROM learning_samples s JOIN drafts d ON d.id=s.draft_id AND d.book_id=s.book_id WHERE s.book_id=? AND s.firm_id=? AND d.status IN ('approved','exported') ORDER BY s.created_at DESC,s.id DESC LIMIT ? OFFSET ?",
                (book_id, actor.firm_id, limit, offset),
            ).fetchall()
            runs = conn.execute(
                "SELECT id,created_at,settings_json,report_json FROM quality_runs WHERE book_id=? AND firm_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (book_id, actor.firm_id, limit, offset),
            ).fetchall()
            totals = {
                kind: conn.execute(
                    f"SELECT count(*) FROM {table} WHERE book_id=? AND firm_id=?",
                    (book_id, actor.firm_id),
                ).fetchone()[0]
                for kind, table in [
                    ("standards", "quality_standards"),
                    ("runs", "quality_runs"),
                    ("candidates", "learning_samples"),
                ]
            }
            summary = [
                dict(row)
                for row in conn.execute(
                    "SELECT q.business_type,count(*) AS confirmed_count FROM quality_standard_types q JOIN quality_confirmations c ON c.standard_id=q.id AND c.book_id=q.book_id WHERE q.book_id=? AND q.firm_id=? GROUP BY q.business_type",
                    (book_id, actor.firm_id),
                )
            ]
            receipt_total = conn.execute(
                "SELECT count(*) FROM delivery_receipts WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            delivery_rows = conn.execute(
                "SELECT * FROM delivery_receipts WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
                (book_id,),
            ).fetchall()
            delivery = DeliveryService(self.db)
            delivery_results = []
            for receipt in delivery_rows:
                export = conn.execute(
                    "SELECT * FROM exports WHERE id=? AND book_id=?",
                    (receipt["export_id"], book_id),
                ).fetchone()
                delivery_results.extend(
                    delivery._receipt_json(conn, actor, export, receipt)["items"]
                )
            value = {
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "standards": [self._standard_json(conn, actor, row) for row in standards],
                "candidates": [
                    {key: row[key] for key in ("id", "draft_id", "reviewed_by")}
                    | {
                        key: json.loads(row["snapshot_json"])["payload"][key]
                        for key in ("counterparty", "amount")
                    }
                    for row in candidates
                ],
                "runs": [
                    {
                        "id": row["id"],
                        "created_at": row["created_at"],
                        "settings": json.loads(row["settings_json"]),
                        "counts": json.loads(row["report_json"])["counts"],
                    }
                    for row in runs
                ],
                "pagination": {"limit": limit, "offset": offset, "totals": totals},
                "summary_by_business_type": summary,
                "delivery_differences": sum(
                    item["state"] == "discrepancy" for item in delivery_results
                )
                if receipt_total
                else None,
                "delivery_measurement": {
                    "receipt_count": receipt_total,
                    "observed_receipts": len(delivery_rows),
                    "truncated": receipt_total > 100,
                },
                "report_groups": report_groups(
                    conn,
                    actor,
                    book_id,
                    self.db.dialect,
                    lambda row: self._run_json(conn, actor, row),
                ),
            }
        measured = EfficiencyService(self.db).overview(actor, book_id)
        value["human_minutes"] = (
            round(measured["human"]["credited_seconds"] / 60, 2)
            if measured["human"]["session_count"]
            else None
        )
        value["supplement_wait_seconds"] = (
            measured["supplement"]["calendar_wait_seconds"]
            if measured["supplement"]["completed_first_response_intervals"]
            else None
        )
        value["measurement_note"] = (
            "人工时间为账套明确计时；补件等待为首次响应日历间隔；交付差异按最新最多100次人工导入回执与冻结导出核对，重复回执可能重复计项，截断明确标记。未采集为null。业务类型只统计已独立确认标准，不将抽样通过率推算为全企业正确率。"
        )
        return value

    def report(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            groups = report_groups(
                conn, actor, book_id, self.db.dialect, lambda row: self._run_json(conn, actor, row)
            )
            return {
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "groups": groups,
                "measurement_note": "每个回放范围只取最新一次最多100个标准的运行，按业务类型汇总其已选样本；不能代表全企业覆盖。配置或证据已变化时保留历史计数并停止展示当前一致比例。业务类型无法归因的人工时间、补件等待和交付差异为null。",
            }
