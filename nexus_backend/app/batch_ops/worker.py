import logging
import threading
from uuid import uuid4

from ..accounting.domain import AccountingError

logger = logging.getLogger(__name__)


class BatchWorker:
    def __init__(self, service, worker_id=None):
        self.service = service
        self.worker_id = worker_id or "batch-" + uuid4().hex
        self.started = False
        self._stop = threading.Event()
        self._thread = None

    def process(self, item):
        try:
            row = self.service.execute(item)
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
            self.service.finish(
                item,
                "failed",
                error_code=getattr(error, "code", "company_blocked"),
                error_detail=str(error),
            )
        except Exception:
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

    def run_once(self):
        if self._stop.is_set():
            return False
        self.started = True
        self.service.recover()
        if self._stop.is_set():
            return False
        item = self.service.claim(self.worker_id)
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
