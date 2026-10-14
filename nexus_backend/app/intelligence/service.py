"""Scoped, auditable model proposals. Models have no mutation or approval tools."""

import json
from datetime import datetime, timezone, timedelta, date
from urllib.parse import urlsplit
from uuid import uuid4

from ..accounting.domain import Actor, AccountingError
from ..accounting.models import DraftUpdate, money_to_cents
from ..accounting.queries import inactive_source_sql
from ..accounting.service import AccountingService, fingerprint, now
from ..business.service import BusinessService, encode
from .checks import extraction_checks, reconcile_checks, finding, normalized, invoice_key
from .documents import prepare_document
from .models import InvoiceExtraction, JournalSuggestion, ProviderResult
from .cache import config_key, read_cache, store_cache
from .structured import (
    is_structured,
    parse_invoice_xml,
    STRUCTURED_PROVIDER,
    STRUCTURED_VERSION,
    extraction_metadata,
    verification_finding,
)
from .provider import (
    AISettings,
    OpenAICompatibleProvider,
    ProviderError,
    EXTRACTION_PROMPT_VERSION,
    JOURNAL_PROMPT_VERSION,
    effective_settings,
)


class IntelligenceService:
    def __init__(self, db, storage, provider=None):
        self.db, self.storage = db, storage
        self.business = BusinessService(db)
        self.ledger = AccountingService(db)
        self.access = self.ledger.access
        self.provider = provider or OpenAICompatibleProvider(AISettings.from_env())
        self.ledger.intelligence = self
        self.business.intelligence = self
        self.meter = None

    def _meter(self):
        if self.meter is None:
            from ..operations.meter import MeterService

            self.meter = MeterService(self.db)
        return self.meter

    def capture_snapshot(self, conn, actor, event):
        from ..operations.service import record_exception_snapshot

        current = self._assessment(conn, actor, event, persist=False)
        record_exception_snapshot(
            self.db, conn, actor, event, current["findings"], current["fingerprint"]
        )

    def config(self, actor):
        settings = self.provider.settings
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            rows = conn.execute(
                "SELECT company_id FROM ai_company_settings WHERE firm_id=? AND enabled=1",
                (actor.firm_id,),
            )
            companies = []
            for row in rows:
                try:
                    self.access.company(conn, actor, row["company_id"])
                    companies.append(row["company_id"])
                except AccountingError as error:
                    if error.status not in {403, 404}:
                        raise
        labels = list(
            dict.fromkeys(
                urlsplit(effective_settings(settings, scenario).base_url).hostname
                for scenario in ("ocr", "journal")
                if getattr(
                    settings,
                    "extraction_available" if scenario == "ocr" else "suggestion_available",
                )
            )
        )
        return {
            "configured": settings.extraction_available or settings.suggestion_available,
            "structured_ready": True,
            "vision_ready": settings.extraction_available,
            "journal_ready": settings.suggestion_available,
            "provider_label": " / ".join(label for label in labels if label) or "尚未配置模型服务",
            "enabled_company_ids": companies,
        }

    def company_settings(self, actor, company_id, enabled=None):
        with self.db.transaction(write=enabled is not None, actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=enabled is not None)
            try:
                self.access.company(conn, actor, company_id, manage=True)
                can_manage = True
            except AccountingError:
                can_manage = False
            if enabled is not None:
                # The resume signal uses updated_at as its next eligible time.
                # API and worker hosts need not share the database's clock.
                stamp = (
                    conn.execute(
                        "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
                    ).fetchone()[0]
                    if self.db.is_postgres
                    else now()
                )
                conn.execute(
                    "INSERT INTO ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                    (actor.firm_id, company_id, int(enabled), actor.user_id, stamp),
                )
                self.business._audit(
                    conn, actor, "intelligence.company.configure", company_id, {"enabled": enabled}
                )
            row = conn.execute(
                "SELECT enabled FROM ai_company_settings WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company_id),
            ).fetchone()
            return {"enabled": bool(row and row["enabled"]), "can_manage": can_manage}

    def _enabled(self, conn, actor, event):
        row = conn.execute(
            "SELECT enabled FROM ai_company_settings WHERE firm_id=? AND company_id=?",
            (actor.firm_id, event["company_id"]),
        ).fetchone()
        return bool(row and row["enabled"])

    def _mapping(self, conn, event):
        return conn.execute(
            "SELECT * FROM business_mappings WHERE event_id=?", (event["id"],)
        ).fetchone()

    def _permissions(self, conn, actor, event):
        member = self.business._membership(conn, actor, event["company_id"])
        own = event["applicant_id"] == actor.user_id
        department = member and event["department_id"] in self.business._approver_departments(
            conn, actor, event["company_id"]
        )
        finance = bool(member and member["role"] == "finance")
        book = None
        mapping = self._mapping(conn, event)
        if mapping:
            try:
                book = self.access.book(conn, actor, mapping["book_id"])
            except AccountingError:
                pass
        edit = bool(book and book["permissions"]["edit"])
        review = bool(book and book["permissions"]["review"])
        if self.db.is_postgres:
            frozen = not conn.execute(
                "SELECT accounting.ai_event_open(?,?,?)",
                (event["id"], actor.firm_id, event["company_id"]),
            ).fetchone()[0]
        else:
            state = (
                conn.execute(
                    "SELECT d.status,p.status AS period_status FROM drafts d JOIN books b ON b.id=d.book_id JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE d.id=?",
                    (mapping["draft_id"],),
                ).fetchone()
                if mapping
                else None
            )
            frozen = bool(
                state and (state["status"] != "draft" or state["period_status"] != "open")
            )
        return {
            "process": bool(
                not frozen and (member and (own or finance or department) or edit or review)
            ),
            "resolve": not frozen and not own and (finance or review),
            "link_bank": not frozen
            and event["status"] == "handed_off"
            and edit
            and (finance or review),
            "generate_proposal": not frozen and edit,
            "apply_proposal": not frozen and edit,
        }

    def _require(self, conn, actor, event, capability, enabled=False):
        if not self._permissions(conn, actor, event)[capability]:
            raise AccountingError("没有该事项的识别、财务处理或账套编辑权限", 403)
        if enabled and not self._enabled(conn, actor, event):
            raise AccountingError("企业尚未启用票据与模型处理，请联系管理员", 403)

    @staticmethod
    def _public_run(row):
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
        result["usage"] = json.loads(result.pop("usage_json"))
        result.pop("input_fingerprint", None)
        result.pop("idempotency_key", None)
        if result["kind"] == "ocr":
            result.update(extraction_metadata(row))
        return result

    @staticmethod
    def _reason(value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
            raise AccountingError("人工处理必须填写有效依据（1–1000字）", 422)
        return value.strip()

    def _claim(
        self,
        conn,
        actor,
        event,
        kind,
        key,
        digest,
        model,
        prompt,
        attachment_id=None,
        draft=None,
        input_version=None,
        provider_name=None,
    ):
        existing = conn.execute(
            "SELECT * FROM ai_runs WHERE firm_id=? AND event_id=? AND kind=? AND idempotency_key=?",
            (actor.firm_id, event["id"], kind, key),
        ).fetchone()
        if existing:
            if existing["input_fingerprint"] != digest:
                raise AccountingError("该处理幂等键已用于不同资料或版本", 409)
            if existing["status"] == "running":
                if datetime.fromisoformat(existing["created_at"]) > datetime.now(
                    timezone.utc
                ) - timedelta(minutes=3):
                    raise AccountingError("同一处理任务仍在运行，请读取结果，勿重复请求", 409)
                if existing["created_by"] != actor.user_id:
                    raise AccountingError("该任务由其他人员发起，请使用新的处理键重试", 409)
                changed = conn.execute(
                    "UPDATE ai_runs SET status='failed',error_code='expired',error_detail='处理超时，请使用新的处理键重试',finished_at=? WHERE id=? AND status='running'",
                    (now(), existing["id"]),
                )
                if changed.rowcount != 1:
                    raise AccountingError("任务状态已改变，请刷新后重试", 409)
                existing = conn.execute(
                    "SELECT * FROM ai_runs WHERE id=?", (existing["id"],)
                ).fetchone()
            return existing, False
        run_id = uuid4().hex
        conn.execute(
            "INSERT INTO ai_runs(id,firm_id,company_id,event_id,kind,attachment_id,draft_id,book_id,input_version,input_fingerprint,idempotency_key,status,result_json,provider,model,prompt_version,usage_json,error_code,error_detail,created_by,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'running',NULL,?,?,?,'{}','','',?,?,NULL)",
            (
                run_id,
                actor.firm_id,
                event["company_id"],
                event["id"],
                kind,
                attachment_id,
                draft["id"] if draft else None,
                draft["book_id"] if draft else None,
                input_version
                if input_version is not None
                else draft["version"]
                if draft
                else event["version"],
                digest,
                key,
                provider_name
                or urlsplit(
                    effective_settings(
                        self.provider.settings, "ocr" if kind == "ocr" else "journal"
                    ).base_url
                ).hostname
                or "openai-compatible",
                model,
                prompt,
                actor.user_id,
                now(),
            ),
        )
        self.business._audit(
            conn,
            actor,
            "intelligence." + kind + ".claim",
            run_id,
            {
                "event_id": event["id"],
                "input_version": input_version
                if input_version is not None
                else draft["version"]
                if draft
                else event["version"],
                "fact_version": event["fact_version"],
            },
        )
        return conn.execute("SELECT * FROM ai_runs WHERE id=?", (run_id,)).fetchone(), True

    def _abandon(self, actor, run_id):
        try:
            with self.db.transaction(write=True, actor=actor) as conn:
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.abandon_ai_run(?)", (run_id,))
                else:
                    conn.execute(
                        "UPDATE ai_runs SET status='stale',result_json=NULL,error_code='scope_changed',error_detail='权限或输入已改变，结果未发布',finished_at=? WHERE id=? AND created_by=? AND status='running'",
                        (now(), run_id, actor.user_id),
                    )
        except Exception:
            # Persistent claims expire; never retry a model request automatically.
            pass

    def _finish(self, conn, actor, run, result=None, error=None, cache_hit=False):
        state = "succeeded" if error is None else "failed"
        usage = result.usage.model_dump() if result and result.usage else {}
        changed = conn.execute(
            "UPDATE ai_runs SET status=?,result_json=?,usage_json=?,error_code=?,error_detail=?,finished_at=?,cache_hit=? WHERE id=? AND status='running'",
            (
                state,
                encode(result.data.model_dump()) if result else None,
                encode(usage),
                error.code if error else "",
                error.detail if error else "",
                now(),
                int(cache_hit),
                run["id"],
            ),
        )
        if changed.rowcount != 1:
            raise AccountingError("处理任务已失效，结果未发布，请重新处理", 409)
        self.business._audit(
            conn, actor, "intelligence." + run["kind"] + ".finish", run["id"], {"status": state}
        )
        return conn.execute("SELECT * FROM ai_runs WHERE id=?", (run["id"],)).fetchone()

    def extract(self, actor, event_id, request, execution=None):
        transaction_options = (
            {"company_id": execution.job["company_id"], "write_scope": "company"}
            if execution
            else {}
        )
        with self.db.transaction(write=True, actor=actor, **transaction_options) as conn:
            event = self.business._event(conn, actor, event_id)
            self._require(conn, actor, event, "process", enabled=True)
            if request.expected_fact_version is None:
                self.business._version(event, request.expected_version)
            elif event["fact_version"] != request.expected_fact_version:
                raise AccountingError("业务事实已改变，识别任务未发布，请重新处理", 409)
            attachment = conn.execute(
                "SELECT * FROM business_attachments WHERE id=? AND event_id=?",
                (request.attachment_id, event_id),
            ).fetchone()
            if not attachment:
                raise AccountingError("附件不存在或不属于该事项", 404)
            from .current_evidence import superseded_ids

            if attachment["id"] in superseded_ids(self.db, conn, event):
                raise AccountingError("该原件已由核验后的补件替代，请识别当前有效票据", 409)
            if execution:
                execution.check(conn)
                execution.inputs(event, attachment)
            elif conn.execute(
                "SELECT 1 FROM ocr_jobs WHERE event_id=? AND attachment_id=? AND status IN ('queued','running','retry_wait')",
                (event_id, attachment["id"]),
            ).fetchone():
                raise AccountingError("该票据正在后台识别，请查看任务进度", 409)
            cache_key = config_key(self.provider.settings, attachment["media_type"])
            structured = is_structured(attachment["media_type"])
            digest = fingerprint(
                {
                    "attachment_id": attachment["id"],
                    "sha256": attachment["sha256"],
                    "config": cache_key,
                    "fact_version": event["fact_version"],
                    "force_refresh": request.force_refresh,
                }
            )
            run, execute = self._claim(
                conn,
                actor,
                event,
                "ocr",
                request.idempotency_key,
                digest,
                "einvoice-0.3"
                if structured
                else effective_settings(self.provider.settings, "ocr").model,
                STRUCTURED_VERSION if structured else EXTRACTION_PROMPT_VERSION,
                attachment_id=attachment["id"],
                input_version=request.expected_fact_version,
                provider_name=STRUCTURED_PROVIDER if structured else None,
            )
            if execution and execute:
                execution.bind(conn, run)
        if not execute:
            return self._public_run(run)
        error, result, pages, cache_hit = None, None, [], False
        budget, attempted = self._meter().context(actor, run), False
        try:
            data = self.storage.read(attachment)
            local = parse_invoice_xml(data) if structured else None
            pages = local.pages if local else prepare_document(data, attachment["media_type"])
            # Check current access immediately before sending financial evidence.
            with self.db.transaction(write=True, actor=actor, **transaction_options) as conn:
                current = self.business._event(conn, actor, event_id)
                self._require(conn, actor, current, "process", enabled=True)
                if (
                    current["fact_version"] != event["fact_version"]
                    or config_key(self.provider.settings, attachment["media_type"]) != cache_key
                ):
                    raise AccountingError("业务事实或识别配置已改变，识别结果未发布", 409)
                if not conn.execute(
                    "SELECT 1 FROM business_attachments WHERE id=? AND event_id=? AND sha256=?",
                    (attachment["id"], event_id, attachment["sha256"]),
                ).fetchone():
                    raise AccountingError("附件资料已改变，识别结果未发布", 409)
                if execution:
                    execution.inputs(current, attachment)
                cached = (
                    None
                    if request.force_refresh
                    else read_cache(self.db, conn, actor, current, attachment, cache_key)
                )
                if cached and not any(
                    issue["severity"] == "blocking"
                    for issue in extraction_checks(
                        cached.model_dump()["documents"],
                        [page.page for page in pages],
                        {page.page: page.native_text for page in pages},
                    )
                ):
                    result = ProviderResult(
                        cached, run["provider"], run["model"], run["prompt_version"], None
                    )
                    cache_hit = True
                else:
                    if local:
                        result = local.result
                    elif execution:
                        execution.before_send(conn)
                    if not local:
                        budget.before_send(conn)
            if not cache_hit and not local:
                attempted = True
                try:
                    before_model = getattr(execution, "before_model", None)
                    if callable(before_model):
                        before_model()
                    from ..concurrency.provider import provider_scope

                    with provider_scope(self.db, actor, event["company_id"]):
                        result = self.provider.extract_invoice(pages)
                finally:
                    after_model = getattr(execution, "after_model", None)
                    if callable(after_model):
                        after_model()
        except ProviderError as caught:
            error = caught
        except AccountingError:
            self._abandon(actor, run["id"])
            raise
        except Exception:
            error = ProviderError("processing_failed", "票据处理失败，请人工核对或重试")
        finally:
            budget.finish(result, error, attempted)
        try:
            with self.db.transaction(write=True, actor=actor, **transaction_options) as conn:
                current = self.business._event(conn, actor, event_id)
                self._require(conn, actor, current, "process", enabled=True)
                if (
                    current["fact_version"] != event["fact_version"]
                    or config_key(self.provider.settings, attachment["media_type"]) != cache_key
                ):
                    raise AccountingError("业务事实或识别配置已改变，识别结果未发布", 409)
                if not conn.execute(
                    "SELECT 1 FROM business_attachments WHERE id=? AND event_id=? AND sha256=?",
                    (attachment["id"], event_id, attachment["sha256"]),
                ).fetchone():
                    raise AccountingError("附件资料已改变，识别结果未发布", 409)
                if execution:
                    execution.check(conn)
                    execution.inputs(current, attachment)
                finished = self._finish(conn, actor, run, result, error, cache_hit=cache_hit)
                if finished["status"] == "succeeded":
                    documents = result.data.model_dump()["documents"]
                    issues = extraction_checks(
                        documents,
                        [page.page for page in pages],
                        {page.page: page.native_text for page in pages},
                    )
                    if structured:
                        issues.append(verification_finding())
                    for issue in issues:
                        self._record_finding(
                            conn,
                            current,
                            finished["id"],
                            issue,
                            fingerprint({"run": finished["id"], "issue": issue}),
                        )
                    if not any(issue["severity"] == "blocking" for issue in issues):
                        store_cache(self.db, conn, actor, current, attachment, cache_key, finished)
                if execution:
                    execution.finish(conn, finished, error)
                self._assessment(conn, actor, current, persist=True)
                return self._public_run(finished)
        except AccountingError:
            self._abandon(actor, run["id"])
            raise

    def _record_finding(self, conn, event, run_id, issue, digest):
        conn.execute(
            "INSERT INTO ai_findings(id,run_id,firm_id,company_id,event_id,code,severity,payload_json,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id,code,fingerprint) DO NOTHING",
            (
                uuid4().hex,
                run_id,
                event["firm_id"],
                event["company_id"],
                event["id"],
                issue["code"],
                issue["severity"],
                encode(issue["payload"]),
                digest,
                now(),
            ),
        )

    def _latest(self, conn, event):
        rows = conn.execute(
            "SELECT r.* FROM ai_runs r WHERE r.event_id=? AND r.kind='ocr' AND NOT EXISTS(SELECT 1 FROM ai_runs newer WHERE newer.event_id=r.event_id AND newer.kind='ocr' AND newer.attachment_id=r.attachment_id AND (newer.created_at>r.created_at OR (newer.created_at=r.created_at AND newer.id>r.id))) ORDER BY r.attachment_id",
            (event["id"],),
        ).fetchall()
        latest = {}
        from .current_evidence import superseded_ids

        excluded = superseded_ids(self.db, conn, event)
        for row in rows:
            if row["attachment_id"] not in excluded:
                latest.setdefault(row["attachment_id"], row)
        return latest

    def _bank_data(self, conn, actor, event):
        mapping = self._mapping(conn, event)
        if not mapping:
            return [], []
        try:
            self.access.book(conn, actor, mapping["book_id"])
        except AccountingError:
            return [], []
        payload = json.loads(event["payload"])
        select = "SELECT s.*,d.status AS draft_status,l.event_id AS linked_event_id FROM sources s LEFT JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id LEFT JOIN ai_bank_links l ON l.bank_source_id=s.id AND l.book_id=s.book_id WHERE s.book_id=? AND s.source_kind='bank'"
        links = [
            dict(row)
            for row in conn.execute(
                select + " AND l.event_id=? ORDER BY s.date,s.id", (mapping["book_id"], event["id"])
            )
        ]
        occurred = date.fromisoformat(payload["occurred_on"])
        # Only a bounded hint set is read here. The reconciliation workbench
        # has its own paged candidates and always revalidates confirmation.
        rows = conn.execute(
            select
            + " AND NOT "
            + inactive_source_sql(self.db.dialect)
            + " AND l.bank_source_id IS NULL AND (d.status IS NULL OR d.status='draft') AND s.direction=? AND s.amount_cents<=? AND s.date BETWEEN ? AND ? ORDER BY CASE WHEN s.amount_cents=? THEN 0 ELSE 1 END,s.date,s.id LIMIT 100",
            (
                mapping["book_id"],
                payload["direction"],
                money_to_cents(payload["amount"]),
                (occurred - timedelta(days=7)).isoformat(),
                (occurred + timedelta(days=7)).isoformat(),
                money_to_cents(payload["amount"]),
            ),
        )
        candidates = []
        for source in rows:
            if normalized(source["counterparty"]) == normalized(payload["counterparty"]) or source[
                "amount_cents"
            ] == money_to_cents(payload["amount"]):
                candidates.append(dict(source))
        return candidates[:30], links

    def _assessment(self, conn, actor, event, persist=False):
        latest = self._latest(conn, event)
        from .current_evidence import current_attachments

        attachments = current_attachments(
            self.db, conn, event, self.business._attachments(conn, event["id"])
        )
        documents, issues, references = [], [], []
        queued = conn.execute(
            "SELECT id,attachment_id,status,attempt_count FROM ocr_jobs WHERE event_id=? AND status IN ('queued','running','retry_wait','interrupted') ORDER BY id",
            (event["id"],),
        ).fetchall()
        current_ids = {item["id"] for item in attachments}
        queued = [row for row in queued if row["attachment_id"] in current_ids]
        for job in queued:
            if job["status"] in {"queued", "running", "retry_wait"}:
                issue = finding(
                    "queue_pending_" + job["attachment_id"],
                    "后台票据任务尚未完成，请等待结果或取消任务后人工核对",
                    job_id=job["id"],
                    non_overridable=True,
                )
            else:
                issue = finding(
                    "queue_interrupted_" + job["attachment_id"],
                    "后台识别结果不确定，请核对原件并决定是否重试",
                    job_id=job["id"],
                )
            issues.append(issue)
        for attachment in attachments:
            run = latest.get(attachment["id"])
            if not run or run["status"] != "succeeded":
                code = (
                    "ocr_running"
                    if run and run["status"] == "running"
                    else "ocr_missing_" + attachment["id"]
                )
                issues.append(
                    finding(
                        code, "附件尚未成功识别，需人工核对或重试", attachment_id=attachment["id"]
                    )
                )
                continue
            result = json.loads(run["result_json"])
            documents.extend(result["documents"])
            references.append({"attachment_id": attachment["id"], "extraction_id": run["id"]})
            for row in conn.execute(
                "SELECT code,severity,payload_json FROM ai_findings WHERE run_id=?", (run["id"],)
            ):
                issues.append(
                    {
                        "code": row["code"],
                        "severity": row["severity"],
                        "payload": json.loads(row["payload_json"]),
                    }
                )
        payload = json.loads(event["payload"])
        supplemental = self.ledger._case_guard().accepted_context_in_transaction(
            conn, actor, event["id"]
        )
        case_revision = self.ledger._case_guard().evidence_revision_in_transaction(
            conn, actor, event_id=event["id"]
        )
        mapping_for_cases = self._mapping(conn, event)
        if mapping_for_cases:
            try:
                self.access.book(conn, actor, mapping_for_cases["book_id"])
                case_source = conn.execute(
                    "SELECT * FROM sources WHERE id=? AND book_id=?",
                    (mapping_for_cases["source_id"], mapping_for_cases["book_id"]),
                ).fetchone()
                if self.ledger._case_guard().blocking_issues_in_transaction(
                    conn, actor, case_source
                ):
                    issues.append(
                        finding(
                            "case_pending",
                            "异常或补件尚未核验并关闭，请由责任人处理",
                            non_overridable=True,
                        )
                    )
            except AccountingError as error:
                if error.status not in {403, 404}:
                    raise
        company = conn.execute(
            "SELECT tax_id FROM companies WHERE id=?", (event["company_id"],)
        ).fetchone()
        issues.extend(reconcile_checks(payload, documents, company["tax_id"] if company else ""))
        duplicate_context = []
        keys = {invoice_key(document) for document in documents if invoice_key(document)}
        if keys:
            for other in conn.execute(
                "SELECT * FROM business_events WHERE firm_id=? AND company_id=? AND id<>?",
                (actor.firm_id, event["company_id"], event["id"]),
            ):
                if not self.business._visible(conn, actor, other):
                    continue
                for other_run in self._latest(conn, other).values():
                    if other_run["status"] == "succeeded":
                        overlapping = keys & {
                            invoice_key(document)
                            for document in json.loads(other_run["result_json"])["documents"]
                            if invoice_key(document)
                        }
                        if overlapping:
                            duplicate_context.append(other_run["id"])
            if duplicate_context:
                issues.append(
                    finding(
                        "duplicate_invoice_other_event",
                        "在当前授权范围内发现其他事项使用相同销售方及发票号码，请核对是否重复报销或记账",
                        match_count=len(duplicate_context),
                    )
                )
        candidates, links = self._bank_data(conn, actor, event)
        linked_blocks = []
        for bank in links:
            if self.ledger._case_guard().blocking_issues_in_transaction(conn, actor, bank):
                linked_blocks.append((bank["id"], "case"))
                issues.append(
                    finding(
                        "linked_bank_case_" + bank["id"],
                        "已关联银行流水存在尚未关闭的异常或补件，请先完成核验",
                        non_overridable=True,
                    )
                )
            if self.ledger._bank_import_guard().blocking_issues_in_transaction(conn, actor, bank):
                linked_blocks.append((bank["id"], "duplicate"))
                issues.append(
                    finding(
                        "linked_bank_duplicate_" + bank["id"],
                        "已关联银行流水存在待核验的相似交易，请先核验是否为独立交易",
                        non_overridable=True,
                    )
                )
        if payload["payment_status"] == "paid" and candidates and not links:
            issues.append(
                finding(
                    "bank_match_review",
                    "存在可能对应的银行流水，请人工关联或说明为何不关联",
                    candidate_count=len(candidates),
                )
            )
        if links and sum(row["amount_cents"] for row in links) != money_to_cents(payload["amount"]):
            issues.append(finding("bank_link_amount", "已关联流水合计与事项金额不一致"))
        revision_fingerprint = self.ledger._revision_guard().event_fingerprint_in_transaction(
            conn, actor, event
        )
        digest = fingerprint(
            {
                "payload": payload,
                "fact_revisions": revision_fingerprint,
                "company_tax_id": company["tax_id"] if company else "",
                "attachments": attachments,
                "runs": [(row["id"], row["status"]) for row in latest.values()],
                "bank": [
                    (
                        row["id"],
                        row["draft_status"],
                        row["amount_cents"],
                        row["direction"],
                        row["counterparty"],
                        row["date"],
                    )
                    for row in candidates
                ],
                "links": [row["id"] for row in links],
                "linked_blocks": linked_blocks,
                "duplicates": sorted(duplicate_context),
                "queue": [dict(row) for row in queued],
                "cases": case_revision,
                "supplements": supplemental,
            }
        )
        mapping = self._mapping(conn, event)
        if mapping:
            try:
                self.access.book(conn, actor, mapping["book_id"])
                current_source = conn.execute(
                    "SELECT * FROM sources WHERE id=? AND book_id=?",
                    (mapping["source_id"], mapping["book_id"]),
                ).fetchone()
                if current_source and self.ledger._source_state_issues(conn, current_source):
                    issues.append(
                        finding(
                            "fact_revision_pending",
                            "事实修订待独立审批或来源已被替代，请先完成事实核验",
                            non_overridable=True,
                        )
                    )
                draft = conn.execute(
                    "SELECT version FROM drafts WHERE id=? AND book_id=?",
                    (mapping["draft_id"], mapping["book_id"]),
                ).fetchone()
                proposals = (
                    conn.execute(
                        "SELECT r.* FROM ai_runs r LEFT JOIN ai_proposal_applications a ON a.run_id=r.id WHERE r.event_id=? AND r.kind='proposal' AND r.status='succeeded' AND (r.input_version=? OR a.id IS NOT NULL) ORDER BY r.created_at DESC,r.id DESC",
                        (event["id"], draft["version"]),
                    ).fetchall()
                    if draft
                    else []
                )
                for proposal in proposals:
                    for index, warning in enumerate(
                        json.loads(proposal["result_json"])["warnings"]
                    ):
                        issues.append(
                            finding(
                                "proposal_warning_" + proposal["id"] + "_" + str(index),
                                "模型提示需人工核对：" + warning,
                                proposal_id=proposal["id"],
                            )
                        )
            except AccountingError as error:
                if error.status not in {403, 404}:
                    raise
        if persist:
            for issue in issues:
                self._record_finding(
                    conn, event, None, issue, fingerprint({"context": digest, "issue": issue})
                )
        active = []
        for issue in issues:
            key = fingerprint({"context": digest, "issue": issue})
            row = conn.execute(
                "SELECT * FROM ai_findings WHERE event_id=? AND code=? AND fingerprint=?",
                (event["id"], issue["code"], key),
            ).fetchone()
            active.append(
                self._finding_json(conn, row)
                if row
                else {
                    "id": None,
                    "event_id": event["id"],
                    **issue,
                    "resolved": False,
                    "resolution_reason": None,
                }
            )
        if persist:
            from ..operations.service import record_exception_snapshot

            record_exception_snapshot(self.db, conn, actor, event, active, digest)
        return {
            "findings": active,
            "documents": documents,
            "references": references,
            "bank_candidates": candidates,
            "links": links,
            "fingerprint": digest,
        }

    @staticmethod
    def _finding_json(conn, row):
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        review = conn.execute(
            "SELECT reason,created_by,created_at FROM ai_finding_reviews WHERE finding_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        value.update(resolved=bool(review), resolution_reason=review["reason"] if review else None)
        value.pop("fingerprint", None)
        return value

    def reconcile(self, actor, event_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            event = self.business._event(conn, actor, event_id)
            self._require(conn, actor, event, "process", enabled=True)
            self._assessment(conn, actor, event, persist=True)
            self.business._audit(conn, actor, "intelligence.reconcile", event_id, {})
        return self.detail(actor, event_id)

    def detail(self, actor, event_id):
        with self.db.transaction(actor=actor) as conn:
            event = self.business._event(conn, actor, event_id)
            runs = [
                self._public_run(row)
                for row in conn.execute(
                    "SELECT * FROM ai_runs WHERE event_id=? ORDER BY created_at DESC,id DESC",
                    (event_id,),
                )
            ]
            for run in runs:
                if run["kind"] == "proposal":
                    try:
                        self.access.book(conn, actor, run["book_id"])
                    except AccountingError as error:
                        if error.status not in {403, 404}:
                            raise
                        run.update(
                            result=None,
                            book_id=None,
                            draft_id=None,
                            error_detail="分录建议由有账套权限的会计查看",
                        )
            assessment = self._assessment(conn, actor, event)
            findings = assessment["findings"] if self._enabled(conn, actor, event) or runs else []
            candidates, links = assessment["bank_candidates"], assessment["links"]
            public_bank = lambda row: {
                "id": row["id"],
                "external_id": row["external_id"],
                "date": row["date"],
                "description": row["description"],
                "counterparty": row["counterparty"],
                "amount": f"{row['amount_cents'] // 100}.{row['amount_cents'] % 100:02d}",
                "direction": row["direction"],
                "status": row["draft_status"] or "unprocessed",
            }
            from ..jobs.service import QueueService

            queue = getattr(self, "queue", None) or QueueService(self.db, self)
            return {
                "runs": runs,
                "findings": findings,
                "queue_jobs": queue.event_jobs(conn, actor, event),
                "bank_candidates": [public_bank(row) for row in candidates],
                "bank_links": [{**public_bank(row), "bank_source_id": row["id"]} for row in links],
                "permissions": self._permissions(conn, actor, event),
                "enabled": self._enabled(conn, actor, event),
                "context_fingerprint": assessment["fingerprint"],
            }

    def resolve(self, actor, finding_id, reason):
        reason = self._reason(reason)
        with self.db.transaction(write=True, actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM ai_findings WHERE id=? AND firm_id=?", (finding_id, actor.firm_id)
            ).fetchone()
            if not row:
                raise AccountingError("异常不存在或无权访问", 404)
            event = self.business._event(conn, actor, row["event_id"])
            self._require(conn, actor, event, "resolve")
            active_issue = next(
                (
                    issue
                    for issue in self._assessment(conn, actor, event)["findings"]
                    if issue["id"] == finding_id
                ),
                None,
            )
            if active_issue is None:
                raise AccountingError("该异常已不属于当前资料，请刷新后重新核对", 409)
            if active_issue["payload"].get("non_overridable"):
                raise AccountingError(
                    "任务尚未完成，请等待结果或取消任务；不能用说明跳过正在识别的票据", 409
                )
            conn.execute(
                "INSERT INTO ai_finding_reviews(id,finding_id,firm_id,company_id,event_id,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    finding_id,
                    actor.firm_id,
                    event["company_id"],
                    event["id"],
                    reason,
                    actor.user_id,
                    now(),
                ),
            )
            self.business._audit(
                conn, actor, "intelligence.finding.resolve", finding_id, {"reason": reason}
            )
            self._assessment(conn, actor, event, persist=True)
            return self._finding_json(conn, row)

    def _proposal_context(self, conn, actor, book_id, draft_id, expected_version):
        book = self.access.book(conn, actor, book_id, "edit")
        self.ledger._open(book)
        draft = self.ledger._editable_draft(conn, book_id, draft_id, expected_version)
        mapping = conn.execute(
            "SELECT * FROM business_mappings WHERE draft_id=? AND book_id=?", (draft_id, book_id)
        ).fetchone()
        if not mapping:
            raise AccountingError("本阶段仅为员工已移交的业务事项生成模型分录建议", 409)
        event = self.business._event(conn, actor, mapping["event_id"])
        self._require(conn, actor, event, "generate_proposal", enabled=True)
        source = conn.execute(
            "SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], book_id)
        ).fetchone()
        accounts = [
            dict(row)
            for row in conn.execute(
                "SELECT code,name,category,normal_balance,auxiliary_requirements,version FROM accounts WHERE book_id=? AND active=1 ORDER BY code",
                (book_id,),
            )
        ]
        for account in accounts:
            account["auxiliary_requirements"] = json.loads(account["auxiliary_requirements"])
        templates = [
            dict(row)
            for row in conn.execute(
                "SELECT t.id,t.current_version,v.definition,v.definition_fingerprint FROM journal_templates t JOIN journal_template_versions v ON v.template_id=t.id AND v.book_id=t.book_id AND v.version=t.current_version WHERE t.book_id=? AND t.active=1 ORDER BY t.id",
                (book_id,),
            )
        ]
        assessment = self._assessment(conn, actor, event)
        documents = []
        for ref in assessment["references"]:
            run = conn.execute(
                "SELECT result_json FROM ai_runs WHERE id=?", (ref["extraction_id"],)
            ).fetchone()
            documents.extend(
                {**document, **ref} for document in json.loads(run["result_json"])["documents"]
            )
        master_context = self.ledger._ledger_guard().context_in_transaction(
            conn, actor, event["company_id"], json.loads(event["payload"]), book_id
        )
        # Reviews may change after a proposal; facts, account set and draft version may not.
        settings = effective_settings(self.provider.settings, "journal")
        fact_context = {
            "draft_id": draft_id,
            "draft_version": draft["version"],
            "source": dict(source),
            "event_fact_version": event["fact_version"],
            "templates": templates,
            "evidence_ref": self.ledger._effective_evidence(conn, source),
            "accounts": accounts,
            "assessment": assessment["fingerprint"],
            "provider": settings.base_url,
            "format": settings.response_format,
            "model": settings.model,
            "prompt": JOURNAL_PROMPT_VERSION,
            "company_rules": self.ledger._learning_guard().policy_fingerprint(conn, actor, book_id),
            "master_context": master_context,
            "case_evidence": self.ledger._case_guard().evidence_revision_in_transaction(
                conn, actor, source=source
            ),
        }
        context = {
            "currency": "CNY",
            "book": {"id": book_id, "period": book["period"], "company_id": event["company_id"]},
            "business": json.loads(event["payload"]),
            "source": self.ledger._source_json(conn, source),
            "auxiliary_context": {
                "department": event["department_id"],
                "employee": event["applicant_id"],
                "counterparty": json.loads(event["payload"])["counterparty"],
                "project": json.loads(event["payload"]).get("project", ""),
            },
            "accounts": accounts,
            "documents": documents,
            "master_data": master_context,
            "supplements": self.ledger._case_guard().accepted_context_in_transaction(
                conn, actor, event["id"]
            ),
            "templates": [
                {
                    "id": item["id"],
                    "version": item["current_version"],
                    "definition": json.loads(item["definition"]),
                }
                for item in templates
            ],
            "bank_links": [
                {
                    "id": row["id"],
                    "amount_cents": row["amount_cents"],
                    "direction": row["direction"],
                }
                for row in assessment["links"]
            ],
            "findings": [
                {
                    "code": issue["code"],
                    "severity": issue["severity"],
                    "message": issue["payload"]["message"],
                    "resolved": issue["resolved"],
                }
                for issue in assessment["findings"]
            ],
            "constraints": "金额合计必须等于来源；已支付需银行存款方向匹配；未支付不得编造付款；税额不推测，不自行判断抵扣。只生成待人工复核草稿。",
        }
        return event, draft, source, assessment, context, fingerprint(fact_context)

    def _validate_proposal(self, conn, source, context, suggestion, actor=None):
        lines = [
            {
                "account_code": row.account_code,
                "debit_cents": money_to_cents(row.debit),
                "credit_cents": money_to_cents(row.credit),
            }
            for row in suggestion.lines
        ]
        issues = self.ledger._calculate_issues(
            source,
            lines,
            {row["code"] for row in context["accounts"]},
            context["source"]["evidence_ref"],
        )
        bank = (
            sum(row["debit_cents"] for row in lines if row["account_code"].startswith("1002")),
            sum(row["credit_cents"] for row in lines if row["account_code"].startswith("1002")),
        )
        if context["business"]["payment_status"] == "paid":
            expected = (
                (source["amount_cents"], 0)
                if source["direction"] == "income"
                else (0, source["amount_cents"])
            )
            if bank != expected:
                issues.append("已支付事项的银行存款金额或方向不符")
        elif bank != (0, 0):
            issues.append("未支付事项不得生成银行付款分录")
        # All citations must refer to a current extraction, an actual field, and its page.
        valid = set()
        for document in context["documents"]:
            for evidence in document["evidence"]:
                if document.get(evidence["field"]) is not None:
                    valid.add(
                        (
                            document["attachment_id"],
                            document["extraction_id"],
                            evidence["field"],
                            evidence["page"],
                        )
                    )
        if not suggestion.evidence_refs or any(
            (ref.attachment_id, ref.extraction_id, ref.field, ref.page) not in valid
            for ref in suggestion.evidence_refs
        ):
            issues.append("分录建议必须引用当前票据已识别字段的页码证据")
        if not any(ref.field == "gross_amount" for ref in suggestion.evidence_refs):
            issues.append("分录建议缺少票据金额证据引用")
        if actor:
            from ..accounting.catalog import CatalogService

            issues.extend(
                CatalogService(self.db).validate_auxiliaries_in_transaction(
                    conn,
                    actor,
                    context["book"]["id"],
                    [row.model_dump() for row in suggestion.lines],
                )
            )
        for line in suggestion.lines:
            for key, value in line.model_dump().get("auxiliary_values", {}).items():
                if value != context["auxiliary_context"].get(key):
                    issues.append("模型辅助值必须来自当前业务事实，不可编造人员、部门或项目")
        if issues:
            raise ProviderError(
                "invalid_proposal", "分录建议未通过金额、科目或证据校验，请会计人工处理", 422
            )

    def propose(self, actor, book_id, draft_id, request, execution=None):
        with self.db.transaction(write=True, actor=actor) as conn:
            event, draft, source, assessment, context, digest = self._proposal_context(
                conn, actor, book_id, draft_id, request.expected_version
            )
            if execution:
                execution.check(conn)
                execution.inputs(event, draft)
            run, execute = self._claim(
                conn,
                actor,
                event,
                "proposal",
                request.idempotency_key,
                digest,
                effective_settings(self.provider.settings, "journal").model,
                JOURNAL_PROMPT_VERSION,
                draft=draft,
            )
            if execution and execute:
                execution.bind(conn, run)
        if not execute:
            return self._public_run(run)
        result, error = None, None
        budget, attempted = self._meter().context(actor, run), False
        try:
            with self.db.transaction(write=True, actor=actor) as conn:
                current = self._proposal_context(
                    conn, actor, book_id, draft_id, request.expected_version
                )
                if current[-1] != digest:
                    raise AccountingError("记账资料已改变，请刷新后重新生成建议", 409)
                if execution:
                    execution.inputs(current[0], current[1])
                    execution.before_send(conn)
                budget.before_send(conn)
            attempted = True
            try:
                before_model = getattr(execution, "before_model", None)
                if callable(before_model):
                    before_model()
                from ..concurrency.provider import provider_scope

                with provider_scope(self.db, actor, run["company_id"]):
                    result = self.provider.suggest_journal(context)
            finally:
                after_model = getattr(execution, "after_model", None)
                if callable(after_model):
                    after_model()
        except ProviderError as caught:
            error = caught
        except AccountingError:
            self._abandon(actor, run["id"])
            raise
        except Exception:
            error = ProviderError("processing_failed", "记账建议处理失败，请会计人工处理")
        finally:
            budget.finish(result, error, attempted)
        try:
            with self.db.transaction(write=True, actor=actor) as conn:
                event, draft, source, _, current_context, current_digest = self._proposal_context(
                    conn, actor, book_id, draft_id, request.expected_version
                )
                if current_digest != digest:
                    raise AccountingError("记账资料已改变，旧建议未发布，请重新生成", 409)
                if execution:
                    execution.check(conn)
                    execution.inputs(event, draft)
                if result and error is None:
                    try:
                        self._validate_proposal(
                            conn, source, current_context, result.data, actor=actor
                        )
                    except ProviderError as caught:
                        result, error = None, caught
                finished = self._finish(conn, actor, run, result, error)
                self._assessment(conn, actor, event, persist=True)
                if execution:
                    execution.finish(conn, finished, error)
                return self._public_run(finished)
        except AccountingError:
            self._abandon(actor, run["id"])
            raise

    def apply(self, actor, run_id, expected_version):
        with self.db.transaction(write=True, actor=actor) as conn:
            return self._apply_in_transaction(conn, actor, run_id, expected_version)

    def _apply_in_transaction(self, conn, actor, run_id, expected_version, automatic=False):
        """Caller owns the write transaction, including its durable job receipt."""
        run = conn.execute(
            "SELECT * FROM ai_runs WHERE id=? AND firm_id=? AND kind='proposal'",
            (run_id, actor.firm_id),
        ).fetchone()
        if not run:
            raise AccountingError("记账建议不存在或无权访问", 404)
        book = self.access.book(conn, actor, run["book_id"], "edit")
        event = self.business._event(conn, actor, run["event_id"])
        receipt = conn.execute(
            "SELECT * FROM ai_proposal_applications WHERE run_id=?", (run_id,)
        ).fetchone()
        if receipt:
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?",
                (receipt["draft_id"], receipt["book_id"]),
            ).fetchone()
            # An old-version replay is just receipt recovery. Explicitly selecting
            # the current, automatically adopted version is a new human decision.
            if (
                not automatic
                and book["period_status"] == "open"
                and draft["status"] == "draft"
                and draft["version"] == receipt["applied_draft_version"] == expected_version
                and not draft["last_manual_edit_version"]
            ):
                changed = conn.execute(
                    "UPDATE drafts SET last_manual_edit_version=version WHERE id=? AND book_id=? AND version=? AND status='draft' AND last_manual_edit_version=0",
                    (draft["id"], draft["book_id"], expected_version),
                )
                if changed.rowcount == 1:
                    self.ledger._audit(
                        conn,
                        actor,
                        draft["book_id"],
                        "intelligence.proposal.confirm",
                        draft["id"],
                        {
                            "run_id": run_id,
                            "automatic": False,
                            "confirmed_draft_version": expected_version,
                        },
                    )
            return self.ledger._draft_json(conn, draft, actor=actor)
        event, draft, source, assessment, context, digest = self._proposal_context(
            conn, actor, run["book_id"], run["draft_id"], expected_version
        )
        if automatic and draft["last_manual_edit_version"]:
            raise AccountingError("会计已人工修改草稿，请人工核对候选建议，后台不会覆盖", 409)
        self._require(conn, actor, event, "apply_proposal", enabled=True)
        if (
            run["status"] != "succeeded"
            or run["input_version"] != expected_version
            or run["input_fingerprint"] != digest
        ):
            raise AccountingError("建议已失效或未成功完成，请按当前资料重新生成", 409)
        if any(
            issue["severity"] == "blocking" and not issue["resolved"]
            for issue in assessment["findings"]
        ):
            raise AccountingError("尚有票据或流水核对异常，须人工处理后才能采用建议", 409)
        suggestion = JournalSuggestion.model_validate(json.loads(run["result_json"]))
        self._validate_proposal(conn, source, context, suggestion, actor=actor)
        update = DraftUpdate(
            expected_version=expected_version,
            description=suggestion.description,
            lines=[row.model_dump() for row in suggestion.lines],
        )
        manual_version = draft["last_manual_edit_version"] if automatic else expected_version + 1
        changed = conn.execute(
            "UPDATE drafts SET description=?,explanation=?,version=version+1,last_manual_edit_version=? WHERE id=? AND book_id=? AND version=? AND status='draft'",
            (
                update.description,
                "模型建议（待会计复核）：" + suggestion.reasoning,
                manual_version,
                draft["id"],
                draft["book_id"],
                expected_version,
            ),
        )
        if changed.rowcount != 1:
            raise AccountingError("凭证版本已改变，采用失败，请刷新", 409)
        conn.execute(
            "DELETE FROM draft_lines WHERE draft_id=? AND book_id=?",
            (draft["id"], draft["book_id"]),
        )
        conn.executemany(
            "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
            [
                (
                    draft["id"],
                    draft["book_id"],
                    i,
                    row.account_code,
                    money_to_cents(row.debit),
                    money_to_cents(row.credit),
                )
                for i, row in enumerate(update.lines)
            ],
        )
        self.ledger._save_auxiliaries(conn, actor, draft, update.lines)
        conn.execute(
            "INSERT INTO ai_proposal_applications(id,run_id,firm_id,company_id,event_id,book_id,draft_id,applied_draft_version,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                run_id,
                actor.firm_id,
                event["company_id"],
                event["id"],
                draft["book_id"],
                draft["id"],
                expected_version + 1,
                actor.user_id,
                now(),
            ),
        )
        self.ledger._audit(
            conn,
            actor,
            draft["book_id"],
            "intelligence.proposal.apply",
            draft["id"],
            {
                "run_id": run_id,
                "automatic": automatic,
                "previous_version": expected_version,
                "before_description": draft["description"],
                "lines": [row.model_dump() for row in update.lines],
            },
        )
        self._assessment(conn, actor, event, persist=True)
        return self.ledger._draft_json(
            conn,
            conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft["id"], draft["book_id"])
            ).fetchone(),
            actor=actor,
        )

    def link_bank(self, actor, event_id, request):
        reason = self._reason(request.reason)
        if len(set(request.bank_source_ids)) != len(request.bank_source_ids):
            raise AccountingError("银行流水不可重复选择")
        with self.db.transaction(write=True, actor=actor) as conn:
            event = self.business._event(conn, actor, event_id)
            self._require(conn, actor, event, "link_bank", enabled=True)
            mapping = self._mapping(conn, event)
            self.ledger._open(self.access.book(conn, actor, mapping["book_id"], "edit"))
            original = conn.execute(
                "SELECT * FROM sources WHERE id=? AND book_id=?",
                (mapping["source_id"], mapping["book_id"]),
            ).fetchone()
            if self.ledger._source_state_issues(conn, original):
                raise AccountingError("事实修订未完成，请先完成人工核验", 409)
            if self.ledger._settlement_guard().source_allocated(conn, actor, original):
                raise AccountingError("业务已有收付分配，请通过核销流程处理", 409)
            payload = json.loads(event["payload"])
            if payload["payment_status"] != "paid":
                raise AccountingError("仅已支付的事项可关联银行流水", 409)
            linked = conn.execute(
                "SELECT bank_source_id FROM ai_bank_links WHERE event_id=?", (event_id,)
            ).fetchall()
            union = {row["bank_source_id"] for row in linked} | set(request.bank_source_ids)
            sources = []
            for source_id in sorted(union):
                row = conn.execute(
                    "SELECT s.*,d.status AS draft_status,l.event_id AS linked_event_id FROM sources s LEFT JOIN drafts d ON d.source_id=s.id LEFT JOIN ai_bank_links l ON l.bank_source_id=s.id WHERE s.id=? AND s.book_id=? AND s.source_kind='bank'",
                    (source_id, mapping["book_id"]),
                ).fetchone()
                if not row:
                    raise AccountingError("流水不存在、类型错误或不属于当前账套", 404)
                if row["draft_status"] is None:
                    raise AccountingError("请先在账套为该银行流水生成草稿，再人工确认关联", 409)
                if self.ledger._source_state_issues(
                    conn, row
                ) or self.ledger._bank_import_guard().blocking_issues_in_transaction(
                    conn, actor, row
                ):
                    raise AccountingError("流水已隔离或存在待核验的重复候选，不能继续关联", 409)
                if self.ledger._settlement_guard().source_allocated(conn, actor, row):
                    raise AccountingError("流水已有收付分配，不能重复关联完整业务事项", 409)
                if (
                    row["direction"] != payload["direction"]
                    or row["draft_status"] in {"approved", "exported"}
                    or row["linked_event_id"] not in {None, event_id}
                ):
                    raise AccountingError("流水方向不符、已审核导出或已关联其他事项", 409)
                sources.append(row)
            if sum(row["amount_cents"] for row in sources) != money_to_cents(payload["amount"]):
                raise AccountingError("关联流水合计必须等于事项金额", 409)
            for row in sources:
                if not row["linked_event_id"]:
                    conn.execute(
                        "INSERT INTO ai_bank_links(bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                        (
                            row["id"],
                            actor.firm_id,
                            event["company_id"],
                            event_id,
                            mapping["book_id"],
                            actor.user_id,
                            now(),
                        ),
                    )
            self.business._audit(
                conn,
                actor,
                "intelligence.bank.link",
                event_id,
                {"bank_source_ids": sorted(union), "reason": reason},
            )
            self._assessment(conn, actor, event, persist=True)
        return self.detail(actor, event_id)

    def unlink_bank(self, actor, event_id, source_id, reason):
        reason = self._reason(reason)
        with self.db.transaction(write=True, actor=actor) as conn:
            event = self.business._event(conn, actor, event_id)
            self._require(conn, actor, event, "link_bank", enabled=True)
            mapping = self._mapping(conn, event)
            self.ledger._open(self.access.book(conn, actor, mapping["book_id"], "edit"))
            changed = conn.execute(
                "DELETE FROM ai_bank_links WHERE bank_source_id=? AND event_id=? AND book_id=?",
                (source_id, event_id, mapping["book_id"]),
            )
            if changed.rowcount != 1:
                raise AccountingError("关联不存在或已解除，请刷新", 404)
            self.business._audit(
                conn,
                actor,
                "intelligence.bank.unlink",
                event_id,
                {"bank_source_id": source_id, "reason": reason},
            )
            self._assessment(conn, actor, event, persist=True)
        return self.detail(actor, event_id)

    def exceptions(self, actor, company_id=None, limit=50, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise AccountingError("分页范围无效")
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            if company_id:
                self.access.company(conn, actor, company_id)
            rows = conn.execute(
                "SELECT e.*,c.name AS company_name FROM business_events e JOIN companies c ON c.id=e.company_id JOIN firm_clients fc ON fc.firm_id=e.firm_id AND fc.company_id=e.company_id WHERE e.firm_id=? AND fc.status='active'"
                + (" AND e.company_id=?" if company_id else "")
                + " ORDER BY e.created_at DESC,e.id DESC",
                (actor.firm_id, company_id) if company_id else (actor.firm_id,),
            ).fetchall()
            findings = []
            for event in rows:
                if self.business._visible(conn, actor, event) and self._enabled(conn, actor, event):
                    assessment = self._assessment(conn, actor, event)
                    for issue in assessment["findings"]:
                        if not issue["resolved"]:
                            findings.append(
                                {
                                    **issue,
                                    "event_description": json.loads(event["payload"])[
                                        "description"
                                    ],
                                    "company_name": event["company_name"],
                                }
                            )
            return {"findings": findings[offset : offset + limit], "total": len(findings)}
