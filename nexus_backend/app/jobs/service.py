"""Durable OCR commands and scope-filtered progress; no model calls in HTTP enqueue."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
from uuid import uuid4

from ..accounting.database import WRITE_LOCK
from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint
from ..intelligence.provider import EXTRACTION_PROMPT_VERSION, ProviderError

ACTIVE = {"queued", "running", "retry_wait"}
RETRYABLE = {"failed", "interrupted", "stale", "cancelled"}
TERMINAL = RETRYABLE | {"succeeded"}


class QueueService:
    def __init__(self, db, intelligence, clock=None):
        self.db, self.intelligence = db, intelligence
        self.business, self.access = intelligence.business, intelligence.access
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        intelligence.queue = self

    def timestamp(self, seconds=0):
        return (self.clock() + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat()

    def database_timestamp(self, conn):
        if self.db.is_postgres:
            # One time authority for API enqueue, dispatch, leases and retries.
            return conn.execute("SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')").fetchone()[0]
        return self.timestamp()

    def configuration_fingerprint(self):
        settings = self.intelligence.provider.settings
        return fingerprint({"url": settings.base_url, "model": settings.vision_model,
                            "format": settings.response_format, "prompt": EXTRACTION_PROMPT_VERSION})

    def _ready(self):
        if not self.intelligence.provider.settings.extraction_available:
            raise ProviderError("unavailable", "尚未配置票据识别服务，请先配置供应商")

    def _input(self, conn, actor, event_id, attachment_id, company_id=None, expected_version=None):
        event = self.business._event(conn, actor, event_id)
        self.intelligence._require(conn, actor, event, "process", enabled=True)
        if company_id and event["company_id"] != company_id:
            raise AccountingError("一批票据必须来自同一企业", 409)
        if expected_version is not None:
            self.business._version(event, expected_version)
        attachment = conn.execute("SELECT * FROM business_attachments WHERE id=? AND event_id=?", (attachment_id, event_id)).fetchone()
        if not attachment:
            raise AccountingError("附件不存在或不属于当前事项", 404)
        return event, attachment

    def enqueue(self, actor, request):
        pairs = [(item.event_id, item.attachment_id) for item in request.items]
        if len(pairs) != len(set(pairs)):
            raise AccountingError("同一批次不能重复选择附件", 422)
        digest = fingerprint({"company_id": request.company_id, "items": sorted((item.model_dump() for item in request.items), key=lambda value: (value["event_id"], value["attachment_id"]))})
        with self.db.transaction(write=True, actor=actor) as conn:
            inputs = [self._input(conn, actor, item.event_id, item.attachment_id, request.company_id, item.expected_version) for item in request.items]
            self._ready()
            existing = conn.execute("SELECT * FROM ocr_batches WHERE firm_id=? AND created_by=? AND idempotency_key=?", (actor.firm_id, actor.user_id, request.idempotency_key)).fetchone()
            if existing:
                if existing["request_fingerprint"] != digest:
                    raise AccountingError("批次幂等键已用于不同的票据集合", 409)
                return self._batch_json(conn, actor, existing)
            self._capacity(conn, actor, request.company_id, len(inputs))
            for event, attachment in inputs:
                if conn.execute("SELECT 1 FROM ocr_jobs WHERE event_id=? AND attachment_id=? AND status IN ('queued','running','retry_wait')", (event["id"], attachment["id"])).fetchone():
                    raise AccountingError("所选票据已有未完成任务，请查看进度，勿重复提交", 409)
            batch_id, stamp = uuid4().hex, self.database_timestamp(conn)
            conn.execute("INSERT INTO ocr_batches(id,firm_id,company_id,created_by,idempotency_key,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?)", (batch_id, actor.firm_id, request.company_id, actor.user_id, request.idempotency_key, digest, stamp))
            for event, attachment in inputs:
                conn.execute("INSERT INTO ocr_jobs(id,batch_id,firm_id,company_id,event_id,attachment_id,expected_version,attachment_sha256,configuration_fingerprint,execution_user_id,created_by,created_at,status,attempt_count,max_attempts,available_at,error_code,error_detail,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'queued',0,3,?,'','',?)", (uuid4().hex, batch_id, actor.firm_id, request.company_id, event["id"], attachment["id"], event["version"], attachment["sha256"], self.configuration_fingerprint(), actor.user_id, actor.user_id, stamp, stamp, stamp))
            self.business._audit(conn, actor, "queue.batch.enqueue", batch_id, {"company_id": request.company_id, "count": len(inputs)})
            for event, _ in inputs:
                self.intelligence.capture_snapshot(conn, actor, event)
            return self._batch_json(conn, actor, conn.execute("SELECT * FROM ocr_batches WHERE id=?", (batch_id,)).fetchone())

    def _capacity(self, conn, actor, company_id, additional):
        # RLS limits ordinary members to their visible work. The unique active
        # index remains the final concurrent duplicate guard.
        count = conn.execute("SELECT COUNT(*) FROM ocr_jobs WHERE firm_id=? AND company_id=? AND status IN ('queued','running','retry_wait')", (actor.firm_id, company_id)).fetchone()[0]
        if count + additional > 200:
            raise AccountingError("当前可见企业范围待处理票据已达上限，请处理后再提交", 409)

    def _visible_jobs(self, conn, actor, batch_id=None, event_id=None):
        sql, parameters = "SELECT j.* FROM ocr_jobs j WHERE j.firm_id=?", [actor.firm_id]
        if batch_id:
            sql += " AND j.batch_id=?"; parameters.append(batch_id)
        if event_id:
            sql += " AND j.event_id=?"; parameters.append(event_id)
        visible = []
        for job in conn.execute(sql + " ORDER BY j.created_at,j.id", parameters):
            try:
                event = self.business._event(conn, actor, job["event_id"])
            except AccountingError as error:
                if error.status in {403, 404}:
                    continue
                raise
            visible.append((job, event))
        return visible

    def _job_json(self, conn, actor, job, event):
        attachment = conn.execute("SELECT filename FROM business_attachments WHERE id=? AND event_id=?", (job["attachment_id"], event["id"])).fetchone()
        allowed = self.intelligence._permissions(conn, actor, event)["process"]
        enabled = self.intelligence._enabled(conn, actor, event)
        value = {key: job[key] for key in ("id", "batch_id", "company_id", "event_id", "attachment_id", "status", "attempt_count", "created_at", "updated_at", "error_code", "error_detail", "available_at")}
        value.update(filename=attachment["filename"] if attachment else "附件不可用", event_description=json.loads(event["payload"])["description"],
                     permissions={"retry": bool(allowed and enabled and self.intelligence.provider.settings.extraction_available and job["status"] in RETRYABLE and job["attempt_count"] < 30), "cancel": bool(allowed and job["status"] in ACTIVE | {"interrupted"})},
                     attempts=[{key: row[key] for key in ("attempt_no", "status", "phase", "created_at", "finished_at", "error_code", "error_detail")} for row in conn.execute("SELECT * FROM ocr_attempts WHERE job_id=? ORDER BY attempt_no", (job["id"],))])
        return value

    def event_jobs(self, conn, actor, event):
        return [self._job_json(conn, actor, job, value) for job, value in self._visible_jobs(conn, actor, event_id=event["id"])]

    def _batch_json(self, conn, actor, batch, with_jobs=True):
        visible = self._visible_jobs(conn, actor, batch_id=batch["id"])
        if not visible:
            raise AccountingError("任务批次不存在或无权访问", 404)
        counts = {state: sum(job["status"] == state for job, _ in visible) for state in ACTIVE | TERMINAL}
        completed = sum(counts[state] for state in TERMINAL)
        total = len(visible)
        pending = counts["queued"] + counts["retry_wait"]
        status = "running" if counts["running"] else "queued" if pending else "completed" if counts["succeeded"] == total else "cancelled" if counts["cancelled"] == total else "attention"
        company = conn.execute("SELECT name FROM companies WHERE id=?", (batch["company_id"],)).fetchone()
        return {"id": batch["id"], "company_id": batch["company_id"], "company_name": company["name"] if company else "客户企业", "created_at": batch["created_at"], "status": status,
                "progress": {"total": total, "completed": completed, "succeeded": counts["succeeded"], "failed": counts["failed"], "interrupted": counts["interrupted"], "stale": counts["stale"], "cancelled": counts["cancelled"], "pending": pending, "running": counts["running"], "percent": completed * 100 // total},
                "jobs": [self._job_json(conn, actor, job, event) for job, event in visible] if with_jobs else []}

    def get_batch(self, actor, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            batch = conn.execute("SELECT * FROM ocr_batches WHERE id=? AND firm_id=?", (batch_id, actor.firm_id)).fetchone()
            if not batch:
                raise AccountingError("任务批次不存在或无权访问", 404)
            return self._batch_json(conn, actor, batch)

    def list_batches(self, actor, company_id=None, event_id=None, limit=50, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise AccountingError("分页范围无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            if company_id:
                self.access.company(conn, actor, company_id)
            if event_id:
                self.business._event(conn, actor, event_id)
            rows = conn.execute("SELECT DISTINCT b.* FROM ocr_batches b JOIN ocr_jobs j ON j.batch_id=b.id WHERE b.firm_id=?" + (" AND b.company_id=?" if company_id else "") + (" AND j.event_id=?" if event_id else "") + " ORDER BY b.created_at DESC,b.id DESC", [actor.firm_id, *([company_id] if company_id else []), *([event_id] if event_id else [])])
            values = []
            for batch in rows:
                try:
                    values.append(self._batch_json(conn, actor, batch, with_jobs=False))
                except AccountingError as error:
                    if error.status != 404:
                        raise
            return {"batches": values[offset:offset + limit], "total": len(values)}

    def _job(self, conn, actor, job_id):
        job = conn.execute("SELECT * FROM ocr_jobs WHERE id=? AND firm_id=?", (job_id, actor.firm_id)).fetchone()
        if not job:
            raise AccountingError("票据任务不存在或无权访问", 404)
        return job, self.business._event(conn, actor, job["event_id"])

    def _retry(self, conn, actor, job, key):
        event, attachment = self._input(conn, actor, job["event_id"], job["attachment_id"])
        self._ready()
        if conn.execute("SELECT 1 FROM ocr_retries WHERE job_id=? AND request_key=?", (job["id"], key)).fetchone():
            return
        if job["status"] not in RETRYABLE or job["attempt_count"] >= 30:
            raise AccountingError("该任务不可重试；请读取当前进度或检查尝试次数", 409)
        self._capacity(conn, actor, job["company_id"], 1)
        if conn.execute("SELECT 1 FROM ocr_jobs WHERE event_id=? AND attachment_id=? AND id<>? AND status IN ('queued','running','retry_wait')", (job["event_id"], job["attachment_id"], job["id"])).fetchone():
            raise AccountingError("该票据已有另一项未完成任务", 409)
        stamp = self.database_timestamp(conn)
        conn.execute("INSERT INTO ocr_retries(job_id,request_key,firm_id,company_id,event_id,requested_by,created_at) VALUES(?,?,?,?,?,?,?)", (job["id"], key, actor.firm_id, job["company_id"], job["event_id"], actor.user_id, stamp))
        changed = conn.execute("UPDATE ocr_jobs SET status='queued',expected_version=?,attachment_sha256=?,configuration_fingerprint=?,execution_user_id=?,max_attempts=?,available_at=?,current_run_id=NULL,error_code='',error_detail='',finished_at=NULL,updated_at=? WHERE id=? AND status=?", (event["version"], attachment["sha256"], self.configuration_fingerprint(), actor.user_id, min(job["attempt_count"] + 3, 30), stamp, stamp, job["id"], job["status"]))
        if changed.rowcount != 1:
            raise AccountingError("任务状态已改变，请刷新", 409)
        self.business._audit(conn, actor, "queue.job.retry", job["id"], {"previous_status": job["status"], "expected_version": event["version"]})
        self.intelligence.capture_snapshot(conn, actor, event)

    def retry_job(self, actor, job_id, key):
        with self.db.transaction(write=True, actor=actor) as conn:
            job, _ = self._job(conn, actor, job_id)
            self._retry(conn, actor, job, key)
            batch_id = job["batch_id"]
        return self.get_batch(actor, batch_id)

    def retry_batch(self, actor, batch_id, key):
        with self.db.transaction(write=True, actor=actor) as conn:
            batch = conn.execute("SELECT * FROM ocr_batches WHERE id=? AND firm_id=?", (batch_id, actor.firm_id)).fetchone()
            if not batch:
                raise AccountingError("任务批次不存在或无权访问", 404)
            visible = self._visible_jobs(conn, actor, batch_id=batch_id)
            if not visible:
                raise AccountingError("任务批次不存在或无权访问", 404)
            if conn.execute("SELECT 1 FROM ocr_batch_retries WHERE batch_id=? AND requested_by=? AND request_key=?", (batch_id, actor.user_id, key)).fetchone():
                return self._batch_json(conn, actor, batch)
            selected = [job for job, _ in visible if job["status"] in RETRYABLE]
            if not selected:
                raise AccountingError("当前批次没有可重试的失败任务", 409)
            for job in selected:
                self._retry(conn, actor, job, key)
            conn.execute("INSERT INTO ocr_batch_retries(batch_id,request_key,firm_id,company_id,requested_by,created_at) VALUES(?,?,?,?,?,?)", (batch_id, key, actor.firm_id, batch["company_id"], actor.user_id, self.database_timestamp(conn)))
        return self.get_batch(actor, batch_id)

    def cancel_job(self, actor, job_id, reason):
        reason = self.intelligence._reason(reason)
        with self.db.transaction(write=True, actor=actor) as conn:
            job, event = self._job(conn, actor, job_id)
            self.intelligence._require(conn, actor, event, "process")
            if job["status"] == "cancelled":
                return self._batch_json(conn, actor, conn.execute("SELECT * FROM ocr_batches WHERE id=?", (job["batch_id"],)).fetchone())
            if job["status"] not in ACTIVE | {"interrupted"}:
                raise AccountingError("已完成的任务不能取消", 409)
            if self.db.is_postgres:
                if not conn.execute("SELECT accounting.cancel_ocr_job(?,?)", (job_id, reason)).fetchone()[0]:
                    raise AccountingError("任务状态已改变或当前不可取消", 409)
            else:
                self._finish_sqlite(conn, job, "cancelled", "cancelled", "任务已由授权人员取消", job["current_run_id"])
                self._invalidate_run(conn, job)
            self.business._audit(conn, actor, "queue.job.cancel", job_id, {"reason": reason})
            self.intelligence.capture_snapshot(conn, actor, event)
        return self.get_batch(actor, job["batch_id"])

    @contextmanager
    def worker_transaction(self):
        conn = self.db.connect()
        try:
            conn.execute("BEGIN" if self.db.is_postgres else "BEGIN IMMEDIATE")
            if self.db.is_postgres:
                conn.execute("SET LOCAL ROLE accounting_worker")
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                conn.execute("SELECT pg_advisory_xact_lock(?)", (WRITE_LOCK,))
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim(self, worker_id, lease_seconds=180, concurrency=2):
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                row = conn.execute("SELECT * FROM accounting.claim_ocr_job(?,?,?)", (worker_id, lease_seconds, concurrency)).fetchone()
                return dict(row) if row else None
            stamp = self.timestamp()
            if conn.execute("SELECT COUNT(*) FROM ocr_jobs WHERE status='running'", ()).fetchone()[0] >= concurrency:
                return None
            job = conn.execute("SELECT j.* FROM ocr_jobs j LEFT JOIN ocr_company_schedule s ON s.firm_id=j.firm_id AND s.company_id=j.company_id WHERE j.status IN ('queued','retry_wait') AND j.available_at<=? AND j.attempt_count<j.max_attempts AND NOT EXISTS(SELECT 1 FROM ocr_jobs active WHERE active.firm_id=j.firm_id AND active.company_id=j.company_id AND active.status='running') ORDER BY COALESCE(s.last_dispatch_seq,0),j.created_at,j.id LIMIT 1", (stamp,)).fetchone()
            if not job:
                return None
            token = uuid4().hex
            conn.execute("UPDATE ocr_jobs SET status='running',attempt_count=attempt_count+1,lease_token=?,lease_until=?,worker_id=?,current_run_id=NULL,updated_at=? WHERE id=?", (token, self.timestamp(lease_seconds), worker_id, stamp, job["id"]))
            sequence = conn.execute("SELECT COALESCE(MAX(last_dispatch_seq),0)+1 FROM ocr_company_schedule").fetchone()[0]
            conn.execute("INSERT INTO ocr_company_schedule(firm_id,company_id,last_dispatch_seq) VALUES(?,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET last_dispatch_seq=excluded.last_dispatch_seq", (job["firm_id"], job["company_id"], sequence))
            conn.execute("INSERT INTO ocr_attempts(id,job_id,firm_id,company_id,event_id,attachment_id,attempt_no,lease_token,execution_user_id,input_version,attachment_sha256,configuration_fingerprint,phase,status,error_code,error_detail,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'claimed','running','','',?)", (uuid4().hex, job["id"], job["firm_id"], job["company_id"], job["event_id"], job["attachment_id"], job["attempt_count"] + 1, token, job["execution_user_id"], job["expected_version"], job["attachment_sha256"], job["configuration_fingerprint"], stamp))
            return dict(conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (job["id"],)).fetchone())

    def _invalidate_run(self, conn, job):
        if job["current_run_id"]:
            conn.execute("UPDATE ai_runs SET status='stale',result_json=NULL,error_code='queue_interrupted',error_detail='任务已失效，旧响应不再发布',finished_at=? WHERE id=? AND event_id=? AND attachment_id=? AND created_by=? AND status='running'", (self.timestamp(), job["current_run_id"], job["event_id"], job["attachment_id"], job["execution_user_id"]))

    def _finish_sqlite(self, conn, job, status, code="", detail="", run_id=None, delay=0):
        stamp = self.timestamp()
        attempt_status = "failed" if status == "retry_wait" else status
        conn.execute("UPDATE ocr_attempts SET phase='finished',status=?,ai_run_id=COALESCE(?,ai_run_id),error_code=?,error_detail=?,finished_at=? WHERE job_id=? AND attempt_no=? AND status='running'", (attempt_status, run_id, code, detail, stamp, job["id"], job["attempt_count"]))
        conn.execute("UPDATE ocr_jobs SET status=?,current_run_id=COALESCE(?,current_run_id),error_code=?,error_detail=?,available_at=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=?,finished_at=? WHERE id=?", (status, run_id, code, detail, self.timestamp(delay), stamp, None if status in ACTIVE else stamp, job["id"]))

    def recover(self):
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                return conn.execute("SELECT accounting.recover_ocr_jobs()").fetchone()[0]
            jobs = conn.execute("SELECT * FROM ocr_jobs WHERE status='running' AND lease_until<=?", (self.timestamp(),)).fetchall()
            for job in jobs:
                run = conn.execute("SELECT * FROM ai_runs WHERE id=?", (job["current_run_id"],)).fetchone() if job["current_run_id"] else None
                attempt = conn.execute("SELECT * FROM ocr_attempts WHERE job_id=? AND attempt_no=?", (job["id"], job["attempt_count"])).fetchone()
                if run and run["status"] in {"succeeded", "failed", "stale"}:
                    self._finish_sqlite(conn, job, run["status"], run["error_code"], run["error_detail"], run["id"])
                elif attempt and attempt["phase"] == "claimed":
                    state = "retry_wait" if job["attempt_count"] < job["max_attempts"] else "failed"
                    self._finish_sqlite(conn, job, state, "worker_restarted", "发送前任务中断，已恢复待处理" if state == "retry_wait" else "恢复次数已达上限，请人工检查")
                    self._invalidate_run(conn, job)
                else:
                    self._finish_sqlite(conn, job, "interrupted", "result_uncertain", "模型请求已发送但结果不确定，请核对后决定是否重试")
                    self._invalidate_run(conn, job)
            return len(jobs)
