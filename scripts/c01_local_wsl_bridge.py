"""Opt-in loopback-only test infrastructure; never an application endpoint.

Launch directly from PowerShell through WSL. A private configuration names every
permitted database, official binary and artifact root. No arbitrary argv/shell,
remote DSN, model, deployment or production operation is supported.
"""

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.identity_test_assets import asset_contract  # noqa: E402

AUTH_SHA = asset_contract()["binary_sha256"]


class BridgeError(ValueError):
    pass


def host_path(value):
    if not isinstance(value, str) or "\x00" in value:
        raise BridgeError("Invalid path")
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", value):
        value = "/mnt/" + value[0].lower() + "/" + value[3:].replace("\\", "/")
    return Path(value)


def confined_path(value, roots):
    path = host_path(value).resolve()
    if not any(path.is_relative_to(host_path(root).resolve()) for root in roots):
        raise BridgeError("Path outside approved test artifacts")
    return path


def database_target(target, database=None):
    url = urlsplit(target["url"])
    if (
        url.scheme not in {"postgres", "postgresql"}
        or url.hostname != "127.0.0.1"
        or url.port not in {25496, 25497, 25498, 25499}
        or url.query
        or url.fragment
        or not url.path.startswith("/ak01_local_ak05_")
        or not url.username
        or not url.password
    ):
        raise BridgeError("Only approved disposable loopback databases are allowed")
    name = url.path[1:]
    if database is not None:
        if not isinstance(database, str) or not re.fullmatch(
            r"ak01_local_ak05_auth_[a-f0-9]{32}", database
        ):
            raise BridgeError("Only test auth database derivation is allowed")
        name = database
    return {
        "PGHOST": "127.0.0.1",
        "PGPORT": str(url.port),
        "PGDATABASE": name,
        "PGUSER": unquote(url.username),
        "PGPASSWORD": unquote(url.password),
        "PGCONNECT_TIMEOUT": "8",
    }


