"""Dedicated projection-maintenance process.

Read requests may wake a request-local hint, but rebuilds run only here with a
system-capable database identity. API replicas therefore never own long-lived
background loops or race each other to build the same company projection.
"""

import argparse
import logging
import os
import signal
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv

from .accounting.database import Database
from .workspace.projection import ProjectionRefresher

logger = logging.getLogger(__name__)


def _database_url() -> str:
    return (
        os.getenv("ACCOUNTING_MAINTENANCE_DATABASE_URL")
        or os.getenv("ACCOUNTING_SYSTEM_DATABASE_URL")
        or os.getenv("ACCOUNTING_DATABASE_URL")
        or "data/accounting.db"
    )


def main(ready_callback=None):
    parser = argparse.ArgumentParser(description="统一待办投影维护进程")
    parser.add_argument("--env-file", help="显式加载维护配置，默认不加载任何文件")
    parser.add_argument("--once", action="store_true", help="扫描并刷新一轮后退出")
    args = parser.parse_args()
    if args.env_file:
        config_path = Path(args.env_file).resolve()
        if not config_path.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(config_path, override=False)

    url = _database_url()
    from .jobs.restore_hold import enforce_restore_hold

    enforce_restore_hold(
        database_path=None if url.startswith(("postgres://", "postgresql://")) else url
    )
    production = os.getenv("ACCOUNTING_ENV", "local").lower() == "production" or any(
        os.getenv(key, "").lower() in {"prod", "production"}
        for key in ("APP_ENV", "ENV", "ENVIRONMENT")
    )
    if production and (
        not url.startswith(("postgres://", "postgresql://"))
        or parse_qs(urlsplit(url).query).get("sslmode", [""])[0]
        not in {"require", "verify-ca", "verify-full"}
    ):
        parser.error("生产投影维护须使用可切换 accounting_system 的独立 PostgreSQL 账号与显式 TLS")

    database = None
    refresher = None
    try:
        database = Database(url)
        poll_seconds = float(os.getenv("ACCOUNTING_PROJECTION_POLL_SECONDS", "5"))
        if not 1 <= poll_seconds <= 60:
            raise ValueError("投影维护轮询须为1–60秒")
        refresher = ProjectionRefresher(database, poll_seconds=poll_seconds)
        if args.once:
            refresher.run_once()
            return 0
        refresher.start()
        if ready_callback:
            ready_callback()
        halted = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: halted.set())
        signal.signal(signal.SIGTERM, lambda *_: halted.set())
        while not halted.wait(1):
            pass
        return 0
    except Exception:
        logger.error("投影维护进程启动失败；请检查数据库账号是否可切换 accounting_system")
        return 1
    finally:
        if refresher:
            refresher.stop()
        if database:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
