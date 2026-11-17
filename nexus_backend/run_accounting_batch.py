"""Dedicated batch-operations worker for the container deployment."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import threading

logger = logging.getLogger(__name__)


def main(ready_callback=None) -> int:
    from dotenv import load_dotenv

    from app.accounting.database import Database
    from app.batch_ops.service import BatchOperationsService
    from app.batch_ops.worker import BatchWorker
    from app.intelligence.service import IntelligenceService
    from app.jobs.restore_hold import enforce_restore_hold
    from app.jobs.worker import verify_worker_login
    from app.ledgers.service import LedgersService
    from app.monthly.batch import MonthlyBatchService
    from app.service_plans.service import ServicePlansService
    from app.storage.factory import create_attachment_store

    parser = argparse.ArgumentParser(description="批量运营队列后台")
    parser.add_argument("--env-file", help="显式加载新的会计配置文件，默认不加载任何文件")
    parser.add_argument("--once", action="store_true", help="最多处理一轮并退出")
    args = parser.parse_args()
    if args.env_file:
        config = Path(args.env_file).resolve()
        if not config.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(config, override=False)

    enforce_restore_hold()
    url = os.getenv("ACCOUNTING_WORKER_DATABASE_URL", "").strip()
    if not url:
        parser.error("须显式配置 ACCOUNTING_WORKER_DATABASE_URL")
    root = os.getenv("ACCOUNTING_ATTACHMENT_ROOT", "")
    database = None
    worker = None
    started = False
    try:
        database = Database(url)
        database.assert_restore_hold()
        verify_worker_login(database)
        storage = create_attachment_store(root)
        storage.verify_private()
        intelligence = IntelligenceService(database, storage)
        monthly = MonthlyBatchService(
            database,
            intelligence.ledger,
            LedgersService(database),
            ServicePlansService(database, intelligence.ledger, intelligence.business),
            intelligence.business,
        )
        worker = BatchWorker(BatchOperationsService(database, monthly))
        if args.once:
            worker.run_once()
            return 0
        worker.start()
        started = True
        if ready_callback:
            ready_callback()
        halted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: halted.set())
        signal.signal(signal.SIGTERM, lambda *_: halted.set())
        while not halted.wait(1):
            pass
        return 0
    except Exception:
        logger.error("批量后台启动或执行失败，请检查独立账号、迁移与存储；连接和原件内容已隐藏")
        return 1
    finally:
        drained = True
        if started and worker is not None:
            try:
                worker.stop()
                drained = worker.wait_for_idle(30) is True
            except Exception:
                drained = False
            if not drained:
                logger.warning("批量后台在宽限期内未排空，数据库保持到进程退出，依赖持久租约恢复")
        if database is not None and drained:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