def validate_snapshot(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", value):
        raise BridgeError("Invalid exported PostgreSQL snapshot")
    return value


class Bridge:
    def __init__(self, config_path):
        self.path = Path(config_path)
        self.configuration_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.token = self.config()["token"]
        if not isinstance(self.token, str) or len(self.token) < 40:
            raise BridgeError("Private random authentication token required")
        self.processes = {}
        self.lock = threading.Lock()

    def config(self):
        if (
            hasattr(self, "configuration_sha256")
            and hashlib.sha256(self.path.read_bytes()).hexdigest() != self.configuration_sha256
        ):
            raise BridgeError(
                "Immutable bridge configuration changed; restart explicitly for a new run"
            )
        return json.loads(self.path.read_text(encoding="utf-8-sig"))

    def execute(self, data):
        if not isinstance(data, dict):
            raise BridgeError("Object request required")
        op = data.get("op")
        if op not in {"pg_dump", "pg_restore", "start_gotrue", "stop_gotrue", "status", "shutdown"}:
            raise BridgeError("Operation is not allowed")
        allowed = {
            "pg_dump": {
                "op",
                "target",
                "database",
                "output",
                "schema",
                "snapshot",
                "no_owner",
                "no_privileges",
            },
            "pg_restore": {"op", "target", "database", "input"},
            "start_gotrue": {
                "op",
                "target",
                "database",
                "port",
                "jwt_secret",
                "origin",
                "log_path",
            },
            "stop_gotrue": {"op", "port"},
            "status": {"op"},
            "shutdown": {"op"},
        }[op]
        if set(data) - allowed:
            raise BridgeError("Unexpected operation parameter")
        if op == "status":
            self.config()
            return {
                "ready": True,
                "configuration_sha256": self.configuration_sha256,
                "auth_ports": [port for port, p in self.processes.items() if p.poll() is None],
            }
        if op == "shutdown":
            self.stop_all()
            return {"stopped": True}
        if op == "stop_gotrue":
            return self.stop(data.get("port"))
        config = self.config()
        target = config.get("targets", {}).get(data.get("target"))
        if not isinstance(target, dict):
            raise BridgeError("Unknown approved database target")
        pg = database_target(target, data.get("database"))
        binary_dir = host_path(target["binary_directory"]).resolve()
        if str(binary_dir) not in config["binary_directories"]:
            raise BridgeError("Unapproved PostgreSQL binary directory")
        env = {
            "PATH": os.defpath,
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "LD_LIBRARY_PATH": config["library"],
            **pg,
        }
        if op.startswith("pg_"):
            return self.pg(op, data, config, binary_dir, env)
        return self.start(data, config, target, pg, env)

    def pg(self, op, data, config, binary_dir, env):
        args = [str(binary_dir / op)]
        if op == "pg_dump":
            output = confined_path(data.get("output"), config["artifact_roots"])
            if output.exists() or not output.parent.is_dir():
                raise BridgeError("Dump destination must be new in an existing artifact directory")
            schema = data.get("schema", "accounting")
            if schema not in {"accounting", "auth"}:
                raise BridgeError("Unsupported test schema")
            if schema == "auth" and not env["PGDATABASE"].startswith("ak01_local_ak05_auth_"):
                raise BridgeError("Auth dump requires an approved auth database")
            args += ["--format=custom", "--schema=" + schema, "--no-password"]
            for key, flag in (("no_owner", "--no-owner"), ("no_privileges", "--no-privileges")):
                if key in data and type(data[key]) is not bool:
                    raise BridgeError("Boolean option required")
                if data.get(key):
                    args.append(flag)
            if data.get("snapshot") is not None:
                args += ["--snapshot", validate_snapshot(data["snapshot"])]
            args += ["--file", str(output)]
        else:
            source = confined_path(data.get("input"), config["artifact_roots"])
            if not source.is_file():
                raise BridgeError("Restore archive missing")
            with source.open("rb") as stream:
                if stream.read(5) != b"PGDMP":
                    raise BridgeError("Only custom PostgreSQL archives are allowed")
            # Preserve the product restore transaction and privilege semantics.
            args += [
                "--no-owner",
                "--no-privileges",
                "--single-transaction",
                "--exit-on-error",
                "--no-password",
                "--dbname",
                env["PGDATABASE"],
                str(source),
            ]
        result = subprocess.run(
            args, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=180
        )
        if result.returncode:
            raise BridgeError("Local PostgreSQL tool failed; no credentials or raw stderr returned")
        if op == "pg_dump":
            output.chmod(0o600)
            return {
                "completed": True,
                "size": output.stat().st_size,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        return {"completed": True}

    def start(self, data, config, target, pg, env):
        from scripts.p01_local_recovery_env import auth_ports

        port = data.get("port")
        if type(port) is not int or port not in auth_ports(config):
            raise BridgeError("Unapproved auth port")
        if not re.fullmatch(r"ak01_local_ak05_auth_[a-f0-9]{32}", pg["PGDATABASE"]):
            raise BridgeError("Auth requires a dedicated synthetic database")
        origin = data.get("origin")
        if origin != f"https://c01-real-auth-{port}.example.test":
            raise BridgeError("Unapproved synthetic identity origin")
        secret = data.get("jwt_secret")
        if not isinstance(secret, str) or not 32 <= len(secret) <= 256:
            raise BridgeError("Synthetic signing secret required")
        binary = host_path(config["gotrue_binary"]).resolve()
        if hashlib.sha256(binary.read_bytes()).hexdigest() != AUTH_SHA:
            raise BridgeError("Official fixed auth binary hash mismatch")
        migrations = host_path(config["gotrue_migrations"]).resolve()
        manifest = json.loads(host_path(config["gotrue_migrations_manifest"]).read_text())
        for row in manifest:
            path = (migrations / row["file"]).resolve()
            if (
                not path.is_relative_to(migrations)
                or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]
            ):
                raise BridgeError("Official auth migration integrity mismatch")
        log = confined_path(data.get("log_path"), config["artifact_roots"])
        if log.exists() or not log.parent.is_dir():
            raise BridgeError("Auth log destination must be new")
        from urllib.parse import quote

        database_url = (
            "postgresql://"
            + quote(pg["PGUSER"], safe="")
            + ":"
            + quote(pg["PGPASSWORD"], safe="")
            + "@127.0.0.1:"
            + pg["PGPORT"]
            + "/"
            + pg["PGDATABASE"]
            + "?sslmode=disable"
        )
        env.update(
            {
                "GOTRUE_DB_DRIVER": "postgres",
                "GOTRUE_DB_DATABASE_URL": database_url,
                "GOTRUE_DB_NAMESPACE": "auth",
                "GOTRUE_DB_MIGRATIONS_PATH": str(migrations),
                "GOTRUE_API_HOST": "127.0.0.1",
                "GOTRUE_API_PORT": str(port),
                "API_EXTERNAL_URL": origin + "/auth/v1",
                "GOTRUE_JWT_ISSUER": origin + "/auth/v1",
                "GOTRUE_SITE_URL": origin,
                "GOTRUE_JWT_SECRET": secret,
                "GOTRUE_JWT_AUD": "authenticated",
                "GOTRUE_JWT_ADMIN_ROLES": "service_role",
                "GOTRUE_JWT_EXP": "3600",
                "GOTRUE_EXTERNAL_EMAIL_ENABLED": "true",
                "GOTRUE_MAILER_AUTOCONFIRM": "true",
                "GOTRUE_DISABLE_SIGNUP": "true",
                "GOTRUE_MFA_TOTP_ENROLL_ENABLED": "true",
                "GOTRUE_MFA_TOTP_VERIFY_ENABLED": "true",
                "GOTRUE_SMTP_HOST": "127.0.0.1",
                "GOTRUE_SMTP_PORT": "1",
            }
        )
        with self.lock:
            if port in self.processes and self.processes[port].poll() is None:
                raise BridgeError("This approved auth port is already owned")
            with log.open("xb") as stream:
                log.chmod(0o600)
                bootstrap = subprocess.run(
                    [
                        str(host_path(target["binary_directory"]) / "psql"),
                        "-XAt",
                        "--no-password",
                        "--command",
                        "CREATE SCHEMA IF NOT EXISTS auth",
                    ],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                    timeout=10,
                )
                if bootstrap.returncode:
                    raise BridgeError("Dedicated local Auth schema preparation failed")
                migration = subprocess.run(
                    [str(binary), "migrate"],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                    timeout=180,
                )
                if migration.returncode:
                    raise BridgeError("Official local Auth migration failed; private log retained")
                schema = subprocess.run(
                    [
                        str(host_path(target["binary_directory"]) / "psql"),
                        "-XAt",
                        "--no-password",
                        "--command",
                        "SELECT count(*) FROM pg_tables WHERE schemaname='auth' HAVING to_regclass('auth.identities') IS NOT NULL",
                    ],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                )
                count_text = schema.stdout.decode("utf-8", errors="replace").strip()
                if schema.returncode or not count_text.isdecimal() or int(count_text) == 0:
                    raise BridgeError("Auth schema and identities table were not created")
                process = subprocess.Popen(
                    [str(binary), "serve"],
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                )
            self.processes[port] = process
        return {"started": True, "port": port, "pid": process.pid, "auth_tables": int(count_text)}

    def stop(self, port):
        from scripts.p01_local_recovery_env import auth_ports

        if type(port) is not int or port not in auth_ports(self.config()):
            raise BridgeError("Unapproved auth port")
        with self.lock:
            process = self.processes.get(port)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            return {"stopped": True, "port": port}

    def stop_all(self):
        for port in list(self.processes):
            self.stop(port)


def serve(config_path):
    bridge = Bridge(config_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            status, result = 200, None
            token = self.headers.get("Authorization", "")
            authorized = hmac.compare_digest(
                token.encode("utf-8"), ("Bearer " + bridge.token).encode("utf-8")
            )
            if not authorized or self.headers.get("Origin") or self.path != "/v1/jobs":
                status, result = 403, {"error": "Unauthorized local test operation"}
            else:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 65536:
                        raise BridgeError("Invalid request size")
                    data = json.loads(self.rfile.read(size))
                    result = bridge.execute(data)
                except BridgeError as error:
                    status, result = 422, {"error": str(error)}
                except Exception:
                    status, result = (
                        500,
                        {"error": "Local test operation failed; private details suppressed"},
                    )
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if status == 200 and data.get("op") == "shutdown":
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    from scripts.p01_local_recovery_env import bridge_port

    port = bridge_port(
        json.loads(Path(config_path).read_text(encoding="utf-8-sig")).get("bridge_port", 25510)
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    finally:
        bridge.stop_all()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    serve(args.config)
