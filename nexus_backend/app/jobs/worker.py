"""Small durable OCR worker. PostgreSQL requires a separately provisioned login."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import logging
import json
import os
import threading
import time
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from ..accounting.domain import Actor, AccountingError
from ..intelligence.requests import OCRRequest, ProposalRequest
from .service import QueueService
from ..concurrency.settings import SchedulingSettings

logger = logging.getLogger(__name__)


def verify_worker_login(db):
    """PostgreSQL workers must log in as the separately provisioned restricted role."""
    if not db.is_postgres:
        return
    with closing(db.connect()) as conn:
        permitted = conn.execute(
            "SELECT pg_has_role(session_user,'accounting_worker','MEMBER')"
        ).fetchone()[0]
        if not permitted:
            raise RuntimeError(
                "后台处理须使用独立 accounting_worker 登录，API运行账号不能领取跨企业任务"
            )
        principal = conn.execute(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit "
            "FROM pg_roles WHERE rolname=session_user"
        ).fetchone()
        parents = [
            row[0]
            for row in conn.execute(
                "SELECT parent.rolname FROM pg_auth_members m "
                "JOIN pg_roles parent ON parent.oid=m.roleid "
                "JOIN pg_roles child ON child.oid=m.member WHERE child.rolname=session_user"
            )
        ]
        if any(
            principal[key]
            for key in (
                "rolsuper",
                "rolbypassrls",
                "rolcreatedb",
                "rolcreaterole",
                "rolinherit",
            )
        ) or set(parents) != {"accounting_app", "accounting_worker"}:
            raise RuntimeError(
                "worker登录必须为独立受限账号，仅授予accounting_app与accounting_worker且禁止继承或角色管理权限"
            )


class QueueExecution:
    def __init__(self, queue, job):
        self.queue, self.job = queue, job
        self.sent = False
        self.started_at = time.perf_counter()
        self.model_started = None
        self.model_elapsed_ms = None

    def after_model(self):
        if self.model_started is not None and self.model_elapsed_ms is None:
            self.model_elapsed_ms = (time.perf_counter() - self.model_started) * 1000

    def before_model(self):
        self.model_started = time.perf_counter()

    def retry_is_safe(self, error):
        """Retry hints never prove that a dispatched request was uncharged."""
        return (
            (not self.sent or getattr(error, "llm_attempted", None) is False)
            and getattr(error, "usage", None) is None
            and (getattr(error, "retryable", False) or error.code == "busy")
        )

    def record_timings(self, conn, cache_hit=False):
        model_ms = self.model_elapsed_ms or 0
        if self.model_elapsed_ms is not None:
            self.queue.automation.record_metric(conn, self.job, "model_call", model_ms)
        self.queue.automation.record_metric(
            conn,
            self.job,
            "validation_persistence",
            (time.perf_counter() - self.started_at) * 1000 - model_ms,
        )
        if cache_hit:
            self.queue.automation.record_metric(conn, self.job, "ocr_cache_reuse", 0)

    def _function(self, conn, name, parameters):
        if not self.queue.db.is_postgres:
            return None
        conn.execute("SET LOCAL ROLE accounting_worker")
        try:
            return conn.execute(
                "SELECT accounting." + name + "(" + ",".join("?" for _ in parameters) + ")",
                parameters,
            ).fetchone()[0]
        finally:
            conn.execute("SET LOCAL ROLE accounting_app")

    def check(self, conn):
        if self.queue.db.is_postgres:
            valid = self._function(
                conn, "worker_check_ocr_lease", (self.job["id"], self.job["lease_token"])
            )
        else:
            valid = conn.execute(
                "SELECT 1 FROM ocr_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                (self.job["id"], self.job["lease_token"], self.queue.timestamp()),
            ).fetchone()
        if not valid:
            raise AccountingError("后台任务租约已失效，旧响应不会发布", 409)

    def inputs(self, event, attachment):
        version_changed = (
            event["fact_version"] != self.job["expected_fact_version"]
            if self.job.get("expected_fact_version") is not None
            else event["version"] != self.job["expected_version"]
        )
        if (
            event["id"] != self.job["event_id"]
            or version_changed
            or attachment["id"] != self.job["attachment_id"]
            or attachment["sha256"] != self.job["attachment_sha256"]
            or self.queue.configuration_fingerprint(attachment["media_type"])
            != self.job["configuration_fingerprint"]
        ):
            raise AccountingError("后台任务资料或模型配置已改变，请按当前资料重新处理", 409)

    def bind(self, conn, run):
        self.check(conn)
        if self.queue.db.is_postgres:
            valid = self._function(
                conn, "worker_bind_ocr_run", (self.job["id"], self.job["lease_token"], run["id"])
            )
            if not valid:
                raise AccountingError("后台任务未能绑定当前识别记录", 409)
        else:
            conn.execute(
                "UPDATE ocr_jobs SET current_run_id=?,updated_at=? WHERE id=? AND lease_token=?",
                (run["id"], self.queue.timestamp(), self.job["id"], self.job["lease_token"]),
            )
            conn.execute(
                "UPDATE ocr_attempts SET ai_run_id=? WHERE job_id=? AND lease_token=? AND status='running'",
                (run["id"], self.job["id"], self.job["lease_token"]),
            )
        self.job["current_run_id"] = run["id"]

    def before_send(self, conn):
        self.check(conn)
        if self.queue.db.is_postgres:
            valid = self._function(
                conn, "worker_mark_ocr_sending", (self.job["id"], self.job["lease_token"])
            )
            if not valid:
                raise AccountingError("任务已失效，未发送票据", 409)
        else:
            conn.execute(
                "UPDATE ocr_attempts SET phase='sending' WHERE job_id=? AND lease_token=? AND status='running'",
                (self.job["id"], self.job["lease_token"]),
            )
        self.sent = True
        self.model_started = time.perf_counter()

    def finish(self, conn, run, error=None):
        self.check(conn)
        self.record_timings(conn, cache_hit=bool(run["cache_hit"]))
        status, delay = run["status"], 0
        if status == "failed" and error:
            if self.retry_is_safe(error) and self.job[
                "attempt_count"
            ] < self.job["max_attempts"]:
                attempt_in_round = max(
                    1, self.job["attempt_count"] - (self.job["max_attempts"] - 3)
                )
                status, delay = "retry_wait", 30 * (2 ** min(attempt_in_round - 1, 1))
            elif (
                self.sent
                and getattr(error, "llm_attempted", None) is not False
                and error.code in {"timeout", "network", "network_error", "transport", "response_unknown", "processing_failed"}
            ):
                status = "interrupted"
        code, detail = run["error_code"], run["error_detail"]
        if status == "interrupted":
            detail = "模型请求结果不确定，请核对后决定是否重试；重试可能再次调用模型"
        if self.queue.db.is_postgres:
            valid = self._function(
                conn,
                "worker_finish_ocr_job",
                (self.job["id"], self.job["lease_token"], status, code, detail, run["id"], delay),
            )
            if not valid:
                raise AccountingError("后台任务已失效，结果未发布", 409)
        else:
            self.queue._finish_sqlite(conn, self.job, status, code, detail, run["id"], delay)


class ProposalExecution(QueueExecution):
    def check(self, conn):
        if self.queue.db.is_postgres:
            valid = self._function(
                conn, "worker_check_proposal_lease", (self.job["id"], self.job["lease_token"])
            )
        else:
            valid = conn.execute(
                "SELECT 1 FROM proposal_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                (self.job["id"], self.job["lease_token"], self.queue.timestamp()),
            ).fetchone()
        if not valid:
            raise AccountingError("后台分录任务租约已失效，旧响应不会发布", 409)

    def inputs(self, event, draft):
        if (
            event["id"] != self.job["event_id"]
            or event["fact_version"] != self.job["expected_fact_version"]
            or draft["id"] != self.job["draft_id"]
            or draft["book_id"] != self.job["book_id"]
            or draft["version"] != self.job["expected_draft_version"]
            or draft["last_manual_edit_version"]
            or self.queue.proposals.configuration_fingerprint()
            != self.job["configuration_fingerprint"]
        ):
            raise AccountingError("后台分录任务的业务事实、凭证版本或模型配置已改变", 409)

    def policy(self, conn, event):
        if (
            self.queue.proposals.policy_fingerprint(conn, event, self.job["book_id"])
            != self.job["policy_fingerprint"]
        ):
            raise AccountingError("科目或记账模板已改变，请会计按当前配置重新处理", 409)

    def bind(self, conn, run):
        self.check(conn)
        event = conn.execute(
            "SELECT * FROM business_events WHERE id=? AND firm_id=?",
            (self.job["event_id"], self.job["firm_id"]),
        ).fetchone()
        if not event:
            raise AccountingError("业务事项授权已改变，候选未绑定", 409)
        self.policy(conn, event)
        if self.queue.db.is_postgres:
            if not self._function(
                conn,
                "worker_bind_proposal_run",
                (self.job["id"], self.job["lease_token"], run["id"]),
            ):
                raise AccountingError("后台分录任务未能绑定当前模型记录", 409)
        else:
            conn.execute(
                "UPDATE proposal_jobs SET current_run_id=?,updated_at=? WHERE id=? AND lease_token=?",
                (run["id"], self.queue.timestamp(), self.job["id"], self.job["lease_token"]),
            )
            conn.execute(
                "UPDATE proposal_attempts SET ai_run_id=? WHERE job_id=? AND lease_token=? AND status='running'",
                (run["id"], self.job["id"], self.job["lease_token"]),
            )
        self.job["current_run_id"] = run["id"]

    def before_send(self, conn):
        self.check(conn)
        if self.queue.db.is_postgres:
            if not self._function(
                conn, "worker_mark_proposal_sending", (self.job["id"], self.job["lease_token"])
            ):
                raise AccountingError("分录任务已失效，未发送业务资料", 409)
        else:
            conn.execute(
                "UPDATE proposal_attempts SET phase='sending' WHERE job_id=? AND lease_token=? AND status='running'",
                (self.job["id"], self.job["lease_token"]),
            )
        self.sent = True
        self.model_started = time.perf_counter()

    def finish(self, conn, run, error=None):
        self.check(conn)
        status, code, detail, delay = run["status"], run["error_code"], run["error_detail"], 0
        if status == "succeeded":
            # Every adoption check is inside this same transaction. A savepoint
            # also protects the immutable candidate from a later mutation error.
            conn.execute("SAVEPOINT automatic_proposal_adoption")
            try:
                from ..accounting.domain import Actor

                actor = Actor(self.job["execution_user_id"], self.job["firm_id"])
                self.queue.intelligence._apply_in_transaction(
                    conn, actor, run["id"], self.job["expected_draft_version"], automatic=True
                )
                conn.execute("RELEASE SAVEPOINT automatic_proposal_adoption")
            except AccountingError as caught:
                conn.execute("ROLLBACK TO SAVEPOINT automatic_proposal_adoption")
                conn.execute("RELEASE SAVEPOINT automatic_proposal_adoption")
                status, code, detail = (
                    "needs_attention",
                    "adoption_blocked",
                    "候选建议已生成，存在需人工处理的异常或凭证已修改，请会计审阅",
                )
        elif status == "failed" and error:
            if self.retry_is_safe(error) and self.job[
                "attempt_count"
            ] < self.job["max_attempts"]:
                attempt_in_round = max(
                    1, self.job["attempt_count"] - (self.job["max_attempts"] - 3)
                )
                status, delay = "retry_wait", 30 * (2 ** min(attempt_in_round - 1, 1))
            elif (
                self.sent
                and getattr(error, "llm_attempted", None) is not False
                and error.code in {"timeout", "network", "network_error", "transport", "response_unknown", "processing_failed"}
            ):
                status, detail = (
                    "interrupted",
                    "模型请求结果不确定，请核对后决定是否重试；重试可能再次调用模型",
                )
        if self.queue.db.is_postgres:
            self.record_timings(conn)
            valid = self._function(
                conn,
                "worker_finish_proposal_job",
                (self.job["id"], self.job["lease_token"], status, code, detail, run["id"], delay),
            )
            if not valid:
                raise AccountingError("后台分录任务已失效，候选和草稿不会发布", 409)
        else:
            self.record_timings(conn)
            self.queue.proposals.finish_sqlite(
                conn, self.job, status, code, detail, run["id"], delay
            )

    def finish_rule(self, conn, generated, applied):
        self.check(conn)
        if self.queue.db.is_postgres:
            self.record_timings(conn)
            company_rule = generated.get("rule_id")
            function = (
                "worker_finish_proposal_company_rule"
                if company_rule
                else "worker_finish_proposal_rule"
            )
            valid = self._function(
                conn,
                function,
                (
                    self.job["id"],
                    self.job["lease_token"],
                    company_rule or generated["template_id"],
                    generated.get("rule_version") or generated["template_version"],
                    applied["version"],
                ),
            )
            if not valid:
                raise AccountingError("模板分录任务已失效，草稿不会发布", 409)
        else:
            if generated.get("rule_id"):
                conn.execute(
                    "UPDATE proposal_jobs SET result_rule_id=?,result_rule_version=?,result_draft_version=? WHERE id=?",
                    (
                        generated["rule_id"],
                        generated["rule_version"],
                        applied["version"],
                        self.job["id"],
                    ),
                )
            else:
                statement = (
                    "UPDATE proposal_jobs SET result_template_id=?,result_template_version=?,result_draft_version=? WHERE id=?"
                )
                values = (
                    generated["template_id"],
                    generated["template_version"],
                    applied["version"],
                    self.job["id"],
                )
                conn.execute(statement, values)
            self.record_timings(conn)
            self.queue.proposals.finish_sqlite(conn, self.job, "succeeded")


class QueueWorker:
    def __init__(
        self, queue_service, worker_id=None, concurrency=2, lease_seconds=180, poll_seconds=1
    ):
        if (
            type(concurrency) is not int
            or concurrency not in {1, 2}
            or not 30 <= lease_seconds <= 600
            or not 0.2 <= poll_seconds <= 10
        ):
            raise ValueError("队列并发须为1或2，租约30–600秒，轮询0.2–10秒")
        self.queue = queue_service
        self.worker_id = worker_id or "worker-" + uuid4().hex
        self.concurrency, self.lease_seconds, self.poll_seconds = (
            concurrency,
            lease_seconds,
            poll_seconds,
        )
        self.heartbeat_seconds = 10.0
        self._stop = threading.Event()
        self._thread = None
        self._operations = None
        verify_worker_login(self.queue.db)

    def recover(self):
        return self.queue.recover()

    def renew(self, job):
        proposal = job.get("job_kind") == "proposal"
        with self.queue.worker_transaction() as conn:
            if self.queue.db.is_postgres:
                function = "worker_renew_proposal_lease" if proposal else "worker_renew_ocr_lease"
                return conn.execute(
                    "SELECT accounting." + function + "(?,?,?)",
                    (job["id"], job["lease_token"], self.lease_seconds),
                ).fetchone()[0]
            table = "proposal_jobs" if proposal else "ocr_jobs"
            return (
                conn.execute(
                    "UPDATE "
                    + table
                    + " SET lease_until=?,updated_at=? WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                    (
                        self.queue.timestamp(self.lease_seconds),
                        self.queue.timestamp(),
                        job["id"],
                        job["lease_token"],
                        self.queue.timestamp(),
                    ),
                ).rowcount
                == 1
            )

    def _failure(self, job, status, code, detail):
        proposal = job.get("job_kind") == "proposal"
        with self.queue.worker_transaction() as conn:
            if self.queue.db.is_postgres:
                function = "worker_finish_proposal_job" if proposal else "worker_finish_ocr_job"
                return conn.execute(
                    "SELECT accounting." + function + "(?,?,?,?,?,?,?)",
                    (job["id"], job["lease_token"], status, code, detail, job["current_run_id"], 0),
                ).fetchone()[0]
            table = "proposal_jobs" if proposal else "ocr_jobs"
            current = conn.execute(
                "SELECT * FROM "
                + table
                + " WHERE id=? AND status='running' AND lease_token=? AND lease_until>?",
                (job["id"], job["lease_token"], self.queue.timestamp()),
            ).fetchone()
            if not current:
                return False
            if proposal:
                self.queue.proposals.finish_sqlite(
                    conn, current, status, code, detail, current["current_run_id"]
                )
                self.queue.proposals.invalidate_run(conn, current)
            else:
                self.queue._finish_sqlite(
                    conn, current, status, code, detail, current["current_run_id"]
                )
                self.queue._invalidate_run(conn, current)
            return True

    def process(self, job):
        actor = Actor(job["execution_user_id"], job["firm_id"])
        proposal = job.get("job_kind") == "proposal"
        execution = (
            ProposalExecution(self.queue, job) if proposal else QueueExecution(self.queue, job)
        )
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(max(1, min(self.lease_seconds / 3, 15))):
                try:
                    if not self.renew(job):
                        return
                except Exception:
                    # No credential, provider body or financial evidence in logs.
                    logger.warning("后台任务续期暂不可用，发布结果仍需验证租约")

        heart = threading.Thread(target=heartbeat, name="ocr-lease-heartbeat", daemon=True)
        heart.start()
        try:
            if proposal:
                return self.process_proposal(actor, job, execution)
            with self.queue.db.transaction(actor=actor) as conn:
                event, attachment = self.queue._input(
                    conn,
                    actor,
                    job["event_id"],
                    job["attachment_id"],
                    job["company_id"],
                    job["expected_version"] if job.get("expected_fact_version") is None else None,
                )
                execution.check(conn)
                execution.inputs(event, attachment)
                self.queue._ready(attachment)
            return self.queue.intelligence.extract(
                actor,
                job["event_id"],
                OCRRequest(
                    attachment_id=job["attachment_id"],
                    expected_version=job["expected_version"],
                    expected_fact_version=job.get("expected_fact_version"),
                    idempotency_key=f"queue:{job['id']}:{job['attempt_count']}",
                ),
                execution=execution,
            )
        except AccountingError as error:
            from ..operations.meter import BudgetError

            if isinstance(error, BudgetError):
                self._failure(job, "failed", error.code, error.detail)
            else:
                self._failure(
                    job,
                    "stale",
                    "context_changed",
                    "权限、期间或资料已改变，任务停止，请由获授权人员重新处理",
                )
        except Exception as error:
            # Keep supplier content and exception messages out of logs; the
            # class name is enough to separate code/resource failures from
            # provider and authorization failures during incident triage.
            logger.warning(
                "后台任务异常类型=%s，按持久租约恢复",
                type(error).__name__,
            )
            self._failure(
                job,
                "interrupted" if execution.sent else "failed",
                "worker_failure",
                "任务执行中断，请读取当前结果并由人员决定是否重试",
            )
        finally:
            heartbeat_stop.set()
            heart.join(timeout=2)

    def process_proposal(self, actor, job, execution):
        from ..accounting.catalog import CatalogService
        from ..accounting.models import cents_to_money, money_to_cents

        # No provider call under a transaction. A matched company rule can finish
        # completely here, with the lease, draft and receipt committed together.
        with self.queue.db.transaction(write=True, actor=actor) as conn:
            event, _, draft = self.queue.proposals._input(
                conn, actor, job["event_id"], job["book_id"], job["draft_id"]
            )
            execution.check(conn)
            execution.inputs(event, draft)
            execution.policy(conn, event)
            resume_policy = conn.execute(
                "SELECT enabled FROM queue_automation_policies WHERE firm_id=? AND company_id=?",
                (job["firm_id"], job["company_id"]),
            ).fetchone()
            if resume_policy and resume_policy["enabled"]:
                attention = self.queue.proposals._attention(conn, actor, event, draft)
                if attention:
                    self._finish_proposal_attention(conn, job, execution, *attention)
                    return None
            self.queue.intelligence._require(conn, actor, event, "generate_proposal", enabled=True)
            assessment = self.queue.intelligence._assessment(conn, actor, event)
            blocking = any(
                item["severity"] == "blocking" and not item["resolved"]
                for item in assessment["findings"]
            )
            if blocking or not assessment["references"]:
                self._finish_proposal_attention(
                    conn,
                    job,
                    execution,
                    "blocking_findings",
                    "票据或流水核对异常尚未处理，请会计先核对并补件",
                )
                return None
            payload = json.loads(event["payload"])
            documents = []
            for reference in assessment["references"]:
                run = conn.execute(
                    "SELECT result_json FROM ai_runs WHERE id=? AND status='succeeded'",
                    (reference["extraction_id"],),
                ).fetchone()
                if run:
                    documents.extend(json.loads(run["result_json"])["documents"])
            recognized = {}
            if documents and all(
                all(
                    document.get(field) is not None
                    for field in ("gross_amount", "net_amount", "tax_amount")
                )
                for document in documents
            ):
                gross = sum(money_to_cents(document["gross_amount"]) for document in documents)
                net, tax = (
                    sum(money_to_cents(document[field]) for document in documents)
                    for field in ("net_amount", "tax_amount")
                )
                if gross == money_to_cents(payload["amount"]) and net + tax == gross:
                    recognized = {"net": cents_to_money(net), "tax": cents_to_money(tax)}
            conn.execute("SAVEPOINT automatic_template_adoption")
            try:
                context = {
                    "department_id": event["department_id"],
                    "applicant_id": event["applicant_id"],
                }
                generated = (
                    self.queue.intelligence.ledger._learning_guard().rule_generate_in_transaction(
                        conn,
                        actor,
                        job["book_id"],
                        event["template_id"],
                        payload,
                        money_to_cents(payload["amount"]),
                        context,
                        recognized,
                    )
                )
                if generated is None:
                    generated = CatalogService(self.queue.db).generate_in_transaction(
                        conn,
                        actor,
                        job["book_id"],
                        event["template_id"],
                        payload,
                        money_to_cents(payload["amount"]),
                        context,
                        recognized,
                    )
                if generated:
                    if generated["issues"]:
                        raise AccountingError("；".join(generated["issues"]), 409)
                    applied = self.queue.intelligence.ledger._apply_template_in_transaction(
                        conn,
                        actor,
                        job["book_id"],
                        job["draft_id"],
                        job["expected_draft_version"],
                        generated,
                    )
                    execution.finish_rule(conn, generated, applied)
                    self.queue.intelligence._assessment(conn, actor, event, persist=True)
                    conn.execute("RELEASE SAVEPOINT automatic_template_adoption")
                    return applied
                conn.execute("RELEASE SAVEPOINT automatic_template_adoption")
            except AccountingError:
                conn.execute("ROLLBACK TO SAVEPOINT automatic_template_adoption")
                conn.execute("RELEASE SAVEPOINT automatic_template_adoption")
                # A template match with incomplete tax/auxiliary evidence must not
                # silently fall through to a model that guesses missing facts.
                self._finish_proposal_attention(
                    conn,
                    job,
                    execution,
                    "template_blocked",
                    "企业记账模板存在冲突或缺少已核对的税额、辅助信息，请会计处理",
                )
                return None
            if not self.queue.intelligence.provider.settings.suggestion_available:
                self._finish_proposal_attention(
                    conn,
                    job,
                    execution,
                    "provider_unavailable",
                    "无匹配企业模板且分录供应商尚未配置，请会计处理",
                )
                return None
        return self.queue.intelligence.propose(
            actor,
            job["book_id"],
            job["draft_id"],
            ProposalRequest(
                expected_version=job["expected_draft_version"],
                idempotency_key=f"proposal-queue:{job['id']}:{job['attempt_count']}",
            ),
            execution=execution,
        )

    def _finish_proposal_attention(self, conn, job, execution, code, detail):
        execution.record_timings(conn)
        if self.queue.db.is_postgres:
            if not execution._function(
                conn,
                "worker_finish_proposal_job",
                (job["id"], job["lease_token"], "needs_attention", code, detail, None, 0),
            ):
                raise AccountingError("分录任务租约已失效", 409)
        else:
            self.queue.proposals.finish_sqlite(conn, job, "needs_attention", code, detail)

    def run_once(self):
        self.maintenance(0)
        self.recover()
        job = self.queue.claim(
            self.worker_id, self.lease_seconds, SchedulingSettings.from_env().requested_global_limit
        )
        if not job:
            return False
        # A claimed job is real in-flight work. Do not leave the last
        # observation at zero while a slow provider call is running.
        self.maintenance(1)
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(stop_heartbeat, self.heartbeat_seconds),
            name="ocr-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            self.process(job)
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)
            self.maintenance(0)
        return True

    def _heartbeat_loop(self, stop, interval=10.0):
        while not stop.wait(interval):
            try:
                self.heartbeat(1)
            except Exception:
                logger.warning("票据队列心跳暂不可用，任务租约保持不变")

    def heartbeat(self, inflight=1):
        """Persist a lightweight in-flight observation without alert refresh."""
        if self._operations is None:
            from ..operations.service import OperationsService

            self._operations = OperationsService(
                self.queue.db,
                self.queue.intelligence._meter(),
                intelligence=self.queue.intelligence,
            )
        with self.queue.worker_transaction() as conn:
            self._operations.heartbeat(conn, self.worker_id, inflight, "active")

    def maintenance(self, inflight=0, stopped=False):
        if self._operations is None:
            from ..operations.service import OperationsService

            self._operations = OperationsService(
                self.queue.db,
                self.queue.intelligence._meter(),
                intelligence=self.queue.intelligence,
            )
        with self.queue.worker_transaction() as conn:
            self._operations.heartbeat(
                conn,
                self.worker_id,
                inflight,
                "stopped" if stopped else "active" if inflight else "idle",
            )
        if not stopped:
            self.queue.automation.resume_pending()
            with self.queue.worker_transaction() as conn:
                self._operations.refresh_pending(conn)

    def _loop(self):
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="ocr-job") as pool:
            futures = set()
            last_monitor = 0
            while not self._stop.is_set():
                for future in [value for value in futures if value.done()]:
                    futures.remove(future)
                    try:
                        future.result()
                    except Exception:
                        logger.warning("后台任务暂未完成，将按持久租约恢复")
                try:
                    if time.monotonic() - last_monitor >= 10:
                        self.maintenance(len(futures))
                        last_monitor = time.monotonic()
                    self.recover()
                    while len(futures) < self.concurrency and not self._stop.is_set():
                        job = self.queue.claim(
                            self.worker_id,
                            self.lease_seconds,
                            SchedulingSettings.from_env().requested_global_limit,
                        )
                        if not job:
                            break
                        futures.add(pool.submit(self.process, job))
                except Exception:
                    logger.warning("后台队列暂不可用，将继续读取持久状态")
                self._stop.wait(self.poll_seconds)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ocr-queue-dispatcher", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            if not self._thread.is_alive():
                try:
                    self.maintenance(0, stopped=True)
                except Exception:
                    pass
        # In-flight calls may finish with their lease. A process kill leaves
        # durable recovery state; queued work is never deleted at shutdown.


def main(ready_callback=None):
    import argparse
    import signal
    from pathlib import Path
    from dotenv import load_dotenv
    from ..accounting.database import Database
    from ..storage.factory import create_attachment_store
    from ..intelligence.service import IntelligenceService

    parser = argparse.ArgumentParser(description="持久票据处理后台服务")
    parser.add_argument("--env-file", help="显式加载新的会计配置文件，默认不加载任何文件")
    parser.add_argument("--once", action="store_true", help="恢复过期任务后最多处理一个任务并退出")
    args = parser.parse_args()
    if args.env_file:
        config_path = Path(args.env_file).resolve()
        if not config_path.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(config_path, override=False)
    from .restore_hold import enforce_restore_hold

    enforce_restore_hold()
    url = os.getenv("ACCOUNTING_WORKER_DATABASE_URL", "")
    root = os.getenv("ACCOUNTING_ATTACHMENT_ROOT", "")
    if not url:
        parser.error(
            "须显式配置 ACCOUNTING_WORKER_DATABASE_URL；原件存储须使用共享 Supabase 或本地持久目录"
        )
    if os.getenv("ACCOUNTING_ENV", "local").lower() == "production" or any(
        os.getenv(key, "").lower() in {"prod", "production"}
        for key in ("APP_ENV", "ENV", "ENVIRONMENT")
    ):
        if not url.startswith(("postgres://", "postgresql://")) or parse_qs(
            urlsplit(url).query
        ).get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}:
            parser.error("生产worker须使用独立PostgreSQL与显式sslmode=require或证书校验")
    worker_started = False
    worker = None
    database = None
    try:
        database = Database(url)
        database.assert_restore_hold()
        storage = create_attachment_store(root)
        storage.verify_private()
        queue = QueueService(database, IntelligenceService(database, storage))
        lease = int(os.getenv("ACCOUNTING_QUEUE_LEASE_SECONDS", "180"))
        if lease < 150:
            raise ValueError("部署租约至少150秒，覆盖PDF准备与模型调用")
        worker = QueueWorker(
            queue,
            concurrency=int(os.getenv("ACCOUNTING_QUEUE_CONCURRENCY", "2")),
            lease_seconds=lease,
            poll_seconds=float(os.getenv("ACCOUNTING_QUEUE_POLL_SECONDS", "1")),
        )
        if args.once:
            worker.run_once()
            return 0
        worker.start()
        worker_started = True
        if ready_callback:
            ready_callback()
        halted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: halted.set())
        signal.signal(signal.SIGTERM, lambda *_: halted.set())
        while not halted.wait(1):
            pass
        return 0
    except Exception:
        logger.error("后台服务启动或执行失败，请检查独立账号、迁移及模型配置；连接和原件内容已隐藏")
        return 1
    finally:
        if worker_started:
            worker.stop()
        if database:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
