"""Employee intake is private preparation; confirmed facts use the existing pipeline."""

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint, now
from ..accounting.telemetry import tag_authorized_company
from ..business.models import BusinessEventCreate
from ..business.service import BusinessService, encode
from ..business.templates import validate_payload
from ..intelligence.cache import config_key, store_cache
from ..intelligence.documents import prepare_document
from ..intelligence.models import ProviderResult
from ..intelligence.ocr import (
    engine_enabled,
    extraction_findings,
    prompt_version,
    usage_payload,
    stored_extraction,
)
from ..intelligence.provider import EXTRACTION_PROMPT_VERSION, ProviderError, effective_settings
from ..intelligence.structured import (
    is_structured,
    parse_invoice_xml,
    STRUCTURED_VERSION,
    STRUCTURED_PROVIDER,
    extraction_metadata,
    verification_finding,
)
from ..operations.meter import MeterService


class IntakeService:
    def __init__(self, db, storage, provider, meter=None, business=None, intelligence=None):
        self.db, self.storage, self.provider = db, storage, provider
        self.business = business or BusinessService(db)
        self.meter = meter or MeterService(db)
        self.intelligence = intelligence

    def _membership(self, conn, actor, company, department=None, preparing=False):
        member = self.business._membership(conn, actor, company)
        if not member:
            raise AccountingError("需要当前企业成员授权才能录入事项", 403)
        if preparing:
            policy = self.business.submission.current(conn, actor.firm_id, company)
            if policy["mode"] == "direct":
                if member["role"] not in {"owner", "admin", "finance"}:
                    raise AccountingError("企业直接提交模式仅允许负责人、管理员或出纳财务录入", 403)
            elif department is None:
                raise AccountingError("部门审批模式须选择有效部门", 422)
        if department is not None:
            if member["role"] == "employee" and member["department_id"] != department:
                raise AccountingError("员工只能在当前所属部门录入，请联系管理员核对部门", 403)
            if not conn.execute(
                "SELECT 1 FROM departments WHERE id=? AND company_id=?", (department, company)
            ).fetchone():
                raise AccountingError("部门不属于当前企业", 422)
        tag_authorized_company(company)
        return member

    def _draft(self, conn, actor, draft_id, editable=False):
        draft = conn.execute(
            "SELECT * FROM intake_drafts WHERE id=? AND firm_id=? AND created_by=?",
            (draft_id, actor.firm_id, actor.user_id),
        ).fetchone()
        if not draft:
            raise AccountingError("录入草稿不存在或无权访问", 404)
        try:
            self._membership(
                conn,
                actor,
                draft["company_id"],
                draft["department_id"] if editable else None,
                preparing=editable,
            )
        except AccountingError:
            raise AccountingError("录入草稿不存在或当前授权已失效", 404) from None
        if editable and draft["status"] != "draft":
            raise AccountingError("事项已经保存，请从本人事项查看和修改", 409)
        return draft

    @staticmethod
    def _version(draft, expected):
        if draft["version"] != expected:
            raise AccountingError("录入草稿已更新，请重新读取后核对", 409)

    def _company_of(self, actor, draft_id):
        """Resolve the draft's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            return self._draft(conn, actor, draft_id)["company_id"]

    def _attachments(self, conn, draft_id):
        return conn.execute(
            "SELECT * FROM intake_attachments WHERE intake_id=? ORDER BY created_at,id", (draft_id,)
        ).fetchall()

    def _enabled(self, conn, draft):
        row = conn.execute(
            "SELECT enabled FROM ai_company_settings WHERE firm_id=? AND company_id=?",
            (draft["firm_id"], draft["company_id"]),
        ).fetchone()
        return bool(row and row["enabled"])

    @staticmethod
    def _run_json(row):
        return {
            key: row[key]
            for key in (
                "id",
                "attachment_id",
                "status",
                "error_code",
                "error_detail",
                "created_at",
                "finished_at",
            )
        } | {
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "checks": json.loads(row["checks_json"]),
            **extraction_metadata(row),
        }

    def _json(self, conn, actor, draft):
        attachments = [
            {key: row[key] for key in ("id", "filename", "media_type", "sha256", "size_bytes")}
            for row in self._attachments(conn, draft["id"])
        ]
        runs = [
            self._run_json(row)
            for row in conn.execute(
                "SELECT * FROM intake_runs WHERE intake_id=? ORDER BY created_at DESC,id DESC",
                (draft["id"],),
            )
        ]
        labels = conn.execute(
            "SELECT c.name AS company_name,d.name AS department_name FROM companies c LEFT JOIN departments d ON d.company_id=c.id AND d.id=? WHERE c.id=?",
            (draft["department_id"], draft["company_id"]),
        ).fetchone()
        return {
            key: draft[key]
            for key in (
                "id",
                "company_id",
                "department_id",
                "template_id",
                "status",
                "version",
                "event_id",
                "updated_at",
            )
        } | {
            "fields": json.loads(draft["fields_json"]),
            "attachments": attachments,
            "runs": runs,
            "company_name": labels["company_name"] if labels else "",
            "department_name": (labels["department_name"] or "") if labels else "",
            "submission_policy": self.business.submission.current(
                conn, actor.firm_id, draft["company_id"]
            ),
            "vision_ready": self.provider.settings.extraction_available,
            "structured_ready": True,
            "ai_enabled": self._enabled(conn, draft),
        }

    def list(self, actor, company_id=None):
        with self.db.transaction(actor=actor) as conn:
            self.business.access.require_user(conn, actor.user_id)
            if company_id:
                self._membership(conn, actor, company_id)
            rows = conn.execute(
                "SELECT d.* FROM intake_drafts d JOIN company_memberships cm ON cm.company_id=d.company_id AND cm.user_id=d.created_by JOIN firm_clients fc ON fc.firm_id=d.firm_id AND fc.company_id=d.company_id AND fc.status='active' WHERE d.firm_id=? AND d.created_by=? AND d.status='draft'"
                + (" AND d.company_id=?" if company_id else "")
                + " ORDER BY d.updated_at DESC,d.id DESC LIMIT 50",
                (actor.firm_id, actor.user_id, *([company_id] if company_id else [])),
            ).fetchall()
            return {"drafts": [self._json(conn, actor, row) for row in rows]}

    def get(self, actor, draft_id):
        company_id = self._company_of(actor, draft_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._draft(conn, actor, draft_id)
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            expired = conn.execute(
                "SELECT id FROM intake_runs WHERE intake_id=? AND status='running' AND created_at<=?",
                (draft_id, cutoff),
            ).fetchall()
            for row in expired:
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.abandon_intake_run(?)", (row["id"],))
                else:
                    conn.execute(
                        "UPDATE intake_runs SET status='stale',result_json=NULL,error_code='expired',error_detail='识别已中断，原件保留；请管理员核定费用后重试',finished_at=? WHERE id=? AND status='running'",
                        (now(), row["id"]),
                    )
            return self._json(conn, actor, self._draft(conn, actor, draft_id))

    def create(self, actor, request):
        digest = fingerprint(request.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(
            write=True, actor=actor, company_id=request.company_id, write_scope="company"
        ) as conn:
            self._membership(conn, actor, request.company_id, request.department_id, preparing=True)
            prior = conn.execute(
                "SELECT * FROM intake_drafts WHERE firm_id=? AND created_by=? AND create_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["create_fingerprint"] != digest:
                    raise AccountingError("该保存编号已用于其他录入内容", 409)
                return self._json(conn, actor, self._draft(conn, actor, prior["id"]))
            draft_id, stamp = uuid4().hex, now()
            conn.execute(
                "INSERT INTO intake_drafts(id,firm_id,company_id,department_id,created_by,template_id,fields_json,status,version,create_key,create_fingerprint,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'draft',1,?,?,?,?)",
                (
                    draft_id,
                    actor.firm_id,
                    request.company_id,
                    request.department_id,
                    actor.user_id,
                    request.template_id,
                    encode(request.fields.model_dump()),
                    request.idempotency_key,
                    digest,
                    stamp,
                    stamp,
                ),
            )
            return self._json(conn, actor, self._draft(conn, actor, draft_id))

    def update(self, actor, draft_id, request):
        company_id = self._company_of(actor, draft_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            draft = self._draft(conn, actor, draft_id, editable=True)
            self._version(draft, request.expected_version)
            conn.execute(
                "UPDATE intake_drafts SET fields_json=?,template_id=?,version=version+1,updated_at=? WHERE id=? AND version=?",
                (
                    encode(request.fields.model_dump()),
                    request.template_id,
                    now(),
                    draft_id,
                    request.expected_version,
                ),
            )
            return self._json(conn, actor, self._draft(conn, actor, draft_id))

    def require_upload(self, actor, draft_id, version):
        with self.db.transaction(actor=actor) as conn:
            self._version(self._draft(conn, actor, draft_id, editable=True), version)

    def add_attachment(self, actor, draft_id, version, metadata):
        company_id = self._company_of(actor, draft_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            draft = self._draft(conn, actor, draft_id, editable=True)
            self._version(draft, version)
            attachments = self._attachments(conn, draft_id)
            if (
                len(attachments) >= 10
                or sum(row["size_bytes"] for row in attachments) + metadata["size_bytes"]
                > 50 * 1024 * 1024
            ):
                raise AccountingError("每件事项最多 10 个附件，合计不超过 50 MB", 422)
            if any(row["sha256"] == metadata["sha256"] for row in attachments):
                return self._json(conn, actor, draft)
            conn.execute(
                "INSERT INTO intake_attachments(id,intake_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    metadata["id"],
                    draft_id,
                    actor.firm_id,
                    draft["company_id"],
                    metadata["storage_key"],
                    metadata["filename"],
                    metadata["media_type"],
                    metadata["sha256"],
                    metadata["size_bytes"],
                    now(),
                    actor.user_id,
                ),
            )
            conn.execute(
                "UPDATE intake_drafts SET version=version+1,updated_at=? WHERE id=?",
                (now(), draft_id),
            )
            result = self._json(conn, actor, self._draft(conn, actor, draft_id))
            from ..business.storage import retain_original

            retain_original(metadata)
            return result

    def download(self, actor, attachment_id):
        with self.db.transaction(actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM intake_attachments WHERE id=? AND firm_id=?",
                (attachment_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("附件不存在或无权访问", 404)
            self._draft(conn, actor, row["intake_id"])
            metadata = dict(row)
        return self.storage.read(metadata), metadata

    def recognize(self, actor, draft_id, attachment_id, request):
        company_id = self._company_of(actor, draft_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            draft = self._draft(conn, actor, draft_id, editable=True)
            self._version(draft, request.expected_version)
            if not self._enabled(conn, draft):
                raise AccountingError("企业未启用 AI，请继续手工填写或联系管理员", 403)
            attachment = conn.execute(
                "SELECT * FROM intake_attachments WHERE id=? AND intake_id=?",
                (attachment_id, draft_id),
            ).fetchone()
            if not attachment:
                raise AccountingError("附件不存在或无权访问", 404)
            structured = is_structured(attachment["media_type"])
            if not structured and not self.provider.settings.extraction_available:
                raise AccountingError("尚未配置票据识别服务，请继续手工填写", 409)
            digest = self._configuration_input(attachment)
            prior = conn.execute(
                "SELECT * FROM intake_runs WHERE intake_id=? AND idempotency_key=?",
                (draft_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["input_fingerprint"] != digest or prior["attachment_id"] != attachment_id:
                    raise AccountingError("识别编号已用于其他附件或模型配置", 409)
                return self._run_json(prior)
            latest = conn.execute(
                "SELECT r.* FROM intake_runs r WHERE r.intake_id=? AND r.attachment_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT 1",
                (draft_id, attachment_id),
            ).fetchone()
            if latest and latest["status"] == "running":
                if datetime.fromisoformat(latest["created_at"]) > datetime.now(
                    timezone.utc
                ) - timedelta(minutes=3):
                    raise AccountingError("该附件仍在识别，请读取已保存进度", 409)
                conn.execute(
                    "UPDATE intake_runs SET status='failed',error_code='expired',error_detail='识别已中断，核定费用后可重试',finished_at=? WHERE id=?",
                    (now(), latest["id"]),
                )
            unresolved = (
                conn.execute(
                    "SELECT accounting.intake_request_unresolved(?,?)", (draft_id, attachment_id)
                ).fetchone()[0]
                if self.db.is_postgres
                else conn.execute(
                    "SELECT 1 FROM model_costs c JOIN intake_runs r ON r.id=c.intake_run_id WHERE r.intake_id=? AND r.attachment_id=? AND c.state IN ('reserved','sent','uncertain')",
                    (draft_id, attachment_id),
                ).fetchone()
            )
            if unresolved:
                raise AccountingError(
                    "上次请求结果或费用尚未确认，请管理员核定后再识别；可继续手工填写", 409
                )
            run_id, stamp = uuid4().hex, now()
            settings = effective_settings(self.provider.settings, "ocr")
            conn.execute(
                "INSERT INTO intake_runs(id,intake_id,attachment_id,firm_id,company_id,created_by,input_fingerprint,idempotency_key,status,provider,model,prompt_version,created_at) VALUES(?,?,?,?,?,?,?,?,'running',?,?,?,?)",
                (
                    run_id,
                    draft_id,
                    attachment_id,
                    actor.firm_id,
                    draft["company_id"],
                    actor.user_id,
                    digest,
                    request.idempotency_key,
                    STRUCTURED_PROVIDER
                    if structured
                    else urlsplit(settings.base_url).hostname or "openai-compatible",
                    "einvoice-0.3" if structured else settings.model,
                    STRUCTURED_VERSION if structured else prompt_version(self.provider.settings),
                    stamp,
                ),
            )
            run = dict(
                conn.execute("SELECT * FROM intake_runs WHERE id=?", (run_id,)).fetchone()
            ) | {"kind": "ocr"}
            metadata = dict(attachment)
        budget, attempted, result, error = self.meter.context(actor, run), False, None, None
        processing_deadline = time.monotonic() + 150
        try:
            data = self.storage.read(metadata)
            local = parse_invoice_xml(data) if structured else None
            pages = (
                local.pages
                if local
                else (
                    prepare_document(data, metadata["media_type"], high_resolution=True)
                    if engine_enabled(self.provider.settings)
                    else prepare_document(data, metadata["media_type"])
                )
            )
            with self.db.transaction(
                write=True, actor=actor, company_id=company_id, write_scope="company"
            ) as conn:
                current = self._draft(conn, actor, draft_id, editable=True)
                if (
                    not self._enabled(conn, current)
                    or self._configuration_input(metadata) != digest
                ):
                    raise AccountingError("企业处理配置已改变，识别未发送", 403)
                if not local:
                    budget.before_send(conn)
            if local:
                result = local.result
            else:
                attempted = True
                from ..concurrency.provider import provider_scope

                def reauthorize():
                    with self.db.transaction(actor=actor) as check_conn:
                        checked = self._draft(check_conn, actor, draft_id, editable=True)
                        if (
                            not self._enabled(check_conn, checked)
                            or self._configuration_input(metadata) != digest
                        ):
                            raise AccountingError("授权或识别配置已改变，字段提取未发送", 403)

                with provider_scope(self.db, actor, run["company_id"]):
                    result = (
                        self.provider.extract_invoice(
                            pages,
                            authorization_check=reauthorize,
                            processing_deadline=processing_deadline,
                        )
                        if engine_enabled(self.provider.settings)
                        else self.provider.extract_invoice(pages)
                    )
                    if getattr(result.data, "ocr_evidence", None):
                        result.data.ocr_evidence.attachment_sha256 = metadata["sha256"]
        except ProviderError as failure:
            error = failure
        except AccountingError as caught:
            if getattr(caught, "llm_attempted", None) is False:
                error = caught
            self._abandon(actor, run_id)
            raise
        except BaseException:
            self._abandon(actor, run_id)
            raise
        finally:
            budget.finish(result, error, attempted)
        try:
            with self.db.transaction(
                write=True, actor=actor, company_id=company_id, write_scope="company"
            ) as conn:
                current = self._draft(conn, actor, draft_id, editable=True)
                if (
                    not self._enabled(conn, current)
                    or self._configuration_input(metadata) != digest
                ):
                    raise AccountingError("企业处理配置已改变，识别结果未发布", 403)
                checks = [] if error else extraction_findings(result.data, pages)
                if structured and not error:
                    checks.append(verification_finding())
                conn.execute(
                    "UPDATE intake_runs SET status=?,result_json=?,usage_json=?,error_code=?,error_detail=?,checks_json=?,finished_at=? WHERE id=? AND status='running'",
                    (
                        "failed" if error else "succeeded",
                        None if error else encode(result.data.model_dump()),
                        encode(usage_payload(result, error)),
                        error.code if error else "",
                        error.detail if error else "",
                        encode(checks),
                        now(),
                        run_id,
                    ),
                )
                return self._run_json(
                    conn.execute("SELECT * FROM intake_runs WHERE id=?", (run_id,)).fetchone()
                )
        except BaseException:
            self._abandon(actor, run_id)
            raise

    def _configuration_input(self, attachment):
        if is_structured(dict(attachment).get("media_type", "")) or engine_enabled(
            self.provider.settings
        ):
            return fingerprint(
                {
                    "sha256": attachment["sha256"],
                    "config": config_key(self.provider.settings, attachment["media_type"]),
                }
            )
        settings = effective_settings(self.provider.settings, "ocr")
        return fingerprint(
            {
                "sha256": attachment["sha256"],
                "model": settings.model,
                "provider": settings.base_url,
                "format": settings.response_format,
                "prompt": EXTRACTION_PROMPT_VERSION,
            }
        )

    def _abandon(self, actor, run_id):
        try:
            with self.db.transaction(actor=actor) as conn:
                row = conn.execute(
                    "SELECT company_id FROM intake_runs WHERE id=? AND firm_id=?",
                    (run_id, actor.firm_id),
                ).fetchone()
            if not row:
                return
            with self.db.transaction(
                write=True, actor=actor, company_id=row["company_id"], write_scope="company"
            ) as conn:
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.abandon_intake_run(?)", (run_id,))
                else:
                    conn.execute(
                        "UPDATE intake_runs SET status='stale',result_json=NULL,error_code='scope_changed',error_detail='权限或资料已改变，结果未发布',finished_at=? WHERE id=? AND status='running' AND created_by=?",
                        (now(), run_id, actor.user_id),
                    )
        except Exception:
            pass

    def complete(self, actor, draft_id, request):
        if not request.fields_confirmed:
            raise AccountingError("请核对原件并确认日期、金额和交易对方等字段", 422)
        digest = fingerprint(request.model_dump(exclude={"idempotency_key", "expected_version"}))
        company_id = self._company_of(actor, draft_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            draft = self._draft(conn, actor, draft_id)
            if draft["status"] == "completed":
                if (
                    draft["completion_key"] != request.idempotency_key
                    or draft["completion_fingerprint"] != digest
                ):
                    raise AccountingError("录入草稿已保存，请查看本人事项", 409)
                return {
                    "event": self.business._event_json(
                        conn, actor, self.business._event(conn, actor, draft["event_id"])
                    ),
                    "intake_id": draft_id,
                }
            self._draft(conn, actor, draft_id, editable=True)
            self._version(draft, request.expected_version)
            attachments = self._attachments(conn, draft_id)
            if request.submit and not attachments:
                raise AccountingError("提交审批前请上传至少一个原始附件", 422)
            payload = request.payload.model_dump()
            validate_payload(draft["template_id"], 1, payload)
            event = self.business.create(
                actor,
                BusinessEventCreate(
                    company_id=draft["company_id"],
                    department_id=draft["department_id"],
                    template_id=draft["template_id"],
                    payload=payload,
                    idempotency_key="intake-" + draft_id,
                ),
                conn=conn,
            )
            for attachment in attachments:
                event = self.business.add_attachment(
                    actor, event["id"], event["version"], dict(attachment), conn=conn
                )
            # Retain recognized originals in the same evidence pipeline. This is
            # a documented transfer, not a second model call or a second charge.
            if self.intelligence:
                event_row = self.business._event(conn, actor, event["id"])
                for attachment in attachments:
                    prior = conn.execute(
                        "SELECT * FROM intake_runs WHERE intake_id=? AND attachment_id=? AND status='succeeded' ORDER BY created_at DESC,id DESC LIMIT 1",
                        (draft_id, attachment["id"]),
                    ).fetchone()
                    if not prior:
                        continue
                    recognized = stored_extraction(json.loads(prior["result_json"]))
                    transferred, _ = self.intelligence._claim(
                        conn,
                        actor,
                        event_row,
                        "ocr",
                        "intake-transfer-" + prior["id"],
                        fingerprint({"intake_run": prior["id"], "sha256": attachment["sha256"]}),
                        prior["model"],
                        prior["prompt_version"],
                        attachment_id=attachment["id"],
                        input_version=event_row["fact_version"],
                        provider_name=prior["provider"],
                    )
                    finished = self.intelligence._finish(
                        conn,
                        actor,
                        transferred,
                        ProviderResult(
                            recognized,
                            prior["provider"],
                            prior["model"],
                            prior["prompt_version"],
                            None,
                        ),
                        cache_hit=True,
                    )
                    for issue in json.loads(prior["checks_json"]):
                        self.intelligence._record_finding(
                            conn,
                            event_row,
                            finished["id"],
                            issue,
                            fingerprint({"run": finished["id"], "issue": issue}),
                        )
                    current_config = self._configuration_input(attachment)
                    if (
                        self._enabled(conn, draft)
                        and prior["input_fingerprint"] == current_config
                        and not any(
                            issue["severity"] == "blocking"
                            for issue in json.loads(prior["checks_json"])
                        )
                    ):
                        store_cache(
                            self.db,
                            conn,
                            actor,
                            event_row,
                            attachment,
                            config_key(self.provider.settings, attachment["media_type"]),
                            finished,
                        )
            if request.submit:
                event = self.business.submit(actor, event["id"], event["version"], conn=conn)
            conn.execute(
                "UPDATE intake_drafts SET status='completed',fields_json=?,event_id=?,completion_key=?,completion_fingerprint=?,version=version+1,updated_at=? WHERE id=? AND version=?",
                (
                    encode(payload),
                    event["id"],
                    request.idempotency_key,
                    digest,
                    now(),
                    draft_id,
                    draft["version"],
                ),
            )
            self.business._audit(
                conn,
                actor,
                "intake.complete",
                draft_id,
                {
                    "event_id": event["id"],
                    "attachment_count": len(attachments),
                    "submitted": request.submit,
                },
            )
            return {"event": event, "intake_id": draft_id}
