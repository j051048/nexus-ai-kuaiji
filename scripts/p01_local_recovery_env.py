"""Owned Linux/WSL recovery environments; immutable runs, no database deletion.

Run prepare/bootstrap/start/status/stop on the PostgreSQL host (Linux or WSL).
new-pair may also run on Windows against those explicit loopback clusters.
No operation downloads software, adopts another server, or resets an old run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / ".codex/p01-environments"
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
        raise RecoveryEnvironmentError("layout_invalid: four independent PG16/17 clusters required")
    if set(occupied) & {row["port"] for row in LAYOUT.values()}:
        raise RecoveryEnvironmentError("port_occupied: another server must not be adopted")
    return value


def validate_empty_target(objects, schemas, functions):
    if any(type(value) is not int or value != 0 for value in (objects, schemas, functions)):
        raise RecoveryEnvironmentError("target_not_empty: never reset or drop a recovery target")


def secure(path):
    if os.name == "nt":
        sys.path[:0] = [str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests")]
        from c01_identity_fixture import _secure

        _secure(path)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def write_new_private(path, data):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise RecoveryEnvironmentError(
            "config_exists: immutable configuration cannot be overwritten"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    secure(path.parent)
    # O_EXCL and 0600 protect the first byte, including on shared Linux runners.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RecoveryEnvironmentError(
            "config_exists: immutable configuration cannot be overwritten"
        ) from None
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
    secure(path)


# Compatibility helper is now deliberately write-once.
write_private = write_new_private


def make_plan(runtime_base, pg_bin_root, gotrue_root):
    identifier = uuid4().hex
    return {
        "schema": "p01-environment-v2",
        "environment_id": identifier,
        "root": str(Path(runtime_base).resolve() / ("p01-recovery-" + identifier)),
        "pg_bin_root": str(Path(pg_bin_root).resolve()),
        "gotrue_root": str(Path(gotrue_root).resolve()),
        "clusters": {
            name: {
                **row,
                "password": secrets.token_urlsafe(32),
                **{
                    role + "_password": secrets.token_urlsafe(32)
                    for role in ("app", "worker", "projection")
                },
            }
            for name, row in LAYOUT.items()
        },
    }


def read_plan(directory):
    path = Path(directory) / "environment-private.json"
    if path.is_symlink():
        raise RecoveryEnvironmentError("unsafe_path: linked environment plan")
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    if plan.get("schema") != "p01-environment-v2" or not re.fullmatch(
        "[a-f0-9]{32}", plan.get("environment_id", "")
    ):
        raise RecoveryEnvironmentError(
            "legacy_environment: require a new v2 environment, preserve old files"
        )
    if Path(plan["root"]).name != "p01-recovery-" + plan["environment_id"]:
        raise RecoveryEnvironmentError("owner_mismatch: unexpected environment directory")
    validate_layout(
        {
            key: {field: row[field] for field in ("major", "port", "kind")}
            for key, row in plan["clusters"].items()
        }
    )
    return plan


def occupied_ports():
    occupied = []
    for port in (25496, 25497, 25498, 25499, 25510, 25511, 25512):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                occupied.append(port)
    return occupied


def validate_owned_cluster(plan, name):
    if name not in LAYOUT:
        raise RecoveryEnvironmentError("owner_mismatch: unknown cluster")
    root = Path(plan["root"])
    data = root / name / "data"
    if root.is_symlink() or data.is_symlink() or data.resolve() != root.resolve() / name / "data":
        raise RecoveryEnvironmentError("unsafe_path: cluster escapes owned root")
    marker = root / "owner.json"
    if (
        marker.is_symlink()
        or json.loads(marker.read_text()).get("environment_id") != plan["environment_id"]
    ):
        raise RecoveryEnvironmentError("owner_mismatch: environment marker differs")
    if os.name != "nt" and (root.stat().st_uid != os.getuid() or data.stat().st_uid != os.getuid()):
        raise RecoveryEnvironmentError("owner_mismatch: cluster belongs to another OS account")
    row = LAYOUT[name]
    if (data / "PG_VERSION").read_text().strip() != str(row["major"]):
        raise RecoveryEnvironmentError("version_mismatch: owned cluster version differs")
    conf = (data / "postgresql.conf").read_text()
    ports = re.findall(r"^\s*port\s*=\s*(\d+)\s*(?:#.*)?$", conf, re.M)
    addresses = re.findall(r"^\s*listen_addresses\s*=\s*'([^']*)'", conf, re.M)
    if not ports or int(ports[-1]) != row["port"] or not addresses or addresses[-1] != "127.0.0.1":
        raise RecoveryEnvironmentError("address_mismatch: owned cluster port or listener changed")
    return data


def _native():
    if os.name == "nt":
        raise RecoveryEnvironmentError(
            "runtime_host_required: run this operation inside WSL or Linux"
        )


def _run(args, timeout=90, log=None):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RecoveryEnvironmentError(
            "process_timeout: inspect the private environment logs"
        ) from None
    if log is not None:
        with Path(log).open("xb") as stream:
            secure(Path(log))
            stream.write(result.stdout + result.stderr)
    if result.returncode:
        raise RecoveryEnvironmentError("process_failed: inspect the private environment logs")
    return result


def _binary(plan, major, executable):
    directory = Path(plan["pg_bin_root"]) / str(major) / "bin"
    binary = directory / executable
    version = _run([str(directory / "pg_ctl"), "--version"]).stdout.decode()
    if f" {major}." not in version:
        raise RecoveryEnvironmentError("version_mismatch: PostgreSQL executable")
    return binary


def _owned_pid(data, expected_port):
    pidfile = data / "postmaster.pid"
    if not pidfile.exists():
        return None
    lines = pidfile.read_text().splitlines()
    try:
        pid = int(lines[0])
        if Path(lines[1]).resolve() != data.resolve() or int(lines[3]) != expected_port:
            raise ValueError
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if os.fsencode(data) not in cmdline or b"-D" not in cmdline:
            raise ValueError
    except (ValueError, IndexError, FileNotFoundError):
        raise RecoveryEnvironmentError(
            "pid_mismatch: refusing to signal stale or foreign PID"
        ) from None
    return pid


def prepare(directory, runtime_base=None, pg_bin_root="/usr/lib/postgresql", gotrue_root=None):
    _native()
    directory = Path(directory).resolve()
    if directory.exists():
        raise RecoveryEnvironmentError("config_exists: choose a fresh environment directory")
    plan = make_plan(
        runtime_base or Path.home() / ".local/share/nexus-local-tests",
        pg_bin_root,
        gotrue_root or Path.home() / ".local/share/nexus-local-tests/gotrue-v2.197.0",
    )
    directory.mkdir(parents=True, mode=0o700)
    secure(directory)
    write_new_private(directory / "environment-private.json", plan)
    return {"prepared": True, "environment_id": plan["environment_id"]}


def postgres_settings(port):
    if type(port) is not int or port not in {row["port"] for row in LAYOUT.values()}:
        raise RecoveryEnvironmentError("port_invalid: isolated cluster port required")
    return (
        f"\nlisten_addresses='127.0.0.1'\nport={port}\nunix_socket_directories=''"
        "\npassword_encryption='scram-sha-256'\nfsync=on\nsynchronous_commit=on\nfull_page_writes=on\n"
    )


def bootstrap(directory):
    _native()
    plan = read_plan(directory)
    root = Path(plan["root"])
    if root.exists():
        raise RecoveryEnvironmentError("root_exists: use start; do not reinitialize")
    busy = occupied_ports()
    if busy:
        raise RecoveryEnvironmentError("port_occupied: " + ",".join(map(str, busy)))
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    write_new_private(root / "owner.json", {"environment_id": plan["environment_id"]})
    for name, row in plan["clusters"].items():
        folder = root / name
        folder.mkdir(mode=0o700)
        password_file = folder / "init.password"
        with password_file.open("x") as stream:
            password_file.chmod(0o600)
            stream.write(row["password"])
        try:
            _run(
                [
                    str(_binary(plan, row["major"], "initdb")),
                    "-D",
                    str(folder / "data"),
                    "-U",
                    "p01_admin",
                    "--pwfile",
                    str(password_file),
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    "--encoding=UTF8",
                    "--locale=C.UTF-8",
                ],
                log=folder / "initdb.log",
            )
        finally:
            password_file.unlink(missing_ok=True)
        with (folder / "data/postgresql.conf").open("a") as stream:
            stream.write(postgres_settings(row["port"]))
    # Freeze the approved local assets on their native host; a Windows caller
    # can later provision new DB pairs without inventing Linux filesystem paths.
    auth = Path(plan["gotrue_root"])
    from scripts.c01_local_wsl_bridge import AUTH_SHA

    if hashlib.sha256((auth / "auth").read_bytes()).hexdigest() != AUTH_SHA:
        raise RecoveryEnvironmentError("auth_digest_mismatch: pinned official binary required")
    manifest = [
        {
            "file": p.relative_to(auth / "migrations").as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted((auth / "migrations").rglob("*"))
        if p.is_file()
    ]
    if not manifest:
        raise RecoveryEnvironmentError("auth_migrations_missing")
    write_new_private(
        Path(directory) / "gotrue-assets-private.json",
        {"binary_sha256": AUTH_SHA, "migrations": manifest},
    )
    return lifecycle(directory, "start")


def lifecycle(directory, operation):
    _native()
    plan = read_plan(directory)
    if operation != "status" and list((Path(directory) / "runs").glob("*/gate-active.json")):
        raise RecoveryEnvironmentError(
            "gate_active: finish the gate before changing cluster lifecycle"
        )
    # Serialize lifecycle tools; TCP probing is advisory, never an ownership proof.
    import fcntl

    lock_path = Path(directory) / "lifecycle.lock"
    with lock_path.open("a") as lock:
        secure(lock_path)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryEnvironmentError(
                "environment_busy: another lifecycle operation is active"
            ) from None
        plans = [(name, validate_owned_cluster(plan, name)) for name in LAYOUT]
        state = {}
        for name, data in plans:
            row = LAYOUT[name]
            pid = _owned_pid(data, row["port"])
            if operation == "start" and pid is None:
                if row["port"] in occupied_ports():
                    raise RecoveryEnvironmentError("port_occupied: refusing to adopt listener")
                _run(
                    [
                        str(_binary(plan, row["major"], "pg_ctl")),
                        "-D",
                        str(data),
                        "-l",
                        str(data.parent / "postgres.log"),
                        "-w",
                        "start",
                    ]
                )
                # pg_ctl must actually have created the exact owned postmaster.
                pid = _owned_pid(data, row["port"])
                if pid is None:
                    raise RecoveryEnvironmentError("start_unverified: owned PID missing")
            elif operation == "stop" and pid is not None:
                _run(
                    [
                        str(_binary(plan, row["major"], "pg_ctl")),
                        "-D",
                        str(data),
                        "-m",
                        "fast",
                        "-w",
                        "stop",
                    ]
                )
                pid = _owned_pid(data, row["port"])
            state[name] = {"major": row["major"], "port": row["port"], "running": pid is not None}
        return {"environment_id": plan["environment_id"], "clusters": state, "data_deleted": False}


def local_url(spec, database, username="p01_admin", password=None):
    return f"postgresql://{username}:{quote(password or spec['password'], safe='')}@127.0.0.1:{spec['port']}/{database}"


def needs_login_provision(existing):
    if existing and existing != {"ak05_runtime", "ak05_worker", "ak05_projection"}:
        raise RecoveryEnvironmentError(
            "role_inventory: incomplete test roles; never change existing grants"
        )
    return not existing


def new_pair(directory):
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
    from scripts.c01_local_wsl_bridge import AUTH_SHA

    plan = read_plan(directory)
    run = Path(directory).resolve() / "runs" / uuid4().hex
    run.mkdir(parents=True, mode=0o700)
    secure(run)
    bridge_targets = {}
    for name, row in plan["clusters"].items():
        database = f"ak01_local_ak05_p01_{name}_{run.name}"
        admin = local_url(row, database)
        with psycopg.connect(
            local_url(row, "postgres"), connect_timeout=5, autocommit=True
        ) as conn:
            if int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 != row["major"]:
                raise RecoveryEnvironmentError("version_mismatch: explicit local target")
            existing = {
                record[0]
                for record in conn.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s)",
                    (["ak05_runtime", "ak05_worker", "ak05_projection"],),
                )
            }
            create_logins = needs_login_provision(existing)
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER p01_admin").format(sql.Identifier(database))
            )
        if row["kind"] == "source":
            migrate(admin)
        else:
            with psycopg.connect(admin) as conn:
                for role in (
                    "accounting_app",
                    "accounting_system",
                    "accounting_worker",
                    "accounting_projection",
                ):
                    if not conn.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
                    ).fetchone():
                        conn.execute(
                            sql.SQL(
                                "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                            ).format(sql.Identifier(role))
                        )
        config = {"admin": admin}
        for role, login, provision in (
            ("app", "ak05_runtime", provision_login),
            ("worker", "ak05_worker", provision_worker_login),
            ("projection", "ak05_projection", provision_projection_login),
        ):
            # Passwords belong to the immutable environment, never rotate an old run's roles.
            if create_logins:
                provision(admin, login, row[role + "_password"])
            config[role] = local_url(row, database, login, row[role + "_password"])
        assert_server_and_roles(config, row["major"])
        # Reusing a verified login never resets its password or memberships.
        # Authenticate all four roles before publishing the new immutable run.
        for url in config.values():
            with psycopg.connect(url, connect_timeout=5) as check:
                check.execute("SELECT 1")
        if row["kind"] == "target":
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
        suffix = "recovery_source" if row["kind"] == "source" else "recovery-fresh-target"
        write_new_private(run / f"pg{row['major']}-{suffix}.json", config)
        bridge_targets[name] = {
            "url": admin,
            "binary_directory": str(PurePosixPath(plan["pg_bin_root"]) / str(row["major"]) / "bin"),
        }
    assets = json.loads((Path(directory) / "gotrue-assets-private.json").read_text())
    if assets["binary_sha256"] != AUTH_SHA or not assets["migrations"]:
        raise RecoveryEnvironmentError(
            "auth_digest_mismatch: frozen native asset manifest required"
        )
    auth = PurePosixPath(plan["gotrue_root"])
    manifest = assets["migrations"]
    write_new_private(run / "gotrue-migrations-manifest.json", manifest)
    bridge = {
        "token": secrets.token_urlsafe(48),
        "targets": bridge_targets,
        "binary_directories": [
            str(PurePosixPath(plan["pg_bin_root"]) / str(m) / "bin") for m in (16, 17)
        ],
        "library": "/usr/lib/x86_64-linux-gnu",
        "artifact_roots": [str(ROOT / "tmp_test")],
        "gotrue_binary": str(auth / "auth"),
        "gotrue_migrations": str(auth / "migrations"),
        "gotrue_migrations_manifest": str(run / "gotrue-migrations-manifest.json"),
    }
    write_new_private(run / "wsl-bridge-private.json", bridge)
    write_new_private(
        run / "run.json",
        {"schema": "p01-run-v2", "environment_id": plan["environment_id"], "run_id": run.name},
    )
    return {"run_directory": str(run), "previous_runs_unchanged": True}


def bridge_start(config_dir):
    _native()
    config = Path(config_dir).resolve() / "wsl-bridge-private.json"
    if 25510 in occupied_ports():
        raise RecoveryEnvironmentError("bridge_occupied: never replace another run's bridge")
    log = config.parent / ("bridge-" + uuid4().hex + ".log")
    with log.open("xb") as output:
        secure(log)
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "scripts/c01_local_wsl_bridge.py"),
                "--config",
                str(config),
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    sys.path.insert(0, str(ROOT / "nexus_backend/tests"))
    from c01_wsl_bridge import call

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RecoveryEnvironmentError("bridge_start_failed: inspect private run log")
        try:
            state = call(config, "status", timeout=1)
            if state.get("configuration_sha256") != hashlib.sha256(config.read_bytes()).hexdigest():
                raise RecoveryEnvironmentError("bridge_identity_mismatch")
            return {"bridge_ready": True}
        except RuntimeError:
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise RecoveryEnvironmentError("bridge_timeout")


def bridge_stop(config_dir):
    sys.path.insert(0, str(ROOT / "nexus_backend/tests"))
    from c01_wsl_bridge import call

    config = Path(config_dir) / "wsl-bridge-private.json"
    if 25510 not in occupied_ports():
        return {"bridge_stopped": True, "already_stopped": True}
    result = call(config, "shutdown")
    deadline = time.monotonic() + 15
    while 25510 in occupied_ports() and time.monotonic() < deadline:
        time.sleep(0.1)
    if 25510 in occupied_ports():
        raise RecoveryEnvironmentError("bridge_stop_timeout: do not signal foreign processes")
    return result


def main():
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "bootstrap",
            "start",
            "status",
            "stop",
            "new-pair",
            "bridge-start",
            "bridge-stop",
        ),
    )
    parser.add_argument("--directory", required=True)
    parser.add_argument("--runtime-base")
    parser.add_argument("--pg-bin-root", default="/usr/lib/postgresql")
    parser.add_argument("--gotrue-root")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.directory, args.runtime_base, args.pg_bin_root, args.gotrue_root)
        elif args.command == "bootstrap":
            result = bootstrap(args.directory)
        elif args.command == "new-pair":
            result = new_pair(args.directory)
        elif args.command == "bridge-start":
            result = bridge_start(args.directory)
        elif args.command == "bridge-stop":
            result = bridge_stop(args.directory)
        else:
            result = lifecycle(args.directory, args.command)
        print(json.dumps(result))
    except Exception as error:
        code = (
            str(error).split(":")[0]
            if isinstance(error, RecoveryEnvironmentError)
            else type(error).__name__
        )
        print(
            json.dumps(
                {
                    "status": "failed",
                    "operation": args.command,
                    "code": code,
                    "next_action": "Inspect the private environment logs; preserve the run, never reset an old database.",
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
