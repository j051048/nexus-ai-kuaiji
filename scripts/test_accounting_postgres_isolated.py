"""Run explicit tests against an already provisioned disposable loopback PG.

Configuration is a local JSON file with admin/app/worker DSNs. Credentials are
not logged. This runner never provisions, migrates, drops or contacts cloud DBs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args, pytest_args = parser.parse_known_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    target_keys = ["admin", "app", "worker"] + (["projection"] if config.get("projection") else [])
    targets = [urlsplit(config[key]) for key in target_keys]
    for target in targets:
        if (
            target.scheme not in {"postgres", "postgresql"}
            or target.hostname != "127.0.0.1"
            or not target.port
            or target.port == 5432
            or not target.path.startswith("/ak01_local_ak05_")
            or target.query
            or target.fragment
        ):
            parser.error("仅允许独立端口的显式AK05本地临时数据库")
    if len({(t.hostname, t.port, t.path) for t in targets}) != 1:
        parser.error("三个身份必须指向同一个本地隔离库")
    for name in list(os.environ):
        if name.upper().startswith(
            ("ACCOUNTING_", "SUPABASE_", "OPENAI_", "DEEPSEEK_", "ZEABUR_", "VERCEL_", "PG")
        ) or name.upper() in {"DATABASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            os.environ.pop(name, None)
    os.environ.update(
        ACCOUNTING_TEST_POSTGRES_URL=config["admin"],
        ACCOUNTING_TEST_DATABASE_URL=config["app"],
        ACCOUNTING_TEST_WORKER_DATABASE_URL=config["worker"],
        ACCOUNTING_AK01_ISOLATED_TARGET="1",
        ACCOUNTING_AK05_ISOLATED_TARGET="1",
        ACCOUNTING_ENV="test",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if config.get("projection"):
        os.environ["ACCOUNTING_PROJECTION_DATABASE_URL"] = config["projection"]
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "nexus_backend"), str(root / "nexus_backend/tests"), str(root)]
    import psycopg

    connect = psycopg.connect

    def isolated_connect(conninfo="", *values, **kwargs):
        if conninfo not in {config[key] for key in target_keys} or set(kwargs) & {
            "host",
            "hostaddr",
            "port",
            "dbname",
            "user",
            "password",
            "service",
            "servicefile",
        }:
            raise RuntimeError("PostgreSQL test rejected another database")
        kwargs.setdefault("connect_timeout", 5)
        return connect(conninfo, *values, **kwargs)

    psycopg.connect = isolated_connect
    real_connect, real_dns = socket.socket.connect, socket.getaddrinfo

    def local_socket(instance, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("PostgreSQL test blocked external socket")
        return real_connect(instance, address)

    def local_dns(host, *values, **kwargs):
        if host not in {None, "127.0.0.1", "::1", "localhost", b"127.0.0.1"}:
            raise RuntimeError("PostgreSQL test blocked external DNS")
        return real_dns(host, *values, **kwargs)

    socket.socket.connect = local_socket
    socket.getaddrinfo = local_dns
    artifact = root / "tmp_test/ak05-pg-runs" / uuid4().hex
    artifact.mkdir(parents=True)
    if any(value.startswith("--basetemp") for value in pytest_args):
        parser.error("隔离运行器提供独立的临时目录")
    pytest_args += ["--basetemp", str(artifact / "pytest")]
    if not any(value.startswith("--junit") for value in pytest_args):
        pytest_args += ["--junitxml", str(artifact / "results.xml")]
    print("AK05 PostgreSQL artifacts:", artifact, flush=True)
    import pytest

    return pytest.main(pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
