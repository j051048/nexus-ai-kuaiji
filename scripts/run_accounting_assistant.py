"""Explicit, private worker configuration; no old platform environment files."""
from pathlib import Path
import argparse
import logging
import os
import signal
import sys
import threading
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))


def main(ready_callback=None):
    from dotenv import load_dotenv
    from app.accounting.database import Database
    from app.agent_workspace.service import AgentWorkspaceService
    from app.intelligence.provider import AISettings, OpenAICompatibleProvider
    from app.intelligence.service import IntelligenceService
    from app.storage.factory import create_attachment_store
    from app.legacy_migration.service import LegacyMigrationService
    from app.legacy_migration.settlements import LegacySettlementService
    from app.operations.meter import MeterService
    from app.assistant.provider import AssistantProvider
    from app.assistant.meter import AssistantMeter
    from app.assistant.service import AssistantService
    from app.assistant.worker import AssistantWorker
    parser = argparse.ArgumentParser(description="持久会计助手后台服务")
    parser.add_argument("--env-file", help="显式加载会计配置文件")
    parser.add_argument("--once", action="store_true", help="恢复租约后最多处理一轮对话")
    args = parser.parse_args()
    if args.env_file:
        config = Path(args.env_file).resolve()
        if not config.is_file():
            parser.error("指定配置文件不存在")
        load_dotenv(config, override=False)
    url = os.getenv("ACCOUNTING_WORKER_DATABASE_URL", "")
    root = os.getenv("ACCOUNTING_ATTACHMENT_ROOT", "")
    if not url:
        parser.error("须显式配置 ACCOUNTING_WORKER_DATABASE_URL；原件存储须使用共享 Supabase 或本地持久目录")
    production = os.getenv("ACCOUNTING_ENV", "local").lower() == "production" or any(
        os.getenv(key, "").lower() in {"production", "prod"} for key in ("APP_ENV", "ENV", "ENVIRONMENT"))
    if production and (not url.startswith(("postgres://", "postgresql://")) or
                       parse_qs(urlsplit(url).query).get("sslmode", [""])[0] not in {"require", "verify-ca", "verify-full"}):
        parser.error("生产助手须使用独立PostgreSQL worker账号与显式TLS配置")
    worker_started = False
    worker = None
    try:
        db = Database(url)
        storage = create_attachment_store(root)
        storage.verify_private()
        intelligence = IntelligenceService(db, storage, provider=OpenAICompatibleProvider(AISettings.from_env()))
        ledger, business = intelligence.ledger, intelligence.business
        migration = LegacyMigrationService(db, storage)
        ledger.legacy_settlements = LegacySettlementService(db, migration, ledger)
        provider = AssistantProvider(intelligence.provider)
        service = AssistantService(db, ledger, business, agent_workspace=AgentWorkspaceService(db, ledger, business, None, migration),
                                   provider=provider, intelligence=intelligence)
        worker = AssistantWorker(service, provider, AssistantMeter(db, MeterService(db)))
        if args.once:
            try:
                worker.monitor.tick()
                worker.run_once()
            finally:
                worker.monitor.tick(state="stopped")
            return 0
        stopped = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        worker.start()
        worker_started = True
        if ready_callback:
            ready_callback()
        while not stopped.wait(1):
            pass
        return 0
    except Exception:
        logging.error("会计助手后台启动或执行失败，请检查独立账号、迁移与模型配置；连接及资料内容已隐藏")
        return 1
    finally:
        if worker_started:
            worker.stop()


if __name__ == "__main__":
    raise SystemExit(main())
