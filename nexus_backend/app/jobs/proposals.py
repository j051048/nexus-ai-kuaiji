"""Durable automatic candidates. Authorization and adoption remain deterministic."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint
from ..intelligence.provider import JOURNAL_PROMPT_VERSION, effective_settings
from .visibility import book_edit_scope

PROPOSAL_ACTIVE = {"waiting", "queued", "running", "retry_wait"}
PROPOSAL_RETRYABLE = {"failed", "interrupted", "stale", "cancelled", "needs_attention"}
PROPOSAL_TERMINAL = PROPOSAL_RETRYABLE | {"succeeded"}


class ProposalQueueService:
    def __init__(self, queue):
        self.queue = queue
        self.db, self.intelligence = queue.db, queue.intelligence
        self.business, self.access = queue.business, queue.access

    def configuration_fingerprint(self):
        settings = effective_settings(self.intelligence.provider.settings, "journal")
        return fingerprint(
            {
                "url": settings.base_url,
                "model": settings.model,
                "format": settings.response_format,
                "prompt": JOURNAL_PROMPT_VERSION,
                "extraction": self.queue.configuration_fingerprint(),
            }
        )

    def policy_fingerprint(self, conn, event, book_id):
        accounts = [
            dict(row)
            for row in conn.execute(
                "SELECT code,name,active,version,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code",
                (book_id,),
            )
        ]
        templates = [
            dict(row)
            for row in conn.execute(
                "SELECT t.id,t.current_version,v.definition_fingerprint FROM journal_templates t JOIN journal_template_versions v ON v.template_id=t.id AND v.book_id=t.book_id AND v.version=t.current_version WHERE t.book_id=? AND t.active=1 ORDER BY t.id",
                (book_id,),
            )
        ]
        rules = [
            dict(row)
            for row in conn.execute(
                "SELECT r.id,r.current_version,r.state_version,v.definition_fingerprint FROM learning_rules r JOIN learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version WHERE r.book_id=? AND r.firm_id=? AND r.status='published' ORDER BY r.id",
                (book_id, event["firm_id"]),
            )
        ]
        return fingerprint(
            {
                "accounts": accounts,
                "templates": templates,
                "template_id": event["template_id"],
                "template_version": event["template_version"],
                "company_rules": rules,
            }
        )

    def _input(self, conn, actor, event_id, book_id, draft_id):
        book = self.access.book(conn, actor, book_id, "edit")
        self.intelligence.ledger._open(book)
        event = self.business._event(conn, actor, event_id)
        mapping = self.intelligence._mapping(conn, event)
        draft = conn.execute(
            "SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, book_id)
        ).fetchone()
        if (
            not mapping
            or mapping["book_id"] != book_id
            or mapping["draft_id"] != draft_id
            or not draft
            or draft["status"] != "draft"
            or event["status"] != "handed_off"
            or event["company_id"] != book["company_id"]
        ):
            raise AccountingError("业务事项未移交到该账套，或凭证已冻结", 409)
        return event, book, draft

    @staticmethod
    def _deadline(stamp):
        return (datetime.fromisoformat(stamp) + timedelta(minutes=30)).isoformat()

    def enqueue_in_transaction(self, conn, actor, event, book, draft):
        """Called after handoff mapping/status are written in the same transaction."""
        event, book, draft = self._input(conn, actor, event["id"], book["id"], draft["id"])
        config, policy = (
            self.configuration_fingerprint(),
            self.policy_fingerprint(conn, event, book["id"]),
        )
        key = fingerprint(
            {
                "book": book["id"],
                "draft": draft["id"],
                "fact": event["fact_version"],
                "draft_version": draft["version"],
                "config": config,
                "policy": policy,
            }
        )
        existing = conn.execute(
            "SELECT * FROM proposal_jobs WHERE firm_id=? AND idempotency_key=?",
            (actor.firm_id, key),
        ).fetchone()
        if existing:
            return self._json(conn, actor, existing)
        if conn.execute(
            "SELECT 1 FROM proposal_jobs WHERE draft_id=? AND status IN ('waiting','queued','running','retry_wait')",
            (draft["id"],),
        ).fetchone():
            raise AccountingError("该凭证已有后台分录任务，请读取当前进度", 409)
        job_id, stamp = uuid4().hex, self.queue.database_timestamp(conn)
        attention = self._attention(conn, actor, event, draft)
        state, code, detail = ("needs_attention", *attention) if attention else ("queued", "", "")
        conn.execute(
            "INSERT INTO proposal_jobs(id,firm_id,company_id,event_id,book_id,draft_id,expected_fact_version,expected_draft_version,configuration_fingerprint,policy_fingerprint,idempotency_key,execution_user_id,created_by,created_at,status,available_at,dependency_deadline,error_code,error_detail,updated_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                actor.firm_id,
                event["company_id"],
                event["id"],
                book["id"],
                draft["id"],
                event["fact_version"],
                draft["version"],
                config,
                policy,
                key,
                actor.user_id,
                actor.user_id,
                stamp,
                state,
                stamp,
                self._deadline(stamp),
                code,
                detail,
                stamp,
                stamp if attention else None,
            ),
        )
        if not attention:
            dependencies = self._dependencies(conn, actor, event, job_id, 1)
            if dependencies:
                # API creation includes the complete dependency state atomically.
                # PostgreSQL permits this transition only through a scoped helper.
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.proposal_wait_dependencies(?)", (job_id,))
                else:
                    conn.execute("UPDATE proposal_jobs SET status='waiting' WHERE id=?", (job_id,))
        self.business._audit(
            conn,
            actor,
            "queue.proposal.enqueue",
            job_id,
            {"event_id": event["id"], "book_id": book["id"], "draft_id": draft["id"]},
        )
        self.intelligence.capture_snapshot(conn, actor, event)
        return self._json(
            conn,
            actor,
            conn.execute("SELECT * FROM proposal_jobs WHERE id=?", (job_id,)).fetchone(),
        )

    def _attention(self, conn, actor, event, draft):
        if draft["last_manual_edit_version"]:
            return "manual_draft", "凭证已有人工修改，请会计生成并审阅候选建议"
        source = conn.execute(
            "SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], draft["book_id"])
        ).fetchone()
        if self.intelligence.ledger._source_state_issues(conn, source):
            return "fact_revision_pending", "事实修订或重复来源待人工处理，请先完成核验"
        if self.intelligence.ledger._case_guard().blocking_issues_in_transaction(
            conn, actor, source, draft
        ):
            return "case_pending", "异常或补件待办尚未核验并关闭，请先处理后由会计重试"
        if self.intelligence.ledger._linked_bank_issues(conn, source, actor):
            return "linked_bank_pending", "关联流水有异常或相似交易待核验，请先处理后由会计重试"
        if not self.intelligence._enabled(conn, actor, event):
            return "processing_disabled", "企业尚未启用模型处理，请管理员检查后由会计重试"
        if not conn.execute(
            "SELECT 1 FROM business_attachments WHERE event_id=?", (event["id"],)
        ).fetchone():
            return "evidence_missing", "缺少票据原件，自动建议暂停，请人工补件与复核"
        from ..intelligence.structured import is_structured

        attachments = conn.execute(
            "SELECT media_type FROM business_attachments WHERE event_id=?", (event["id"],)
        ).fetchall()
        if not self.intelligence.provider.settings.extraction_available and any(
            not is_structured(row["media_type"]) for row in attachments
        ):
            return "ocr_unavailable", "票据识别服务尚未配置，请配置后由会计重试"
        try:
            budget = self.queue.automation.budget_attention(conn, actor, event)
            if budget:
                return budget
            pending, _ = self._dependency_plan(conn, event, actor)
        except AccountingError as error:
            if error.status not in {403, 409}:
                raise
            return "policy_invalid", "企业 AI 外发政策或可信端点不可用，请管理员核验后重试"
        try:
            self.queue._capacity(conn, actor, event["company_id"], len(pending))
        except AccountingError:
            return (
                "queue_capacity",
                "企业票据任务积压已达上限，移交资料已保存，请处理积压后由会计重试",
            )
        return None

    def _dependency_plan(self, conn, event, actor):
        from ..intelligence.current_evidence import current_attachments

        attachments = current_attachments(
            self.db,
            conn,
            event,
            conn.execute(
                "SELECT * FROM business_attachments WHERE event_id=? ORDER BY id", (event["id"],)
            ).fetchall(),
        )
        pending, deps = [], []
        latest = self.intelligence._latest(conn, event)
        for attachment in attachments:
            current = latest.get(attachment["id"])
            digest = fingerprint(
                {
                    "attachment_id": attachment["id"],
                    "sha256": attachment["sha256"],
                    "config": self.intelligence._ocr_cache_key(
                        conn, actor, event, attachment["media_type"]
                    ),
                    "fact_version": event["fact_version"],
                    "force_refresh": False,
                }
            )
            if (
                current
                and current["status"] == "succeeded"
                and current["input_fingerprint"] == digest
            ):
                continue
            # A historical successful run alone is insufficient: the digest binds
            # current facts/hash/parser/model configuration. Cache misses require a
            # dependency, whose extraction can still reuse content without fees.
            active = conn.execute(
                "SELECT * FROM ocr_jobs WHERE event_id=? AND attachment_id=? AND status IN ('queued','running','retry_wait')",
                (event["id"], attachment["id"]),
            ).fetchone()
            if active:
                deps.append((active["id"], attachment["id"]))
            else:
                pending.append(attachment)
        return pending, deps

    def _dependencies(self, conn, actor, event, job_id, generation):
        pending, deps = self._dependency_plan(conn, event, actor)
        if pending:
            self.queue._capacity(conn, actor, event["company_id"], len(pending))
            batch_id, stamp = uuid4().hex, self.queue.database_timestamp(conn)
            conn.execute(
                "INSERT INTO ocr_batches(id,firm_id,company_id,created_by,idempotency_key,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    batch_id,
                    actor.firm_id,
                    event["company_id"],
                    actor.user_id,
                    f"proposal:{job_id}:{generation}",
                    fingerprint(
                        {
                            "event": event["id"],
                            "fact": event["fact_version"],
                            "attachments": [row["id"] for row in pending],
                        }
                    ),
                    stamp,
                ),
            )
            for attachment in pending:
                ocr_id = uuid4().hex
                conn.execute(
                    "INSERT INTO ocr_jobs(id,batch_id,firm_id,company_id,event_id,attachment_id,expected_version,expected_fact_version,attachment_sha256,configuration_fingerprint,execution_user_id,created_by,created_at,status,available_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)",
                    (
                        ocr_id,
                        batch_id,
                        actor.firm_id,
                        event["company_id"],
                        event["id"],
                        attachment["id"],
                        event["version"],
                        event["fact_version"],
                        attachment["sha256"],
                        self.queue.configuration_fingerprint(attachment["media_type"]),
                        actor.user_id,
                        actor.user_id,
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                deps.append((ocr_id, attachment["id"]))
        job = conn.execute("SELECT * FROM proposal_jobs WHERE id=?", (job_id,)).fetchone()
        for ocr_id, attachment_id in deps:
            conn.execute(
                "INSERT INTO proposal_dependencies(proposal_job_id,generation,ocr_job_id,attachment_id,event_id,firm_id,company_id,book_id,draft_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    generation,
                    ocr_id,
                    attachment_id,
                    event["id"],
                    actor.firm_id,
                    event["company_id"],
                    job["book_id"],
                    job["draft_id"],
                ),
            )
        return len(deps)

    def _job(self, conn, actor, job_id):
        job = conn.execute(
            "SELECT * FROM proposal_jobs WHERE id=? AND firm_id=?", (job_id, actor.firm_id)
        ).fetchone()
        if not job:
            raise AccountingError("分录任务不存在或无权访问", 404)
        self.access.book(conn, actor, job["book_id"], "edit")
        self.business._event(conn, actor, job["event_id"])
        return job

    def _json(self, conn, actor, job, details=False):
        value = {
            name: job[name]
            for name in (
                "id",
                "company_id",
                "event_id",
                "book_id",
                "draft_id",
                "status",
                "attempt_count",
                "created_at",
                "updated_at",
                "available_at",
                "error_code",
                "error_detail",
                "current_run_id",
                "expected_fact_version",
                "expected_draft_version",
                "result_template_id",
                "result_template_version",
                "result_draft_version",
                "result_rule_id",
                "result_rule_version",
            )
        }
        value["permissions"] = {
            "retry": job["status"] in PROPOSAL_RETRYABLE and job["attempt_count"] < 30,
            "cancel": job["status"] in PROPOSAL_ACTIVE | {"interrupted", "needs_attention"},
        }
        if details:
            value["attempts"] = [
                {
                    name: row[name]
                    for name in (
                        "attempt_no",
                        "status",
                        "phase",
                        "created_at",
                        "finished_at",
                        "error_code",
                        "error_detail",
                        "ai_run_id",
                    )
                }
                for row in conn.execute(
                    "SELECT * FROM proposal_attempts WHERE job_id=? ORDER BY attempt_no",
                    (job["id"],),
                )
            ]
        return value

    def get(self, actor, job_id):
        with self.db.transaction(actor=actor) as conn:
            return self._json(conn, actor, self._job(conn, actor, job_id), details=True)

    def list(self, actor, company_id=None, book_id=None, limit=50, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise AccountingError("分页范围无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            if company_id:
                self.access.company(conn, actor, company_id)
            if book_id:
                self.access.book(conn, actor, book_id, "edit")
            scope, params = book_edit_scope(actor)
            if company_id:
                scope += " AND j.company_id=?"
                params.append(company_id)
            if book_id:
                scope += " AND j.book_id=?"
                params.append(book_id)
            joins = (
                " FROM proposal_jobs j JOIN books b ON b.id=j.book_id JOIN companies c ON c.id=j.company_id JOIN drafts d ON d.id=j.draft_id WHERE "
                + scope
            )
            total = conn.execute("SELECT COUNT(*)" + joins, params).fetchone()[0]
            rows = conn.execute(
                "SELECT j.*,b.name AS book_name,c.name AS company_name,d.description AS draft_description"
                + joins
                + " ORDER BY j.created_at DESC,j.id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            return {
                "jobs": [
                    {
                        **self._json(conn, actor, row),
                        "company_name": row["company_name"],
                        "book_name": row["book_name"],
                        "draft_description": row["draft_description"],
                    }
                    for row in rows
                ],
                "total": total,
            }

    def retry(self, actor, job_id, key, reason="授权会计请求重试"):
        reason = self.intelligence._reason(reason)
        if len(reason.strip()) < 3:
            raise AccountingError("重试理由至少三个字符", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            job = self._job(conn, actor, job_id)
            receipt = conn.execute(
                "SELECT * FROM proposal_retries WHERE job_id=? AND request_key=?", (job_id, key)
            ).fetchone()
            if receipt:
                if receipt["reason"] != reason:
                    raise AccountingError("同一重试键不能变更重试理由", 409)
                return self._json(conn, actor, job, details=True)
            event, _, draft = self._input(
                conn, actor, job["event_id"], job["book_id"], job["draft_id"]
            )
            if job["status"] not in PROPOSAL_RETRYABLE or job["attempt_count"] >= 30:
                raise AccountingError("该分录任务不可重试，请读取当前进度", 409)
            attention = self._attention(conn, actor, event, draft)
            if attention:
                raise AccountingError(attention[1], 409)
            if conn.execute(
                "SELECT 1 FROM proposal_jobs WHERE draft_id=? AND id<>? AND status IN ('waiting','queued','running','retry_wait')",
                (draft["id"], job_id),
            ).fetchone():
                raise AccountingError("该凭证已有另一项未完成分录任务", 409)
            stamp = self.queue.database_timestamp(conn)
            conn.execute(
                "INSERT INTO proposal_retries(job_id,request_key,firm_id,company_id,event_id,book_id,draft_id,requested_by,created_at,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    key,
                    actor.firm_id,
                    job["company_id"],
                    event["id"],
                    job["book_id"],
                    draft["id"],
                    actor.user_id,
                    stamp,
                    reason,
                ),
            )
            conn.execute(
                "UPDATE proposal_jobs SET status='queued',expected_fact_version=?,expected_draft_version=?,configuration_fingerprint=?,policy_fingerprint=?,execution_user_id=?,generation=generation+1,max_attempts=?,available_at=?,dependency_deadline=?,current_run_id=NULL,result_template_id=NULL,result_template_version=NULL,result_draft_version=NULL,result_rule_id=NULL,result_rule_version=NULL,error_code='',error_detail='',finished_at=NULL,updated_at=? WHERE id=?",
                (
                    event["fact_version"],
                    draft["version"],
                    self.configuration_fingerprint(),
                    self.policy_fingerprint(conn, event, job["book_id"]),
                    actor.user_id,
                    min(30, job["attempt_count"] + 3),
                    stamp,
                    self._deadline(stamp),
                    stamp,
                    job_id,
                ),
            )
            dependencies = self._dependencies(conn, actor, event, job_id, job["generation"] + 1)
            if dependencies:
                if self.db.is_postgres:
                    conn.execute("SELECT accounting.proposal_wait_dependencies(?)", (job_id,))
                else:
                    conn.execute("UPDATE proposal_jobs SET status='waiting' WHERE id=?", (job_id,))
            self.business._audit(
                conn,
                actor,
                "queue.proposal.retry",
                job_id,
                {"previous_status": job["status"], "reason": reason},
            )
        return self.get(actor, job_id)

    def cancel(self, actor, job_id, reason):
        reason = self.intelligence._reason(reason)
        with self.db.transaction(write=True, actor=actor) as conn:
            job = self._job(conn, actor, job_id)
            if job["status"] == "cancelled":
                return self._json(conn, actor, job, details=True)
            if job["status"] not in PROPOSAL_ACTIVE | {"interrupted", "needs_attention"}:
                raise AccountingError("已完成的分录任务不能取消", 409)
            if self.db.is_postgres:
                function = (
                    "cancel_resumable_proposal"
                    if job["status"] == "needs_attention"
                    else "cancel_proposal_job"
                )
                if not conn.execute(
                    "SELECT accounting." + function + "(?,?)", (job_id, reason)
                ).fetchone()[0]:
                    raise AccountingError("分录任务状态已改变", 409)
            else:
                self.finish_sqlite(
                    conn,
                    job,
                    "cancelled",
                    "cancelled",
                    "授权会计已取消自动分录任务",
                    job["current_run_id"],
                )
                self.invalidate_run(conn, job)
            self.business._audit(conn, actor, "queue.proposal.cancel", job_id, {"reason": reason})
        return self.get(actor, job_id)

    def refresh_dependencies_sqlite(self, conn):
        stamp = self.queue.timestamp()
        jobs = conn.execute("SELECT * FROM proposal_jobs WHERE status='waiting'").fetchall()
        for job in jobs:
            states = [
                row[0]
                for row in conn.execute(
                    "SELECT o.status FROM proposal_dependencies d JOIN ocr_jobs o ON o.id=d.ocr_job_id WHERE d.proposal_job_id=? AND d.generation=?",
                    (job["id"], job["generation"]),
                )
            ]
            failed = any(
                state not in {"succeeded", "queued", "running", "retry_wait"} for state in states
            )
            if failed or job["dependency_deadline"] <= stamp:
                self.finish_sqlite(
                    conn,
                    job,
                    "needs_attention",
                    "ocr_dependency_failed" if failed else "ocr_dependency_timeout",
                    "票据识别未完成，请处理失败票据或积压后由会计重试",
                )
            elif all(state == "succeeded" for state in states):
                conn.execute(
                    "UPDATE proposal_jobs SET status='queued',updated_at=? WHERE id=?",
                    (stamp, job["id"]),
                )

    def finish_sqlite(self, conn, job, status, code="", detail="", run_id=None, delay=0):
        stamp = self.queue.timestamp()
        if job["status"] == "running":
            conn.execute(
                "UPDATE proposal_attempts SET phase='finished',status=?,ai_run_id=COALESCE(?,ai_run_id),error_code=?,error_detail=?,finished_at=? WHERE job_id=? AND lease_token=? AND status='running'",
                (
                    "failed" if status == "retry_wait" else status,
                    run_id,
                    code,
                    detail,
                    stamp,
                    job["id"],
                    job["lease_token"],
                ),
            )
        conn.execute(
            "UPDATE proposal_jobs SET status=?,current_run_id=COALESCE(?,current_run_id),error_code=?,error_detail=?,available_at=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=?,finished_at=? WHERE id=?",
            (
                status,
                run_id,
                code,
                detail,
                self.queue.timestamp(delay),
                stamp,
                None if status in PROPOSAL_ACTIVE else stamp,
                job["id"],
            ),
        )

    def invalidate_run(self, conn, job):
        if job["current_run_id"]:
            conn.execute(
                "UPDATE ai_runs SET status='stale',result_json=NULL,error_code='queue_interrupted',error_detail='分录任务已失效，旧响应不会发布',finished_at=? WHERE id=? AND event_id=? AND book_id=? AND draft_id=? AND created_by=? AND status='running'",
                (
                    self.queue.timestamp(),
                    job["current_run_id"],
                    job["event_id"],
                    job["book_id"],
                    job["draft_id"],
                    job["execution_user_id"],
                ),
            )

    def recover_sqlite(self, conn):
        jobs = conn.execute(
            "SELECT j.*,a.phase FROM proposal_jobs j JOIN proposal_attempts a ON a.job_id=j.id AND a.lease_token=j.lease_token WHERE j.status='running' AND j.lease_until<=?",
            (self.queue.timestamp(),),
        ).fetchall()
        for job in jobs:
            # Publishing a run and a task receipt is atomic; terminal run recovery
            # must never infer that a separate adoption occurred.
            status = (
                "interrupted"
                if job["phase"] == "sending"
                else "retry_wait"
                if job["attempt_count"] < job["max_attempts"]
                else "failed"
            )
            self.finish_sqlite(
                conn,
                job,
                status,
                "result_uncertain" if status == "interrupted" else "worker_restarted",
                "模型请求已发送但结果不确定，请核对后人工重试"
                if status == "interrupted"
                else "发送前任务中断，已恢复待处理",
            )
            self.invalidate_run(conn, job)
        self.refresh_dependencies_sqlite(conn)
        return len(jobs)

    def claim_sqlite(self, conn, job, worker_id, lease_seconds):
        token, stamp = uuid4().hex, self.queue.timestamp()
        conn.execute(
            "UPDATE proposal_jobs SET status='running',attempt_count=attempt_count+1,lease_token=?,lease_until=?,worker_id=?,current_run_id=NULL,error_code='',error_detail='',updated_at=? WHERE id=?",
            (token, self.queue.timestamp(lease_seconds), worker_id, stamp, job["id"]),
        )
        conn.execute(
            "INSERT INTO proposal_attempts(id,job_id,event_id,firm_id,company_id,book_id,draft_id,attempt_no,lease_token,execution_user_id,input_fact_version,input_draft_version,configuration_fingerprint,policy_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                job["id"],
                job["event_id"],
                job["firm_id"],
                job["company_id"],
                job["book_id"],
                job["draft_id"],
                job["attempt_count"] + 1,
                token,
                job["execution_user_id"],
                job["expected_fact_version"],
                job["expected_draft_version"],
                job["configuration_fingerprint"],
                job["policy_fingerprint"],
                stamp,
            ),
        )
        return {
            **dict(conn.execute("SELECT * FROM proposal_jobs WHERE id=?", (job["id"],)).fetchone()),
            "job_kind": "proposal",
        }
