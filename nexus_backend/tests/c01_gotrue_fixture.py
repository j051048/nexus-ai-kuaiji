"""Pinned real GoTrue in a private WSL directory; test infrastructure only.

No installed packages, production credentials, emails or external auth calls.
Only the explicit C01 loopback clusters are accepted. Secrets use child stdin
and private files, never process arguments or returned verification evidence.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import jwt
import psycopg
from psycopg import sql

from app.auth import AuthSettings, SupabaseAuthenticator
from c01_identity_fixture import _private_write, _secure, LocalIdentityFixture
from test_c01_full_recovery_postgres import ROOT, _read_config, recovery_config_directory
from c01_wsl_bridge import call
from scripts.p01_local_recovery_env import port_is_occupied

ARTIFACT_SHA256 = "f2aa147268461f74784ddc8b2240aa446ee71ceb9633bac41c30e7b852a860aa"
BRIDGE = recovery_config_directory() / "wsl-bridge-private.json"


def create_auth_database(major, *, target):
    config = _read_config(major, target=target)
    parsed = urlsplit(config["admin"])
    name = "ak01_local_ak05_auth_" + uuid4().hex
    with psycopg.connect(config["admin"], autocommit=True) as conn:
        # Supabase's upstream Auth migrations refer to this conventional role.
        # In these disposable clusters it is NOLOGIN and no runtime is a member.
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='postgres'").fetchone():
            conn.execute(
                "CREATE ROLE postgres NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
        attrs = conn.execute(
            "SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname='postgres'"
        ).fetchone()
        if any(attrs):
            raise ValueError("本机Auth兼容角色不得拥有登录或管理能力")
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        conn.execute(
            sql.SQL("ALTER DATABASE {} SET search_path=auth,public").format(sql.Identifier(name))
        )
    return {
        "admin": urlunsplit(parsed._replace(path="/" + name)),
        "major": major,
        "bridge_target": ("target" if target else "source") + str(major),
    }


def restore_auth_database(config, archive, proof):
    archive = Path(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != proof["sha256"]:
        raise ValueError("Auth私有备份摘要不符")
    with psycopg.connect(config["admin"]) as conn:
        if conn.execute("SELECT to_regnamespace('auth')").fetchone()[0] is not None:
            raise ValueError("Auth恢复目标必须全新，禁止覆盖")
    call(
        BRIDGE,
        "pg_restore",
        target=config["bridge_target"],
        database=urlsplit(config["admin"]).path[1:],
        input=str(archive),
    )
    # Changing the signing key alone does NOT invalidate opaque refresh tokens.
    # This explicit local recovery policy requires a fresh password + MFA login.
    with psycopg.connect(config["admin"]) as conn:
        rows = conn.execute(
            "SELECT id::text,email,encrypted_password FROM auth.users ORDER BY id"
        ).fetchall()
        if hashlib.sha256(json.dumps(rows).encode()).hexdigest() != proof["users_sha256"]:
            raise ValueError("恢复的身份UUID或密码哈希与备份不一致")
        sessions = conn.execute("SELECT count(*) FROM auth.sessions").fetchone()[0]
        refresh = conn.execute("SELECT count(*) FROM auth.refresh_tokens").fetchone()[0]
        conn.execute("DELETE FROM auth.refresh_tokens")
        conn.execute("DELETE FROM auth.sessions")
        payload = {
            "action": "local_recovery_sessions_invalidated",
            "archive_sha256": proof["sha256"],
            "sessions": sessions,
            "refresh_tokens": refresh,
            "requires_password_and_mfa": True,
        }
        conn.execute(
            "INSERT INTO auth.audit_log_entries(id,payload,created_at,ip_address) VALUES(%s,%s::jsonb,%s,'127.0.0.1')",
            (str(uuid4()), json.dumps(payload), datetime.now(timezone.utc)),
        )
    return payload


class _LocalAuthTransport(httpx.BaseTransport):
    def __init__(self, origin, port):
        self.origin, self.port = origin, port
        self.reach_host = os.environ.get("C01_AUTH_PROXY_HOST", "127.0.0.1")
        self.reach_port = port if self.reach_host == "127.0.0.1" else port + 1000
        self.client = httpx.Client(trust_env=False, timeout=10)

    def handle_request(self, request):
        if str(request.url).split("/auth/v1")[0] != self.origin:
            raise RuntimeError("认证请求越过本机固定测试身份来源")
        path = request.url.raw_path.decode().removeprefix("/auth/v1")
        response = self.client.request(
            request.method,
            f"http://{self.reach_host}:{self.reach_port}" + path,
            headers=dict(request.headers),
            content=request.read(),
        )
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    def close(self):
        self.client.close()


class RealGoTrue:
    def __init__(self, config, directory, port):
        value = urlsplit(config["admin"])
        if (
            value.hostname != "127.0.0.1"
            or value.port not in {25496, 25497, 25498, 25499}
            or not value.path.startswith("/ak01_local_ak05_auth_")
        ):
            raise ValueError("真实GoTrue只允许独立本机Auth测试库")
        if port not in {25511, 25512}:
            raise ValueError("真实GoTrue仅允许固定两个回环测试端口")
        self.config, self.directory, self.port = config, Path(directory), port
        self.origin = "https://c01-real-auth-" + str(port) + ".example.test"
        self.secret = secrets.token_urlsafe(48)
        self.publishable_key = "sb_publishable_c01_real_local_only"
        self.factors = {}
        self.process = None
        self.client = httpx.Client(trust_env=False, timeout=10)

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        _secure(self.directory)
        if os.environ.get("C01_AUTH_PORT_FORWARDED") != "1":
            if port_is_occupied(self.port):
                raise RuntimeError("本机身份测试端口已有监听，禁止接管")
        call(
            BRIDGE,
            "start_gotrue",
            target=self.config["bridge_target"],
            database=urlsplit(self.config["admin"]).path[1:],
            port=self.port,
            jwt_secret=self.secret,
            origin=self.origin,
            log_path=str(self.directory / "runtime-private.log"),
        )
        self.running = True
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                reach_host = os.environ.get("C01_AUTH_PROXY_HOST", "127.0.0.1")
                reach_port = self.port if reach_host == "127.0.0.1" else self.port + 1000
                if (
                    self.client.get(f"http://{reach_host}:{reach_port}/health").status_code
                    == 200
                ):
                    return self
            except httpx.TransportError:
                pass
            time.sleep(0.1)
        self.close()
        raise RuntimeError("本机GoTrue启动超时")

    def __exit__(self, *args):
        self.close()

    def close(self):
        if getattr(self, "running", False):
            call(BRIDGE, "stop_gotrue", port=self.port)
            self.running = False
            _secure(self.directory / "runtime-private.log")
        self.client.close()

    def _request(self, method, path, *, token=None, body=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        reach_host = os.environ.get("C01_AUTH_PROXY_HOST", "127.0.0.1")
        reach_port = self.port if reach_host == "127.0.0.1" else self.port + 1000
        response = self.client.request(
            method, f"http://{reach_host}:{reach_port}" + path, headers=headers, json=body
        )
        if not response.is_success:
            raise ValueError(f"本机GoTrue受控请求失败，HTTP {response.status_code}")
        return response.json() if response.content else None

    def admin_token(self):
        now = int(time.time())
        return jwt.encode(
            {
                "role": "service_role",
                "aud": "authenticated",
                "iss": self.origin + "/auth/v1",
                "iat": now,
                "exp": now + 600,
            },
            self.secret,
            algorithm="HS256",
        )

    @property
    def admin_key(self):
        return self.admin_token()

    def provision(self, user_id, email, password):
        return self._request(
            "POST",
            "/admin/users",
            token=self.admin_token(),
            body={"id": user_id, "email": email, "password": password, "email_confirm": True},
        )

    def login(self, email, password):
        return self._request(
            "POST", "/token?grant_type=password", body={"email": email, "password": password}
        )

    def authenticator(self):
        client = httpx.Client(transport=_LocalAuthTransport(self.origin, self.port))
        return SupabaseAuthenticator(
            AuthSettings("supabase", self.origin, "sb_publishable_c01_real_local_only"),
            http_client=client,
        )

    def enroll_factor(self, token):
        factor = self._request(
            "POST",
            "/factors",
            token=token,
            body={"factor_type": "totp", "friendly_name": "C01实际测试验证器"},
        )
        self.factors[jwt.decode(token, options={"verify_signature": False})["sub"]] = factor
        return factor

    def step_up(self, token):
        user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
        factor = self.factors.get(user_id) or self.enroll_factor(token)
        return self.verify_factor(token, factor)

    def verify_factor(self, token, factor):
        challenge = self._request(
            "POST", f"/factors/{factor['id']}/challenge", token=token, body={}
        )
        code = LocalIdentityFixture._totp(factor["totp"]["secret"], int(time.time()))
        return self._request(
            "POST",
            f"/factors/{factor['id']}/verify",
            token=token,
            body={"challenge_id": challenge["id"], "code": code},
        )

    def ban(self, user_id):
        return self._request(
            "PUT",
            f"/admin/users/{user_id}",
            token=self.admin_token(),
            body={"ban_duration": "87600h"},
        )

    def backup(self, destination):
        destination = Path(destination)
        self.close()
        call(
            BRIDGE,
            "pg_dump",
            target=self.config["bridge_target"],
            database=urlsplit(self.config["admin"]).path[1:],
            output=str(destination),
            schema="auth",
        )
        _secure(destination)
        with psycopg.connect(self.config["admin"]) as conn:
            identities = conn.execute(
                "SELECT id::text,email,encrypted_password FROM auth.users ORDER BY id"
            ).fetchall()
            factors = conn.execute(
                "SELECT count(*) FROM auth.mfa_factors WHERE status='verified'"
            ).fetchone()[0]
        return {
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "user_ids": [row[0] for row in identities],
            "users_sha256": hashlib.sha256(json.dumps(identities).encode()).hexdigest(),
            "verified_factors": factors,
            "contains_password_hashes": True,
            "quiesced": True,
            "backed_up_at": datetime.now(timezone.utc).isoformat(),
        }
