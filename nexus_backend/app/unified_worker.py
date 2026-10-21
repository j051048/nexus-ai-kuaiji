"""Unified worker: one process with weighted priority over the durable queues.

实时会计助手（高优）→ 单张票据识别与分录任务（中优）→ 批量旧账迁移（低优）。
领取与租约仍由数据库逐条判定，因此本进程可以随时拆回独立 worker，也不会重复领取；
中小部署运行「1 个 API + 1 个统一 Worker」即可，大规模时再按队列横向扩容。
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

ROLES = ("assistant", "queue", "batch", "migration")
MONITOR_INTERVAL = 15.0


class UnifiedWorker:
    def __init__(
        self,
        *,
        assistant_worker=None,
        queue_worker=None,
        batch_worker=None,
        migration_worker=None,
        assistant_budget=1,
        queue_budget=2,
        batch_budget=2,
        migration_budget=1,
        idle_seconds=1.0,
    ):
        for label, budget in (
            ("assistant", assistant_budget),
            ("queue", queue_budget),
            ("batch", batch_budget),
            ("migration", migration_budget),
        ):
            if type(budget) is not int or not 1 <= budget <= 10:
                raise ValueError(f"统一后台每轮预算须为1–10（{label}）")
        if type(idle_seconds) not in {int, float} or not 0.05 <= idle_seconds <= 30:
            raise ValueError("统一后台空闲轮询须为0.05–30秒")
        plan = []
        if assistant_worker is not None:
            plan.append((assistant_worker, assistant_budget))
        if queue_worker is not None:
            plan.append((queue_worker, queue_budget))
        if batch_worker is not None:
            plan.append((batch_worker, batch_budget))
        if migration_worker is not None:
            plan.append((migration_worker, migration_budget))
        if not plan:
            raise ValueError("统一后台至少需要一类队列")
        self._plan = plan
        self.assistant_worker, self.queue_worker = assistant_worker, queue_worker
        self.batch_worker = batch_worker
        self.migration_worker = migration_worker
        self.idle_seconds = idle_seconds
        self._stop = threading.Event()
        self._thread = None
        self._last_monitor = 0.0
        self._executor = None
        self._inflight = {}
        self._inflight_lock = threading.Lock()
        self._draining = False
        self._executor_closed = False
        self._background_cursor = 0
        self._background_sequence = []
        for label, candidate, budget in (
            ("queue", queue_worker, queue_budget),
            ("batch", batch_worker, batch_budget),
            ("migration", migration_worker, migration_budget),
        ):
            if candidate is not None:
                self._background_sequence.extend([(label, candidate)] * budget)

    def run_once(self):
        """One priority pass; returns whether any queue processed a unit."""
        processed = False
        for worker, budget in self._plan:
            for _ in range(budget):
                try:
                    result = worker.run_once()
                except Exception:
                    logger.warning("统一后台的一类队列本轮未完成，将按持久租约恢复")
                    break
                if not result:
                    break
                processed = True
        return processed

    def _drain(self):
        with self._inflight_lock:
            done = [(role, future) for role, future in self._inflight.items() if future.done()]
            for role, future in done:
                self._inflight.pop(role, None)
        worked = False
        for role, future in done:
            try:
                worked |= bool(future.result())
            except Exception:
                logger.warning("统一后台的%s任务异常，已按持久租约恢复", role)
        return worked

    def _submit(self, role, worker):
        with self._inflight_lock:
            if role in self._inflight:
                return False
            future = self._executor.submit(worker.run_once)
            self._inflight[role] = future
        return True

    def run_tick(self):
        """Submit at most one task per role without waiting for slow work.

        Two local slots: one assistant, one shared background. The database
        claim remains the authority; only a free local slot triggers a claim.
        """
        if self._stop.is_set() or self._executor is None:
            return False
        self._drain()
        submitted = False
        if self.assistant_worker is not None:
            submitted |= self._submit("assistant", self.assistant_worker)
        with self._inflight_lock:
            busy_background = any(role != "assistant" for role in self._inflight)
        if not busy_background and self._background_sequence:
            index = self._background_cursor % len(self._background_sequence)
            role, worker = self._background_sequence[index]
            self._background_cursor = (index + 1) % len(self._background_sequence)
            submitted |= self._submit(role, worker)
        return submitted

    def run_until_idle(self, timeout=120.0):
        """Concurrent one-shot mode: drain real work, then stop submitting."""
        if self._executor is None or self._executor_closed:
            self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="unified-task")
            self._executor_closed = False
        deadline = time.monotonic() + timeout
        worked = False
        try:
            while time.monotonic() < deadline:
                self.run_tick()
                while self.inflight_count() and time.monotonic() < deadline:
                    if self._drain():
                        worked = True
                    time.sleep(0.01)
                if self.inflight_count():
                    break
                if not worked:
                    break
                worked = False
            return worked
        finally:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor_closed = True

    def wait_for_idle(self, timeout=5.0):
        """Wait for already claimed work; never leaves a draining instance marked active."""
        deadline = time.monotonic() + timeout
        while self.inflight_count() and time.monotonic() < deadline:
            self._drain()
            time.sleep(0.01)
        if self.inflight_count():
            return False
        if self._draining:
            self._draining = False
            self._monitor_tick("stopped", inflight=0)
        return True

    def inflight_count(self):
        with self._inflight_lock:
            return len(self._inflight)

    def _monitor_tick(self, state, inflight=None):
        monitor = getattr(self.assistant_worker, "monitor", None)
        if monitor is None:
            return
        if inflight is None:
            with self._inflight_lock:
                count = 1 if "assistant" in self._inflight else 0
        else:
            count = inflight
        try:
            heartbeat = getattr(monitor, "heartbeat", None)
            if callable(heartbeat):
                heartbeat(inflight=min(count, 2), state=state)
            else:
                monitor.tick(inflight=min(count, 2), state=state)
        except Exception:
            logger.warning("会计助手运行监测暂不可用，处理任务保持原有租约控制")

    def _loop(self):
        while not self._stop.is_set():
            if time.monotonic() - self._last_monitor >= MONITOR_INTERVAL:
                with self._inflight_lock:
                    assistant_active = "assistant" in self._inflight
                self._monitor_tick("active" if assistant_active else "idle")
                self._last_monitor = time.monotonic()
            try:
                processed = self.run_tick()
            except Exception:
                logger.warning("统一后台队列暂不可用，将继续读取持久状态")
                processed = False
            if not processed or self.inflight_count() == 0:
                self._stop.wait(self.idle_seconds)
            else:
                # Do not spin while a claimed task is still running; the
                # bounded executor owns the actual wait.
                self._stop.wait(0.01)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if self._draining:
            raise RuntimeError("上一代统一后台仍在排空，不能启动第二套调度")
        self._stop.clear()
        if self._executor is None or self._executor_closed:
            self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="unified-task")
            self._executor_closed = False
        self._thread = threading.Thread(target=self._loop, name="unified-worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        # Completed futures are not in-flight work. Drain them before deciding
        # whether this instance is really draining.
        self._drain()
        with self._inflight_lock:
            pending = len(self._inflight)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor_closed = True
        if pending:
            self._draining = True
            self._monitor_tick("active", inflight=min(pending, 2))
            return
        if self.queue_worker is not None:
            try:
                self.queue_worker.maintenance(0, stopped=True)
            except Exception:
                pass
        self._monitor_tick("stopped", inflight=0)


def main(ready_callback=None):
    import argparse
    import signal
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit

    from dotenv import load_dotenv

    from .accounting.database import Database
    from .agent_workspace.service import AgentWorkspaceService
    from .assistant.meter import AssistantMeter
    from .assistant.provider import AssistantProvider
    from .assistant.service import AssistantService
    from .assistant.worker import AssistantWorker
    from .batch_ops.service import BatchOperationsService
    from .batch_ops.worker import BatchWorker
    from .intelligence.provider import AISettings, OpenAICompatibleProvider
    from .intelligence.service import IntelligenceService
    from .jobs.service import QueueService
    from .jobs.worker import QueueWorker, verify_worker_login
    from .legacy_migration.service import LegacyMigrationService
    from .legacy_migration.settlements import LegacySettlementService
    from .legacy_migration.worker import MigrationWorker
    from .ledgers.service import LedgersService
    from .monthly.batch import MonthlyBatchService
    from .operations.meter import MeterService
    from .service_plans.service import ServicePlansService
    from .storage.factory import create_attachment_store

    parser = argparse.ArgumentParser(description="统一后台：助手、票据队列与旧账迁移的加权调度进程")
    parser.add_argument("--env-file", help="显式加载新的会计配置文件，默认不加载任何文件")
    parser.add_argument("--once", action="store_true", help="按优先级处理一轮并退出")
    args = parser.parse_args()
    if args.env_file:
        config = Path(args.env_file).resolve()
        if not config.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(config, override=False)
    from .jobs.restore_hold import enforce_restore_hold

    enforce_restore_hold()
    url = os.getenv("ACCOUNTING_WORKER_DATABASE_URL", "")
    root = os.getenv("ACCOUNTING_ATTACHMENT_ROOT", "")
    if not url:
        parser.error(
            "须显式配置 ACCOUNTING_WORKER_DATABASE_URL；原件存储须使用共享 Supabase 或本地持久目录"
        )
    production = os.getenv("ACCOUNTING_ENV", "local").lower() == "production" or any(
        os.getenv(key, "").lower() in {"production", "prod"}
        for key in ("APP_ENV", "ENV", "ENVIRONMENT")
    )
    if production and (
        not url.startswith(("postgres://", "postgresql://"))
        or parse_qs(urlsplit(url).query).get("sslmode", [""])[0]
        not in {"require", "verify-ca", "verify-full"}
    ):
        parser.error("生产统一后台须使用独立PostgreSQL worker账号与显式TLS连接")
    roles = {
        part.strip()
        for part in os.getenv("ACCOUNTING_UNIFIED_ROLES", ",".join(ROLES)).split(",")
        if part.strip()
    }
    if not roles or roles - set(ROLES):
        parser.error("ACCOUNTING_UNIFIED_ROLES 仅支持 assistant,queue,batch,migration")
    worker_started = False
    worker = None
    database = None
    try:
        database = Database(url)
        database.assert_restore_hold()
        verify_worker_login(database)
        storage = create_attachment_store(root)
        storage.verify_private()
        queue_worker = assistant_worker = batch_worker = migration_worker = None
        if "queue" in roles:
            queue = QueueService(database, IntelligenceService(database, storage))
            lease = int(os.getenv("ACCOUNTING_QUEUE_LEASE_SECONDS", "180"))
            if lease < 150:
                raise ValueError("部署租约至少150秒，覆盖PDF准备与模型调用")
            queue_worker = QueueWorker(
                queue,
                concurrency=int(os.getenv("ACCOUNTING_QUEUE_CONCURRENCY", "2")),
                lease_seconds=lease,
                poll_seconds=float(os.getenv("ACCOUNTING_QUEUE_POLL_SECONDS", "1")),
            )
        if "assistant" in roles:
            intelligence = IntelligenceService(
                database, storage, provider=OpenAICompatibleProvider(AISettings.from_env())
            )
            ledger, business = intelligence.ledger, intelligence.business
            migration = LegacyMigrationService(database, storage)
            ledger.legacy_settlements = LegacySettlementService(database, migration, ledger)
            provider = AssistantProvider(intelligence.provider)
            service = AssistantService(
                database,
                ledger,
                business,
                agent_workspace=AgentWorkspaceService(database, ledger, business, None, migration),
                provider=provider,
                intelligence=intelligence,
            )
            assistant_worker = AssistantWorker(
                service, provider, AssistantMeter(database, MeterService(database))
            )
        if "batch" in roles:
            batch_intelligence = IntelligenceService(database, storage)
            monthly_batch = MonthlyBatchService(
                database,
                batch_intelligence.ledger,
                LedgersService(database),
                ServicePlansService(
                    database, batch_intelligence.ledger, batch_intelligence.business
                ),
                batch_intelligence.business,
            )
            batch_worker = BatchWorker(BatchOperationsService(database, monthly_batch))
        if "migration" in roles:
            migration_service = LegacyMigrationService(database, storage)
            migration_worker = MigrationWorker(
                migration_service,
                concurrency=int(os.getenv("ACCOUNTING_MIGRATION_CONCURRENCY", "1")),
            )
        worker = UnifiedWorker(
            assistant_worker=assistant_worker,
            queue_worker=queue_worker,
            batch_worker=batch_worker,
            migration_worker=migration_worker,
            assistant_budget=int(os.getenv("ACCOUNTING_UNIFIED_ASSISTANT_BUDGET", "1")),
            queue_budget=int(os.getenv("ACCOUNTING_UNIFIED_QUEUE_BUDGET", "2")),
            batch_budget=int(os.getenv("ACCOUNTING_UNIFIED_BATCH_BUDGET", "2")),
            migration_budget=int(os.getenv("ACCOUNTING_UNIFIED_MIGRATION_BUDGET", "1")),
            idle_seconds=float(os.getenv("ACCOUNTING_UNIFIED_IDLE_SECONDS", "1")),
        )
        if args.once:
            try:
                worker.run_until_idle()
            finally:
                worker.stop()
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
        logger.error("统一后台启动或执行失败，请检查独立账号、迁移与模型配置；连接和原件内容已隐藏")
        return 1
    finally:
        if worker_started:
            worker.stop()
            if not worker.wait_for_idle(30):
                logger.warning("统一后台在宽限期内未排空，数据库保持到进程退出，依赖持久租约恢复")
        if database:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
