"""Persistent, offline Supabase-protocol substitute, NOT a GoTrue implementation.

Only tests import this module. Real password verification, signed JWTs, TOTP,
sessions and UUIDs survive a private fixture bundle. TestClient maps the fixed
HTTPS test origin to its ASGI application; no socket or cloud account is used.
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import struct
import subprocess
import time
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import jwt

from app.auth import AuthSettings, SupabaseAuthenticator
from app.storage.factory import create_attachment_store
from app.storage.settings import StorageSettings


FORMAT = "c01-offline-identity-v1"
PUBLIC_KEY = "sb_publishable_c01_private_fixture_only"


def _secure(path):
    """POSIX 0700/0600, or an owner-only protected NTFS ACL on Windows."""
    path = Path(path)
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    script = """
$ErrorActionPreference='Stop'
$target=$env:C01_FIXTURE_ACL_PATH
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
if(Test-Path -LiteralPath $target -PathType Container){
 $acl=New-Object System.Security.AccessControl.DirectorySecurity
 $inherit=[System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
 $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl',$inherit,'None','Allow')
}else{
 $acl=New-Object System.Security.AccessControl.FileSecurity
 $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow')
}
$acl.SetOwner($sid)
$acl.SetAccessRuleProtection($true,$false)
$acl.AddAccessRule($rule)
if([System.IO.Directory]::Exists($target)){
 [System.IO.Directory]::SetAccessControl($target,$acl)
}else{
 [System.IO.File]::SetAccessControl($target,$acl)
}
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "C01_FIXTURE_ACL_PATH": str(path.resolve())},
        check=True,
        capture_output=True,
    )


def _private_write(path, raw):
    path = Path(path)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _secure(path)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000).hex()


