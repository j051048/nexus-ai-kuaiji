"""Create disposable local recovery clusters; never reset an existing database.

Windows/WSL only. Credentials stay in owner-only local files. Repeating
``new-pair`` creates fresh source/target databases, retaining failed evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from urllib.parse import quote
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".codex/c01-final-pg"
LAYOUT = {
    "source16": {"major": 16, "port": 25496, "kind": "source"},
    "target16": {"major": 16, "port": 25497, "kind": "target"},
    "source17": {"major": 17, "port": 25498, "kind": "source"},
    "target17": {"major": 17, "port": 25499, "kind": "target"},
}


class RecoveryEnvironmentError(ValueError):
    pass


def validate_layout(value, *, occupied=()):
    if value != LAYOUT or any(
        type(row.get("major")) is not int or type(row.get("port")) is not int
        for row in value.values()
    ):
        raise RecoveryEnvironmentError("Four explicit independent PG16/17 clusters required")
    if set(occupied) & {row["port"] for row in LAYOUT.values()}:
        raise RecoveryEnvironmentError("Required local port occupied; no server will be adopted")
    return value


def validate_empty_target(objects, schemas, functions):
    if any(type(value) is not int or value != 0 for value in (objects, schemas, functions)):
        raise RecoveryEnvironmentError("Recovery requires an empty target; never reset or drop it")


def wsl_path(path):
    value = str(Path(path).resolve()).replace("\\", "/")
    if len(value) < 3 or value[1:3] != ":/":
        raise RecoveryEnvironmentError("Explicit Windows workspace path required")
    return "/mnt/" + value[0].lower() + "/" + value[3:]


def secure(path):
    sys.path[:0] = [str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests")]
    from c01_identity_fixture import _secure

    _secure(path)


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    secure(path.parent)
    # Atomic replacement is allowed only for this tool's explicit private config.
    staging = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    staging.write_text(json.dumps(data, indent=2), encoding="utf-8")
    secure(staging)
    os.replace(staging, path)


def local_url(spec, database, username="p01_admin", password=None):
    return f"postgresql://{username}:{quote(password or spec['password'], safe='')}@127.0.0.1:{spec['port']}/{database}"


def _bootstrap_linux(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    validate_layout(
        {
            key: {field: row[field] for field in ("major", "port", "kind")}
            for key, row in request["clusters"].items()
        }
    )
    root = Path(request["root"])
    approved = Path.home() / ".local/share/nexus-local-tests"
    if root.parent != approved or not root.name.startswith("p01-recovery-") or root.exists():
        raise RecoveryEnvironmentError("New owned local test root required")
    sockets = []
    try:
        for row in LAYOUT.values():
            sock = socket.socket()
            sock.bind(("127.0.0.1", row["port"]))
            sockets.append(sock)
    finally:
        for sock in sockets:
            sock.close()
    root.mkdir(mode=0o700)
    for name, row in request["clusters"].items():
        binary = Path(f"/usr/lib/postgresql/{row['major']}/bin")
        version = subprocess.run(
            [str(binary / "pg_ctl"), "--version"], check=True, capture_output=True, text=True
        ).stdout
        if f" {row['major']}." not in version:
            raise RecoveryEnvironmentError("PostgreSQL binary version mismatch")
        directory = root / name
        directory.mkdir(mode=0o700)
        password_file = directory / "init.password"
        password_file.write_text(row["password"], encoding="utf-8")
        password_file.chmod(0o600)
        try:
            subprocess.run(
                [
                    str(binary / "initdb"),
                    "-D",
                    str(directory / "data"),
                    "-U",
                    "p01_admin",
                    "--pwfile",
                    str(password_file),
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C.UTF-8",
                ],
                check=True,
                capture_output=True,
            )
        finally:
            password_file.unlink()
        with (directory / "data/postgresql.conf").open("a") as stream:
            stream.write(
                f"\nlisten_addresses='127.0.0.1'\nport={row['port']}\nunix_socket_directories='{directory}'\npassword_encryption='scram-sha-256'\nfsync=on\nsynchronous_commit=on\nfull_page_writes=on\n"
            )
        subprocess.run(
            [
                str(binary / "pg_ctl"),
                "-D",
                str(directory / "data"),
                "-l",
                str(directory / "postgres.log"),
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
    auth_migrations = Path.home() / ".local/share/nexus-local-tests/gotrue-v2.197.0/migrations"
    if auth_migrations.is_dir():
        manifest = [
            {
                "file": str(path.relative_to(auth_migrations)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(auth_migrations.rglob("*"))
            if path.is_file()
        ]
        manifest_path = Path(request_path).parent / "gotrue-migrations-manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream)
        manifest_path.chmod(0o600)


def prepare():
    if PRIVATE.exists():
        raise RecoveryEnvironmentError(
            "Private setup already exists; use new-pair, never overwrite another setup"
        )
    PRIVATE.mkdir(parents=True)
    secure(PRIVATE)
    request = {
        "root": f"/home/flydao/.local/share/nexus-local-tests/p01-recovery-{uuid4().hex[:12]}",
        "clusters": {
            name: {**row, "password": secrets.token_urlsafe(32)} for name, row in LAYOUT.items()
        },
    }
    write_private(PRIVATE / "environment-private.json", request)
    # WSL may reject child invocations from a Python process on Windows. Give an
    # explicit host-shell command; no secret is embedded in argv or console.
    print("Private plan created. Run from PowerShell, then new-pair 16 and 17:")
    print(
        'wsl -d Ubuntu-22.04 -- python3 "'
        + wsl_path(Path(__file__))
        + '" bootstrap-private "'
        + wsl_path(PRIVATE / "environment-private.json")
        + '"'
    )


def new_pair(major):
    if major not in (16, 17):
        raise RecoveryEnvironmentError("Only PG16/17")
    sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend")]
    import psycopg
    from psycopg import sql
    from scripts.migrate_accounting import (
        migrate,
        provision_login,
        provision_worker_login,
        provision_projection_login,
    )
    from scripts.ak05_postgres_gate import assert_server_and_roles

    request = json.loads((PRIVATE / "environment-private.json").read_text())
    validate_layout(
        {
            key: {field: row[field] for field in ("major", "port", "kind")}
            for key, row in request["clusters"].items()
        }
    )
    run = uuid4().hex[:12]
    configs = {}
    for kind in ("source", "target"):
        row = request["clusters"][f"{kind}{major}"]
        database = f"ak01_local_ak05_p01_{kind}_{major}_{run}"
        admin = local_url(row, database)
        with psycopg.connect(local_url(row, "postgres"), autocommit=True) as conn:
            if int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 != major:
                raise RecoveryEnvironmentError("Local server version mismatch")
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER p01_admin").format(sql.Identifier(database))
            )
        if kind == "source":
            migrate(admin)
        else:
            with psycopg.connect(admin) as conn:
                for capability in (
                    "accounting_app",
                    "accounting_system",
                    "accounting_worker",
                    "accounting_projection",
                ):
                    if not conn.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname=%s", (capability,)
                    ).fetchone():
                        conn.execute(
                            sql.SQL(
                                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                            ).format(sql.Identifier(capability))
                        )
        config = {"admin": admin}
        for role, login, provision in (
            ("app", "ak05_runtime", provision_login),
            ("worker", "ak05_worker", provision_worker_login),
            ("projection", "ak05_projection", provision_projection_login),
        ):
            password_key = role + "_password"
            if password_key not in row:
                row[password_key] = secrets.token_urlsafe(32)
                write_private(PRIVATE / "environment-private.json", request)
                provision(admin, login, row[password_key])
            config[role] = local_url(row, database, login, row[password_key])
        assert_server_and_roles(config, major)
        if kind == "target":
            with psycopg.connect(admin) as conn:
                objects = conn.execute(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'"
                ).fetchone()[0]
                schemas = conn.execute(
                    "SELECT count(*) FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname NOT IN ('public','information_schema')"
                ).fetchone()[0]
                functions = conn.execute(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'"
                ).fetchone()[0]
                validate_empty_target(objects, schemas, functions)
        name = (
            f"pg{major}-recovery_source.json"
            if kind == "source"
            else f"pg{major}-recovery-fresh-target.json"
        )
        write_private(PRIVATE / name, config)
        configs[f"{kind}{major}"] = {
            "url": admin,
            "binary_directory": f"/usr/lib/postgresql/{major}/bin",
        }
    bridge_path = PRIVATE / "wsl-bridge-private.json"
    bridge = (
        json.loads(bridge_path.read_text())
        if bridge_path.exists()
        else {
            "token": secrets.token_urlsafe(48),
            "targets": {},
            "binary_directories": ["/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin"],
            "library": "/usr/lib/x86_64-linux-gnu",
            "artifact_roots": [str(ROOT / "tmp_test")],
            "gotrue_binary": "/home/flydao/.local/share/nexus-local-tests/gotrue-v2.197.0/auth",
            "gotrue_migrations": "/home/flydao/.local/share/nexus-local-tests/gotrue-v2.197.0/migrations",
            "gotrue_migrations_manifest": str(PRIVATE / "gotrue-migrations-manifest.json"),
        }
    )
    bridge["targets"].update(configs)
    write_private(bridge_path, bridge)
    print(
        f"PG{major}: fresh source and empty target ready; restricted four-role configs written privately"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "new-pair", "bootstrap-private"))
    parser.add_argument("argument", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "bootstrap-private":
            _bootstrap_linux(args.argument)
        elif args.command == "prepare":
            prepare()
        else:
            new_pair(int(args.argument))
    except Exception:
        # Neither subprocess exceptions nor DSNs are safe for console output.
        print(
            "Local recovery environment operation failed; no existing database was dropped or reset",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
