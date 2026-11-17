"""Dedicated cross-firm scheduler process for durable plans and reminders."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import os
import signal
import threading

logger = logging.getLogger(__name__)


def main(ready_callback=None) -> int:
    from dotenv import load_dotenv

    from app.accounting.database import Database
    from app.jobs.restore_hold import enforce_restore_hold
    from app.scheduler.runner import SchedulerLoop
    from app.scheduler.service import SchedulerService

    parser = argparse.ArgumentParser(description="定时计划触发进程")
    parser.add_argument("--env-file", help="显式加载调度配置，默认不加载任何文件")
    parser.add_argument("--once", action="store_true", help="扫描一轮后退出")
    args = parser.parse_args()
    if args.env_file:
        config = Path(args.env_file).resolve()
        if not config.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(config, override=False)

    enforce_restore_hold()
    url = os.getenv("ACCOUNTING_SCHEDULER_DATABASE_URL", "").strip()
    if not url:
        parser.error("须显式配置 ACCOUNTING_SCHEDULER_DATABASE_URL")
    database = None
    loop = None
    try:
        database = Database(url, default_role="accounting_app")
        database.assert_restore_hold()
        interval = float(os.getenv("ACCOUNTING_SCHEDULER_INTERVAL_SECONDS", "30"))
        loop = SchedulerLoop(SchedulerService(database), interval_seconds=interval)
        if args.once:
            loop.run_once()
            return 0
        loop.start()
        if ready_callback:
            ready_callback()
        halted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: halted.set())
        signal.signal(signal.SIGTERM, lambda *_: halted.set())
        while not halted.wait(1):
            pass
        return 0
    except Exception:
        logger.error("调度进程启动或执行失败，请检查独立账号、迁移与数据库连接；连接信息已隐藏")
        return 1
    finally:
        if loop is not None:
            loop.stop()
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