class LocalIdentityFixture:
    """A separate identity database and signing key with genuine authentication."""

    @classmethod
    def create(cls, directory, origin="https://c01-auth.example.test"):
        directory = Path(directory)
        if directory.exists():
            raise ValueError("identity destination already exists")
        AuthSettings("supabase", origin, PUBLIC_KEY)
        if not origin.endswith(".example.test"):
            raise ValueError("fixture origins must be fixed .example.test HTTPS domains")
        directory.mkdir(parents=True, mode=0o700)
        _secure(directory)
        key = ec.generate_private_key(ec.SECP256R1())
        config = {
            "format": FORMAT,
            "origin": origin,
            "key_id": "c01-" + uuid4().hex,
            "admin_key": "sb_secret_" + secrets.token_hex(24),
        }
        _private_write(directory / "config.json", _json(config))
        _private_write(
            directory / "signing-key.pem",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        _private_write(directory / "identity.sqlite", b"")
        with sqlite3.connect(directory / "identity.sqlite") as conn:
            conn.executescript("""
                CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,
                    salt TEXT NOT NULL,password_hash TEXT NOT NULL,confirmed INTEGER NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0,totp_secret TEXT,factor_id TEXT,
                    app_metadata TEXT NOT NULL);
                CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires_at INTEGER NOT NULL,
                    revoked INTEGER NOT NULL,aal TEXT NOT NULL);
                CREATE TABLE challenges(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,session_id TEXT NOT NULL,
                    factor_id TEXT NOT NULL,expires_at INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            """)
        return cls(directory)

    def __init__(self, directory):
        self.directory = Path(directory)
        self.config = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
        self.origin = self.config["origin"]
        self.publishable_key = PUBLIC_KEY
        self.key_id = self.config["key_id"]
        self.admin_key = self.config["admin_key"]
        self.signing_key = serialization.load_pem_private_key(
            (self.directory / "signing-key.pem").read_bytes(),
            password=None,
        )
        self.online_reads = 0
        self.app = self._application()
        self.client = TestClient(self.app, base_url=self.origin)

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *args):
        self.client.__exit__(*args)

    def close(self):
        self.client.close()

    def connect(self):
        connection = sqlite3.connect(self.directory / "identity.sqlite")
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def protected(path):
        path = Path(path)
        if os.name != "nt":
            return (path.stat().st_mode & 0o777) == (0o700 if path.is_dir() else 0o600)
        script = """
$target=$env:C01_FIXTURE_ACL_PATH
if([System.IO.Directory]::Exists($target)){$acl=[System.IO.Directory]::GetAccessControl($target)}
else{$acl=[System.IO.File]::GetAccessControl($target)}
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rules=@($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]))
$valid=$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $sid
$valid=$valid -and $rules.Count -gt 0
foreach($rule in $rules){if($rule.IdentityReference.Value -ne $sid -or $rule.AccessControlType -ne 'Allow'){$valid=$false}}
if($valid){'private'}else{'not-private'}
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "C01_FIXTURE_ACL_PATH": str(path.resolve())},
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "private"

    def _user(self, row):
        return {
            "id": row["id"],
            "email": row["email"],
            "aud": "authenticated",
            "role": "authenticated",
            "email_confirmed_at": "2026-01-01T00:00:00Z" if row["confirmed"] else None,
            "user_metadata": {"display_name": row["name"]},
            "app_metadata": json.loads(row["app_metadata"]),
            "factor_id": row["factor_id"],
            "factors": (
                [{"id": row["factor_id"], "factor_type": "totp", "status": "verified"}]
                if row["factor_id"]
                else []
            ),
        }

    def _issue(self, conn, row, aal="aal1"):
        now, session_id = int(time.time()), str(uuid4())
        conn.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?)", (session_id, row["id"], now + 3600, 0, aal)
        )
        claims = {
            "iss": self.origin + "/auth/v1",
            "aud": "authenticated",
            "sub": row["id"],
            "iat": now,
            "exp": now + 3600,
            "role": "authenticated",
            "aal": aal,
            "jti": session_id,
            "session_id": session_id,
        }
        token = jwt.encode(
            claims, self.signing_key, algorithm="ES256", headers={"kid": self.key_id}
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": 3600,
            "user": self._user(row),
        }

    def _verify(self, request):
        value = request.headers.get("authorization", "")
        if not value.startswith("Bearer "):
            raise HTTPException(401, "missing session")
        try:
            claims = jwt.decode(
                value[7:],
                self.signing_key.public_key(),
                algorithms=["ES256"],
                issuer=self.origin + "/auth/v1",
                audience="authenticated",
            )
            with self.connect() as conn:
                session = conn.execute(
                    "SELECT * FROM sessions WHERE id=?", (claims["jti"],)
                ).fetchone()
                user = conn.execute("SELECT * FROM users WHERE id=?", (claims["sub"],)).fetchone()
                if (
                    not session
                    or not user
                    or session["revoked"]
                    or not user["confirmed"]
                    or user["disabled"]
                    or session["user_id"] != user["id"]
                    or session["aal"] != claims["aal"]
                    or session["expires_at"] <= int(time.time())
                ):
                    raise ValueError("invalid active session")
            return claims, user
        except (jwt.PyJWTError, KeyError, ValueError):
            raise HTTPException(401, "invalid active session") from None

    def _application(self):
        app = FastAPI()

        def admin(request):
            if not hmac.compare_digest(
                request.headers.get("authorization", ""), "Bearer " + self.admin_key
            ):
                raise HTTPException(403, "private fixture administrator required")

        @app.get("/auth/v1/.well-known/jwks.json")
        def jwks():
            key = jwt.algorithms.ECAlgorithm.to_jwk(self.signing_key.public_key(), as_dict=True)
            return {"keys": [{**key, "kid": self.key_id, "alg": "ES256", "use": "sig"}]}

        @app.post("/auth/v1/admin/users")
        async def provision(request: Request):
            admin(request)
            body = await request.json()
            email = str(body.get("email", "")).strip().lower()
            password = body.get("password")
            if "@" not in email or not isinstance(password, str) or not 12 <= len(password) <= 128:
                raise HTTPException(422, "fixture email and password required")
            salt = secrets.token_hex(16)
            totp = bool(body.get("fixture_totp", False))
            with self.connect() as conn:
                if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                    raise HTTPException(409, "fixture identity already exists")
                uid = str(uuid4())
                conn.execute(
                    "INSERT INTO users VALUES(?,?,?,?,?,?,0,?,?,?)",
                    (
                        uid,
                        email,
                        (body.get("user_metadata") or {}).get("display_name", "虚构恢复人员"),
                        salt,
                        _password(password, salt),
                        int(body.get("email_confirm") is True),
                        base64.b32encode(secrets.token_bytes(20)).decode() if totp else None,
                        str(uuid4()) if totp else None,
                        json.dumps(body.get("app_metadata") or {}),
                    ),
                )
                return self._user(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())

        @app.get("/auth/v1/admin/users")
        def users(request: Request):
            admin(request)
            with self.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM users WHERE email LIKE ? ORDER BY email",
                    ("%" + request.query_params.get("filter", "") + "%",),
                ).fetchall()
                return {"users": [self._user(row) for row in rows]}

        @app.post("/auth/v1/token")
        async def login(request: Request):
            body = await request.json()
            if request.query_params.get("grant_type") != "password":
                raise HTTPException(400, "fixture supports password grant only")
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM users WHERE email=?", (str(body.get("email", "")).lower(),)
                ).fetchone()
                password = body.get("password")
                if (
                    not row
                    or not isinstance(password, str)
                    or not row["confirmed"]
                    or row["disabled"]
                    or not hmac.compare_digest(
                        _password(password, row["salt"]), row["password_hash"]
                    )
                ):
                    raise HTTPException(400, "invalid login credentials")
                return self._issue(conn, row)

        @app.get("/auth/v1/user")
        def user(request: Request):
            self.online_reads += 1
            _, row = self._verify(request)
            return self._user(row)

        @app.post("/auth/v1/logout")
        def logout(request: Request):
            claims, _ = self._verify(request)
            with self.connect() as conn:
                conn.execute("UPDATE sessions SET revoked=1 WHERE id=?", (claims["jti"],))
            return {}

        @app.post("/auth/v1/factors/{factor_id}/challenge")
        def challenge(factor_id: str, request: Request):
            claims, user = self._verify(request)
            if factor_id != user["factor_id"]:
                raise HTTPException(404, "factor not found")
            challenge_id, expires = str(uuid4()), int(time.time()) + 60
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO challenges(id,user_id,session_id,factor_id,expires_at) VALUES(?,?,?,?,?)",
                    (challenge_id, user["id"], claims["jti"], factor_id, expires),
                )
            return {"id": challenge_id, "type": "totp", "expires_at": expires}

        @app.post("/auth/v1/factors/{factor_id}/verify")
        async def verify_factor(factor_id: str, request: Request):
            claims, user = self._verify(request)
            body = await request.json()
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM challenges WHERE id=?", (body.get("challenge_id"),)
                ).fetchone()
                code = body.get("code")
                valid = (
                    row
                    and not row["used"]
                    and row["user_id"] == user["id"]
                    and row["session_id"] == claims["jti"]
                    and row["factor_id"] == factor_id
                    and row["expires_at"] > int(time.time())
                    and isinstance(code, str)
                    and user["totp_secret"]
                    and code.isascii()
                    and code.isdigit()
                    and len(code) == 6
                )
                if not valid or not any(
                    hmac.compare_digest(
                        code, self._totp(user["totp_secret"], int(time.time()) + offset)
                    )
                    for offset in (-30, 0, 30)
                ):
                    raise HTTPException(422, "invalid TOTP or challenge")
                conn.execute("UPDATE challenges SET used=1 WHERE id=?", (row["id"],))
                conn.execute("UPDATE sessions SET revoked=1 WHERE id=?", (claims["jti"],))
                return self._issue(conn, user, "aal2")

        return app

    def provision(self, email, password, *, confirmed=True, totp=False, name="虚构恢复人员"):
        result = self.client.post(
            self.origin + "/auth/v1/admin/users",
            headers={"Authorization": "Bearer " + self.admin_key},
            json={
                "email": email,
                "password": password,
                "email_confirm": confirmed,
                "fixture_totp": totp,
                "user_metadata": {"display_name": name},
            },
        )
        result.raise_for_status()
        return result.json()

    def login(self, email, password):
        result = self.client.post(
            self.origin + "/auth/v1/token?grant_type=password",
            json={"email": email, "password": password},
        )
        result.raise_for_status()
        return result.json()

    def challenge(self, token):
        user = self.client.get(
            self.origin + "/auth/v1/user", headers={"Authorization": "Bearer " + token}
        )
        user.raise_for_status()
        result = self.client.post(
            self.origin + f"/auth/v1/factors/{user.json()['factor_id']}/challenge",
            headers={"Authorization": "Bearer " + token},
        )
        result.raise_for_status()
        return result.json()

    @staticmethod
    def _totp(secret, at):
        digest = hmac.new(
            base64.b32decode(secret), struct.pack(">Q", at // 30), hashlib.sha1
        ).digest()
        offset = digest[-1] & 15
        return str(
            (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
        ).zfill(6)

    def step_up(self, token):
        claims = jwt.decode(token, options={"verify_signature": False})
        with self.connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE id=?", (claims["sub"],)).fetchone()
        challenge = self.challenge(token)
        result = self.client.post(
            self.origin + f"/auth/v1/factors/{user['factor_id']}/verify",
            headers={"Authorization": "Bearer " + token},
            json={
                "challenge_id": challenge["id"],
                "code": self._totp(user["totp_secret"], int(time.time())),
            },
        )
        result.raise_for_status()
        return result.json()

    def authenticator(self):
        return SupabaseAuthenticator(
            AuthSettings("supabase", self.origin, self.publishable_key), http_client=self.client
        )

    def revoke(self, token):
        result = self.client.post(
            self.origin + "/auth/v1/logout", headers={"Authorization": "Bearer " + token}
        )
        result.raise_for_status()

    def session_state(self, token):
        session_id = jwt.decode(token, options={"verify_signature": False})["jti"]
        with self.connect() as conn:
            return dict(conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())

    def user_count(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def backup(self, destination):
        path = Path(destination)
        if path.exists():
            raise ValueError("identity bundle already exists")
        if not path.parent.exists():
            path.parent.mkdir(parents=True, mode=0o700)
            _secure(path.parent)
        if not self.protected(path.parent):
            raise ValueError("identity bundle requires a private destination directory")
        with self.connect() as conn, sqlite3.connect(":memory:") as snapshot:
            conn.backup(snapshot)
            raw_database = snapshot.serialize()
        content = {
            "format": FORMAT,
            "scope": "synthetic_identity_provider_only",
            "config": self.config,
            "database": base64.b64encode(raw_database).decode(),
            "signing_key": (self.directory / "signing-key.pem").read_text(encoding="ascii"),
        }
        raw = _json(content)
        _private_write(path, raw)
        return {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "users": self.user_count(),
            "format": FORMAT,
        }

    @classmethod
    def restore(cls, bundle, directory, *, expected_sha256, origin=None):
        target = Path(directory)
        raw = Path(bundle).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("identity bundle hash mismatch")
        content = json.loads(raw)
        if (
            content.get("format") != FORMAT
            or content.get("scope") != "synthetic_identity_provider_only"
        ):
            raise ValueError("unknown identity fixture format")
        if target.exists():
            raise ValueError("identity restore target already exists")
        config = content["config"]
        if origin:
            AuthSettings("supabase", origin, PUBLIC_KEY)
            if not origin.endswith(".example.test"):
                raise ValueError("fixture origin must stay offline")
            config["origin"] = origin
        database = base64.b64decode(content["database"], validate=True)
        key = content["signing_key"].encode("ascii")
        serialization.load_pem_private_key(key, password=None)
        target.mkdir(parents=True, mode=0o700)
        _secure(target)
        _private_write(target / "config.json", _json(config))
        _private_write(target / "signing-key.pem", key)
        _private_write(target / "identity.sqlite", database)
        with sqlite3.connect(target / "identity.sqlite") as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("identity database integrity failed")
        return cls(target)


def verify_joint_bundle(joint, identity_bundle, accounting_manifest, required_users):
    """Check both immutable snapshots and active UUIDs before any restore write."""
    raw = Path(identity_bundle).read_bytes()
    if (
        joint.get("quiesced") is not True
        or joint.get("identity_contains_sensitive_material") is not True
    ):
        raise ValueError(
            "identity/accounting snapshots require explicit quiescence and sensitivity"
        )
    if hashlib.sha256(raw).hexdigest() != joint.get("identity_sha256"):
        raise ValueError("identity bundle hash mismatch")
    if hashlib.sha256(Path(accounting_manifest).read_bytes()).hexdigest() != joint.get(
        "accounting_manifest_sha256"
    ):
        raise ValueError("accounting manifest hash mismatch")
    content = json.loads(raw)
    if (
        content.get("format") != FORMAT
        or content.get("scope") != "synthetic_identity_provider_only"
    ):
        raise ValueError("identity bundle format mismatch")
    with sqlite3.connect(":memory:") as conn:
        conn.deserialize(base64.b64decode(content["database"], validate=True))
        present = {
            row[0] for row in conn.execute("SELECT id FROM users WHERE confirmed=1 AND disabled=0")
        }
    scoped = joint.get("user_uuids")
    if not isinstance(scoped, list) or len(scoped) != len(set(scoped)):
        raise ValueError("identity scope is invalid")
    if set(scoped) != set(required_users) or not set(required_users) <= present:
        raise ValueError("active accounting identity missing from combined snapshot")
    return sorted(required_users)


class LocalPrivateObjectFixture:
    """Persistent private REST object substitute using the real storage adapter."""

    bucket = "c01-private-originals"

    def __init__(self, directory, origin="https://c01-objects.example.test"):
        self.directory = Path(directory)
        if self.directory.exists():
            raise ValueError("private object fixture destination already exists")
        self.directory.mkdir(parents=True, mode=0o700)
        _secure(self.directory)
        self.origin = origin
        self.server_key = "sb_secret_" + secrets.token_hex(24)
        self.app = FastAPI()

        def authorize(request):
            if not hmac.compare_digest(request.headers.get("apikey", ""), self.server_key):
                raise HTTPException(403, "private object authorization required")

        def object_path(key):
            relative = Path(key)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise HTTPException(422, "invalid private object key")
            target = (self.directory / relative).resolve()
            if self.directory.resolve() not in target.parents:
                raise HTTPException(422, "invalid private object scope")
            return target

        @self.app.get("/storage/v1/bucket/{bucket}")
        def bucket_metadata(bucket: str, request: Request):
            authorize(request)
            if bucket != self.bucket:
                raise HTTPException(404, "bucket not found")
            return {"id": self.bucket, "public": False, "file_size_limit": 10 * 1024 * 1024}

        @self.app.post("/storage/v1/object/{bucket}/{key:path}")
        async def upload(bucket: str, key: str, request: Request):
            authorize(request)
            if bucket != self.bucket:
                raise HTTPException(404, "bucket not found")
            if request.headers.get("x-upsert") != "false":
                raise HTTPException(409, "immutable objects only")
            target = object_path(key)
            if target.exists():
                raise HTTPException(409, "immutable object already exists")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _private_write(target, await request.body())
            return {"Key": self.bucket + "/" + key}

        @self.app.get("/storage/v1/object/{bucket}/{key:path}")
        def download(bucket: str, key: str, request: Request):
            from fastapi.responses import Response

            authorize(request)
            if bucket != self.bucket:
                raise HTTPException(404, "bucket not found")
            target = object_path(key)
            if not target.is_file():
                raise HTTPException(404, "private object missing")
            return Response(
                target.read_bytes(),
                media_type="application/octet-stream",
                headers={"Cache-Control": "private, no-store"},
            )

        self.client = TestClient(self.app, base_url=self.origin)

    def store(self):
        settings = StorageSettings(
            backend="supabase",
            supabase_url=self.origin,
            bucket=self.bucket,
            server_key=self.server_key,
        )
        return create_attachment_store(settings=settings, client=self.client)

    def restore_original(self, key, data):
        result = self.client.post(
            self.origin + "/storage/v1/object/" + self.bucket + "/" + key,
            content=data,
            headers={"apikey": self.server_key, "x-upsert": "false"},
        )
        result.raise_for_status()

    def close(self):
        self.client.close()
