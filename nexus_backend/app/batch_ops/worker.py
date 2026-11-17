import logging
import threading
from uuid import uuid4

from ..accounting.domain import AccountingError

logger = logging.getLogger(__name__)


class BatchWorker:
    def __init__(self, service, worker_id=None, lease_seconds=120, renew_interval=None):
        self.service = service
        self.worker_id = worker_id or "batch-" + uuid4().hex
        self.lease_seconds = lease_seconds
        self.renew_interval = renew_interval or min(15, max(1, lease_seconds / 3))
        self.started = False
        self._stop = threading.Event()
        self._thread = None

    def process(self, item):
        lost = threading.Event()
        stop_renewal = threading.Event()

        def renew_loop():
            while not stop_renewal.wait(self.renew_interval):
                try:
                    if not self.service.renew(item, self.lease_seconds):
                        lost.set()
                        return
                except Exception:
                    logger.warning("批量任务续租失败，旧执行器停止提交结果")
                    lost.set()
                    return

        renewal = threading.Thread(target=renew_loop, name="batch-lease-renewal", daemon=True)
        renewal.start()
        try:
            row = self.service.execute(item)
            if lost.is_set():
                logger.warning("批量任务租约已失去，结果未提交")
                return
            status = {
                "created": "succeeded",
                "skipped": "skipped",
                "blocked": "failed",
            }.get(row.get("status"), "failed")
            self.service.finish(
                item,
                status,
                result=row,
                error_code="company_blocked" if status == "failed" else "",
                error_detail=row.get("message", "") if status == "failed" else "",
            )
        except AccountingError as error:
            if lost.is_set():
                logger.warning("批量任务租约已失去，失败结果未提交")
                return
            self.service.finish(
                item,
                "failed",
                error_code=getattr(error, "code", "company_blocked"),
                error_detail=str(error),
            )
        except Exception:
            if lost.is_set():
                logger.warning("批量任务租约已失去，失败结果未提交")
                return
            exhausted = int(item.get("attempt", 0)) >= int(item.get("max_attempts", 0))
            self.service.finish(
                item,
                "failed" if exhausted else "retry_wait",
                error_code="worker_failure",
                error_detail=(
                    "批处理持续失败且达到重试上限，请人工处理"
                    if exhausted
                    else "批处理执行中断，将按租约重试"
                ),
                retry_seconds=30,
            )
        finally:
            stop_renewal.set()
            renewal.join(timeout=5)

    def run_once(self):
        if self._stop.is_set():
            return False
        self.started = True
        self.service.recover()
        if self._stop.is_set():
            return False
        item = self.service.claim(self.worker_id, self.lease_seconds)
        if not item:
            return False
        self.process(item)
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(0.5)
            except Exception:
                logger.warning("批量运营任务本轮未完成，将按持久租约恢复")
                self._stop.wait(1)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.prepare_run()
        self._thread = threading.Thread(
            target=self._loop, name="batch-operations-worker", daemon=True
        )
        self._thread.start()

    def prepare_run(self):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("批量作业尚未排空，不能重新启动")
        self._stop.clear()

    def stop(self):
        self._stop.set()

    def wait_for_idle(self, timeout=30):
        if self._thread:
            self._thread.join(timeout=max(0, timeout))
            return not self._thread.is_alive()
        return True
