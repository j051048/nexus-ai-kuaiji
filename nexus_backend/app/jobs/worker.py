"""Small durable OCR worker. PostgreSQL requires a separately provisioned login."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import logging
import os
import threading
import time
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

from ..accounting.domain import Actor, AccountingError
from ..intelligence.requests import OCRRequest
from .service import QueueService

logger = logging.getLogger(__name__)


class QueueExecution:
    def __init__(self, queue, job):
        self.queue, self.job = queue, job
        self.sent = False

    def _function(self, conn, name, parameters):
        if not self.queue.db.is_postgres:
            return None
        conn.execute("SET LOCAL ROLE accounting_worker")
        try:
            return conn.execute("SELECT accounting." + name + "(" + ",".join("?" for _ in parameters) + ")", parameters).fetchone()[0]
        finally:
            conn.execute("SET LOCAL ROLE accounting_app")

    def check(self, conn):
        if self.queue.db.is_postgres:
            valid = self._function(conn, "worker_check_ocr_lease", (self.job["id"], self.job["lease_token"]))
        else:
            valid = conn.execute("SELECT 1 FROM ocr_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (self.job["id"], self.job["lease_token"], self.queue.timestamp())).fetchone()
        if not valid:
            raise AccountingError("后台任务租约已失效，旧响应不会发布", 409)

    def inputs(self, event, attachment):
        if event["id"] != self.job["event_id"] or event["version"] != self.job["expected_version"] or attachment["id"] != self.job["attachment_id"] or attachment["sha256"] != self.job["attachment_sha256"] or self.queue.configuration_fingerprint() != self.job["configuration_fingerprint"]:
            raise AccountingError("后台任务资料或模型配置已改变，请按当前资料重新处理", 409)

    def bind(self, conn, run):
        self.check(conn)
        if self.queue.db.is_postgres:
            valid = self._function(conn, "worker_bind_ocr_run", (self.job["id"], self.job["lease_token"], run["id"]))
            if not valid:
                raise AccountingError("后台任务未能绑定当前识别记录", 409)
        else:
            conn.execute("UPDATE ocr_jobs SET current_run_id=?,updated_at=? WHERE id=? AND lease_token=?", (run["id"], self.queue.timestamp(), self.job["id"], self.job["lease_token"]))
            conn.execute("UPDATE ocr_attempts SET ai_run_id=? WHERE job_id=? AND lease_token=? AND status='running'", (run["id"], self.job["id"], self.job["lease_token"]))
        self.job["current_run_id"] = run["id"]

    def before_send(self, conn):
        self.check(conn)
        if self.queue.db.is_postgres:
            valid = self._function(conn, "worker_mark_ocr_sending", (self.job["id"], self.job["lease_token"]))
            if not valid:
                raise AccountingError("任务已失效，未发送票据", 409)
        else:
            conn.execute("UPDATE ocr_attempts SET phase='sending' WHERE job_id=? AND lease_token=? AND status='running'", (self.job["id"], self.job["lease_token"]))
        self.sent = True

    def finish(self, conn, run, error=None):
        self.check(conn)
        status, delay = run["status"], 0
        if status == "failed" and error:
            if (getattr(error, "retryable", False) or error.code == "busy") and self.job["attempt_count"] < self.job["max_attempts"]:
                attempt_in_round = max(1, self.job["attempt_count"] - (self.job["max_attempts"] - 3))
                status, delay = "retry_wait", 30 * (2 ** min(attempt_in_round - 1, 1))
            elif self.sent and error.code in {"timeout", "network_error", "processing_failed"}:
                status = "interrupted"
        code, detail = run["error_code"], run["error_detail"]
        if status == "interrupted":
            detail = "模型请求结果不确定，请核对后决定是否重试；重试可能再次调用模型"
        if self.queue.db.is_postgres:
            valid = self._function(conn, "worker_finish_ocr_job", (self.job["id"], self.job["lease_token"], status, code, detail, run["id"], delay))
            if not valid:
                raise AccountingError("后台任务已失效，结果未发布", 409)
        else:
            self.queue._finish_sqlite(conn, self.job, status, code, detail, run["id"], delay)


class QueueWorker:
    def __init__(self, queue_service, worker_id=None, concurrency=2, lease_seconds=180, poll_seconds=1):
        if type(concurrency) is not int or concurrency not in {1, 2} or not 30 <= lease_seconds <= 600 or not 0.2 <= poll_seconds <= 10:
            raise ValueError("队列并发须为1或2，租约30–600秒，轮询0.2–10秒")
        self.queue = queue_service
        self.worker_id = worker_id or "worker-" + uuid4().hex
        self.concurrency, self.lease_seconds, self.poll_seconds = concurrency, lease_seconds, poll_seconds
        self._stop = threading.Event()
        self._thread = None
        self._operations = None
        if self.queue.db.is_postgres:
            with closing(self.queue.db.connect()) as conn:
                permitted = conn.execute("SELECT pg_has_role(session_user,'accounting_worker','MEMBER')").fetchone()[0]
                if not permitted:
                    raise RuntimeError("后台处理须使用独立 accounting_worker 登录，API运行账号不能领取跨企业任务")
                principal = conn.execute("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit FROM pg_roles WHERE rolname=session_user").fetchone()
                parents = [row[0] for row in conn.execute("SELECT parent.rolname FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member WHERE child.rolname=session_user")]
                if any(principal[key] for key in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolinherit")) or set(parents) != {"accounting_app", "accounting_worker"}:
                    raise RuntimeError("worker登录必须为独立受限账号，仅授予accounting_app与accounting_worker且禁止继承或角色管理权限")

    def recover(self):
        return self.queue.recover()

    def renew(self, job):
        with self.queue.worker_transaction() as conn:
            if self.queue.db.is_postgres:
                return conn.execute("SELECT accounting.worker_renew_ocr_lease(?,?,?)", (job["id"], job["lease_token"], self.lease_seconds)).fetchone()[0]
            return conn.execute("UPDATE ocr_jobs SET lease_until=?,updated_at=? WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (self.queue.timestamp(self.lease_seconds), self.queue.timestamp(), job["id"], job["lease_token"], self.queue.timestamp())).rowcount == 1

    def _failure(self, job, status, code, detail):
        with self.queue.worker_transaction() as conn:
            if self.queue.db.is_postgres:
                return conn.execute("SELECT accounting.worker_finish_ocr_job(?,?,?,?,?,?,?)", (job["id"], job["lease_token"], status, code, detail, job["current_run_id"], 0)).fetchone()[0]
            current = conn.execute("SELECT * FROM ocr_jobs WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (job["id"], job["lease_token"], self.queue.timestamp())).fetchone()
            if not current:
                return False
            self.queue._finish_sqlite(conn, current, status, code, detail, current["current_run_id"])
            self.queue._invalidate_run(conn, current)
            return True

    def process(self, job):
        actor = Actor(job["execution_user_id"], job["firm_id"])
        execution = QueueExecution(self.queue, job)
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
            with self.queue.db.transaction(actor=actor) as conn:
                event, attachment = self.queue._input(conn, actor, job["event_id"], job["attachment_id"], job["company_id"], job["expected_version"])
                execution.check(conn)
                execution.inputs(event, attachment)
                self.queue._ready()
            return self.queue.intelligence.extract(actor, job["event_id"], OCRRequest(attachment_id=job["attachment_id"], expected_version=job["expected_version"], idempotency_key=f"queue:{job['id']}:{job['attempt_count']}"), execution=execution)
        except AccountingError as error:
            from ..operations.meter import BudgetError
            if isinstance(error, BudgetError):
                self._failure(job, "failed", error.code, error.detail)
            else:
                self._failure(job, "stale", "context_changed", "权限、期间或资料已改变，任务停止，请由获授权人员重新处理")
        except Exception:
            self._failure(job, "interrupted" if execution.sent else "failed", "worker_failure", "任务执行中断，请读取当前结果并由人员决定是否重试")
        finally:
            heartbeat_stop.set()
            heart.join(timeout=2)

    def run_once(self):
        self.maintenance(0)
        self.recover()
        job = self.queue.claim(self.worker_id, self.lease_seconds, self.concurrency)
        if not job:
            return False
        self.process(job)
        self.maintenance(0)
        return True

    def maintenance(self, inflight=0, stopped=False):
        if self._operations is None:
            from ..operations.service import OperationsService
            self._operations = OperationsService(self.queue.db, self.queue.intelligence._meter(), intelligence=self.queue.intelligence)
        with self.queue.worker_transaction() as conn:
            self._operations.maintenance(conn, self.worker_id, inflight, "stopped" if stopped else "active" if inflight else "idle")

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
                        job = self.queue.claim(self.worker_id, self.lease_seconds, self.concurrency)
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


def main():
    import argparse
    import signal
    from pathlib import Path
    from dotenv import load_dotenv
    from ..accounting.database import Database
    from ..business.storage import AttachmentStore
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
    url = os.getenv("ACCOUNTING_WORKER_DATABASE_URL", "")
    root = os.getenv("ACCOUNTING_ATTACHMENT_ROOT", "")
    if not url or not root:
        parser.error("须显式配置 ACCOUNTING_WORKER_DATABASE_URL 和 ACCOUNTING_ATTACHMENT_ROOT")
    if os.getenv("ACCOUNTING_ENV", "local").lower() == "production" or any(os.getenv(key, "").lower() in {"prod", "production"} for key in ("APP_ENV", "ENV", "ENVIRONMENT")):
        if not url.startswith(("postgres://", "postgresql://")) or parse_qs(urlsplit(url).query).get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}:
            parser.error("生产worker须使用独立PostgreSQL与显式sslmode=require或证书校验")
    try:
        database = Database(url)
        queue = QueueService(database, IntelligenceService(database, AttachmentStore(root)))
        lease = int(os.getenv("ACCOUNTING_QUEUE_LEASE_SECONDS", "180"))
        if lease < 150:
            raise ValueError("部署租约至少150秒，覆盖PDF准备与模型调用")
        worker = QueueWorker(queue, concurrency=int(os.getenv("ACCOUNTING_QUEUE_CONCURRENCY", "2")), lease_seconds=lease, poll_seconds=float(os.getenv("ACCOUNTING_QUEUE_POLL_SECONDS", "1")))
        if args.once:
            worker.run_once()
            return 0
        worker.start()
        halted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: halted.set())
        signal.signal(signal.SIGTERM, lambda *_: halted.set())
        while not halted.wait(1):
            pass
        worker.stop()
        return 0
    except Exception:
        logger.error("后台服务启动或执行失败，请检查独立账号、迁移及模型配置；连接和原件内容已隐藏")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
