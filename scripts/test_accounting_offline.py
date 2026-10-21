"""Run synthetic accounting tests with private temp storage and no external sockets.

Use the repository virtualenv. PostgreSQL integration has its own isolated runner;
this entry point deliberately removes inherited database/model credentials.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
from uuid import uuid4


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "tmp_test" / "offline-pytest" / uuid4().hex
    scratch = artifacts / "scratch"
    scratch.mkdir(parents=True)
    # SQLite may create statement/savepoint journals even for a tiny fixture.
    # Choose a writable process-private directory before importing sqlite/pytest.
    for key in ("TEMP", "TMP", "TMPDIR", "SQLITE_TMPDIR"):
        os.environ[key] = str(scratch)
    for name in list(os.environ):
        if name.upper().startswith(
            ("ACCOUNTING_", "SUPABASE_", "OPENAI_", "DEEPSEEK_", "ZEABUR_", "VERCEL_")
        ) or name.upper() in {
            "DATABASE_URL", "PGHOST", "PGPASSWORD", "PGSERVICE", "PGSERVICEFILE",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        }:
            os.environ.pop(name, None)
    os.environ["ACCOUNTING_AUTH_MODE"] = "local"
    os.environ["ACCOUNTING_DATABASE_PATH"] = str(artifacts / "unused-default.sqlite")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path[:0] = [str(root / "nexus_backend"), str(root / "nexus_backend" / "tests"), str(root)]

    connect, connect_ex, getaddrinfo = (
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    )
    allowed_hosts = {"127.0.0.1", "::1", "localhost"}

    def check(address):
        if isinstance(address, tuple) and str(address[0]).lower() not in allowed_hosts:
            raise RuntimeError("Offline test blocked external socket")

    def local_connect(instance, address):
        check(address)
        return connect(instance, address)

    def local_connect_ex(instance, address):
        check(address)
        return connect_ex(instance, address)

    def local_dns(host, *args, **kwargs):
        value = host.decode("ascii") if isinstance(host, bytes) else host
        if value is not None and str(value).lower() not in allowed_hosts:
            raise RuntimeError("Offline test blocked external DNS")
        return getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = local_connect
    socket.socket.connect_ex = local_connect_ex
    socket.getaddrinfo = local_dns
    args = sys.argv[1:]
    # Let pytest own only a fresh task directory, never a user's existing path.
    if any(arg == "--basetemp" or arg.startswith("--basetemp=") for arg in args):
        raise SystemExit("This runner supplies its own fresh --basetemp directory.")
    args.extend(["--basetemp", str(artifacts / "pytest")])
    if not any(arg.startswith(("--junitxml", "--junit-xml")) for arg in args):
        args.extend(["--junitxml", str(artifacts / "results.xml")])
    print(f"Offline test artifacts: {artifacts}", flush=True)
    import pytest
    return pytest.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
