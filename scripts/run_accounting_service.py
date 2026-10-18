"""Container entrypoint: one service role per process, no implicit migrations."""

from __future__ import annotations

import argparse
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import threading
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nexus_backend"))

ROLES = ("api", "worker", "assistant", "migration", "maintenance")
WORKER_MODULES = {
    "worker": "app.jobs.worker",
    "assistant": "run_accounting_assistant",
    "migration": "app.legacy_migration.worker",
    "maintenance": "app.maintenance_worker",
}


class WorkerHealthProbe:
    """Private platform readiness endpoint, opened after worker initialization.

    This is process readiness, not a claim that every company/model/task is
    healthy. Detailed queue heartbeats and exception alarms remain in the app.
    """

    def __init__(self, port: int):
        self.port = port
        self._server = None
        self._thread = None

    def start(self) -> None:
        if self._server is not None:
            return

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/healthz":
                    self.send_error(404)
                    return
                body = b'{"status":"process_running"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                # Platform probes should not create request or identity logs.
                return

        server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="worker-health", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None


def validate_runtime(role: str, env: Mapping[str, str]) -> int:
    """Fail closed on deployment mistakes; errors contain names, never values."""
    if role not in ROLES:
        raise ValueError(
            "ACCOUNTING_SERVICE_ROLE 必须为 api、worker、assistant、migration 或 maintenance"
        )
    if env.get("ACCOUNTING_ADMIN_DATABASE_URL", "").strip():
        raise ValueError("运行容器禁止配置 ACCOUNTING_ADMIN_DATABASE_URL；迁移只在独立维护步骤执行")
    if env.get("ACCOUNTING_QUEUE_EMBEDDED_WORKER", "0") != "0":
        raise ValueError("独立容器部署必须设置 ACCOUNTING_QUEUE_EMBEDDED_WORKER=0")
    if env.get("ACCOUNTING_SEED", "0") != "0":
        raise ValueError("真实登录部署必须设置 ACCOUNTING_SEED=0")
    if env.get("ACCOUNTING_ENV", "").strip().lower() != "production":
        raise ValueError("云端服务入口要求 ACCOUNTING_ENV=production；本地示例继续使用原启动方式")
    key = (
        "ACCOUNTING_DATABASE_URL"
        if role == "api"
        else "ACCOUNTING_MAINTENANCE_DATABASE_URL"
        if role == "maintenance" and env.get("ACCOUNTING_MAINTENANCE_DATABASE_URL", "").strip()
        else "ACCOUNTING_WORKER_DATABASE_URL"
    )
    url = env.get(key, "").strip()
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in {"postgres", "postgresql"} and bool(
            parsed.hostname and parsed.username
        )
        valid = valid and parse_qs(parsed.query).get("sslmode", [""])[0] in {
            "require",
            "verify-ca",
            "verify-full",
        }
        _ = parsed.port
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError(
            f"{key} 必须为受限 PostgreSQL 账号连接，显式启用 sslmode=require 或证书校验"
        )
    other = "ACCOUNTING_WORKER_DATABASE_URL" if role == "api" else "ACCOUNTING_DATABASE_URL"
    if env.get(other, "").strip():
        raise ValueError(f"当前容器只应配置 {key}；移除不属于该服务的 {other}")
    if env.get("ACCOUNTING_STORAGE_BACKEND", "local").strip().lower() != "supabase":
        raise ValueError("此无共享磁盘部署要求 ACCOUNTING_STORAGE_BACKEND=supabase")
    if role == "api" and env.get("ACCOUNTING_AUTH_MODE", "").strip().lower() != "supabase":
        raise ValueError("API 要求 ACCOUNTING_AUTH_MODE=supabase")
    try:
        port = int(env.get("PORT", "8080"))
    except ValueError:
        raise ValueError("PORT 必须为 1 至 65535 的整数") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT 必须为 1 至 65535 的整数")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vercel + Zeabur + Supabase 会计服务入口")
    parser.add_argument("--role", choices=ROLES, help="覆盖 ACCOUNTING_SERVICE_ROLE")
    parser.add_argument("--env-file", help="仅显式加载该配置文件，不加载旧平台 .env")
    parser.add_argument("--once", action="store_true", help="worker 最多运行一轮；API 不接受该参数")
    args = parser.parse_args(argv)
    if args.env_file:
        path = Path(args.env_file).resolve()
        if not path.is_file():
            parser.error("指定的配置文件不存在")
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    role = args.role or os.getenv("ACCOUNTING_SERVICE_ROLE", "api").strip().lower()
    if role == "api" and args.once:
        parser.error("--once 仅用于后台服务")
    try:
        port = validate_runtime(role, os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if role == "api":
        import uvicorn

        # Import string defers application configuration to the serving process.
        uvicorn.run("app.main:app", host="0.0.0.0", port=port, workers=1, proxy_headers=False)
        return 0
    # Existing worker CLIs own signals, leases and recovery. No subprocess shell
    # or supervisor: SIGTERM reaches the worker process running as container PID 1.
    sys.path.insert(0, str(ROOT / "scripts"))
    previous = sys.argv
    sys.argv = [WORKER_MODULES[role], *(["--once"] if args.once else [])]
    probe = WorkerHealthProbe(port) if not args.once else None
    try:
        return int(
            importlib.import_module(WORKER_MODULES[role]).main(
                ready_callback=probe.start if probe is not None else None
            )
            or 0
        )
    finally:
        if probe is not None:
            probe.stop()
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
