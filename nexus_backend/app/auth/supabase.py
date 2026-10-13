"""固定 Supabase 项目的同步认证器，供 FastAPI 同步 Depends 在线程池调用。"""

import ipaddress
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError


class AuthError(Exception):
    def __init__(self, detail: str, status: int = 401):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _unauthorized() -> AuthError:
    return AuthError("登录已失效，请重新登录", 401)


def _unavailable() -> AuthError:
    return AuthError("登录验证服务暂时不可用，请稍后重试", 503)


@dataclass(frozen=True)
class Identity:
    user_id: str
    email: str | None
    display_name: str


@dataclass(frozen=True)
class AuthSettings:
    mode: str = "local"
    supabase_url: str | None = None
    publishable_key: str | None = field(default=None, repr=False)
    audience: str = "authenticated"

    def __post_init__(self):
        if self.mode not in {"local", "supabase"}:
            raise RuntimeError("ACCOUNTING_AUTH_MODE 只能为 local 或 supabase")
        if self.mode == "local":
            return
        if not self.supabase_url or not self.publishable_key:
            raise RuntimeError(
                "Supabase 登录必须配置 ACCOUNTING_SUPABASE_URL 和 ACCOUNTING_SUPABASE_PUBLISHABLE_KEY"
            )
        try:
            parsed = urlsplit(self.supabase_url)
            valid_port = parsed.port in {None, 443}
            host = parsed.hostname or ""
            try:
                ipaddress.ip_address(host)
                is_ip = True
            except ValueError:
                is_ip = False
        except ValueError:
            raise RuntimeError("ACCOUNTING_SUPABASE_URL 必须是有效的 HTTPS 项目地址") from None
        if (
            any(character.isspace() for character in self.supabase_url)
            or parsed.scheme != "https"
            or not valid_port
            or not host
            or "." not in host
            or is_ip
            or host.endswith(".localhost")
            or host == "localhost"
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "ACCOUNTING_SUPABASE_URL 必须是有效的 HTTPS 项目地址，不含路径、用户信息或查询参数"
            )
        object.__setattr__(self, "supabase_url", self.supabase_url.rstrip("/"))
        if not self.audience or len(self.audience) > 200:
            raise RuntimeError("ACCOUNTING_AUTH_AUDIENCE 必须为非空 audience")
        key = self.publishable_key
        if re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", key):
            return
        # 旧版 anon API key 只能用于公开配置。这里读取未验证的声明仅用于
        # 拒绝误配 service_role，不以该声明识别用户或授予任何业务权限。
        try:
            claims = jwt.decode(key, options={"verify_signature": False})
            public_key = claims.get("role") == "anon"
        except PyJWTError:
            public_key = False
        if not public_key:
            raise RuntimeError(
                "ACCOUNTING_SUPABASE_PUBLISHABLE_KEY 只能使用 publishable 或 legacy anon 公钥，禁止 secret/service_role"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        return cls(
            mode=env.get("ACCOUNTING_AUTH_MODE", "local").strip().lower(),
            supabase_url=env.get("ACCOUNTING_SUPABASE_URL", "").strip() or None,
            publishable_key=env.get("ACCOUNTING_SUPABASE_PUBLISHABLE_KEY", "").strip() or None,
            audience=env.get("ACCOUNTING_AUTH_AUDIENCE", "authenticated").strip(),
        )

    @property
    def issuer(self):
        return f"{self.supabase_url}/auth/v1"

    def public_config(self):
        if self.mode == "local":
            return {"mode": "local"}
        return {
            "mode": "supabase",
            "supabase_url": self.supabase_url,
            "publishable_key": self.publishable_key,
        }


class JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class _RemoteJwks:
    """只访问配置的固定地址；公钥缓存五分钟，未知 kid 立即刷新一次。"""

    def __init__(self, url: str, client: httpx.Client):
        self.url = url
        self.client = client
        self._keys: list[jwt.PyJWK] | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def _fetch(self):
        try:
            response = self.client.get(self.url, follow_redirects=False)
            if response.status_code != 200:
                raise _unavailable()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
                raise _unavailable()
            # 共享密钥不得从 JWKS 进入本地 JWT 解码路径。
            raw_keys = [
                key
                for key in payload["keys"]
                if isinstance(key, dict)
                and key.get("kty") in {"EC", "RSA"}
                and key.get("alg") in {"ES256", "RS256"}
                and key.get("use", "sig") == "sig"
                and "d" not in key
                and (
                    "key_ops" not in key
                    or (isinstance(key["key_ops"], list) and "verify" in key["key_ops"])
                )
            ]
            keys = [jwt.PyJWK.from_dict(key) for key in raw_keys]
        except (httpx.HTTPError, ValueError, TypeError, PyJWTError):
            raise _unavailable() from None
        self._keys = keys
        self._expires = time.monotonic() + 300

    def get_signing_key_from_jwt(self, token: str):
        kid = jwt.get_unverified_header(token).get("kid")
        with self._lock:
            fetched = self._keys is None or self._expires <= time.monotonic()
            if fetched:
                self._fetch()
            matches = [key for key in self._keys or [] if key.key_id == kid]
            if not matches and not fetched:
                self._fetch()
                matches = [key for key in self._keys or [] if key.key_id == kid]
            if len(matches) != 1:
                raise _unauthorized()
            return matches[0]


class SupabaseAuthenticator:
    def __init__(
        self,
        settings: AuthSettings,
        *,
        http_client: httpx.Client | None = None,
        jwks_client: JwksClient | None = None,
    ):
        if settings.mode != "supabase":
            raise RuntimeError("SupabaseAuthenticator 需要 supabase 认证配置")
        self.settings = settings
        self._owns_client = http_client is None
        self.client = http_client or httpx.Client(timeout=5.0, follow_redirects=False)
        self.jwks = jwks_client or _RemoteJwks(
            f"{settings.issuer}/.well-known/jwks.json", self.client
        )

    def close(self):
        if self._owns_client:
            self.client.close()

    def _online_user(self, token: str) -> dict:
        try:
            response = self.client.get(
                f"{self.settings.issuer}/user",
                headers={
                    "apikey": self.settings.publishable_key,
                    "Authorization": f"Bearer {token}",
                },
                follow_redirects=False,
            )
            if response.status_code in {400, 401, 403, 422}:
                raise _unauthorized()
            if response.status_code != 200:
                raise _unavailable()
            user = response.json()
            if not isinstance(user, dict):
                raise _unavailable()
            return user
        except (httpx.HTTPError, ValueError):
            raise _unavailable() from None

    def _decode(self, token: str, key=None, *, signature: bool):
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["ES256", "RS256"],
            issuer=self.settings.issuer,
            audience=self.settings.audience,
            options={
                "verify_signature": signature,
                "verify_iss": True,
                "verify_aud": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
                "verify_sub": True,
                "require": ["iss", "aud", "exp", "sub", "iat"],
            },
        )
        if (
            type(claims["exp"]) is not int
            or type(claims["iat"]) is not int
            or claims["exp"] <= claims["iat"]
        ):
            raise _unauthorized()
        try:
            UUID(claims["sub"])
        except (ValueError, TypeError, AttributeError):
            raise _unauthorized() from None
        return claims

    def authenticate(self, token: str) -> Identity:
        if not isinstance(token, str) or not token or len(token) > 16384 or token.strip() != token:
            raise _unauthorized()
        try:
            # 未验证的 header 只选择固定允许的验证路径，绝不提供身份、
            # 权限或公钥 URL。algorithms 参数始终是固定的非对称白名单。
            header = jwt.get_unverified_header(token)
            alg = header.get("alg")
            if alg not in {"ES256", "RS256", "HS256"} or header.get("crit"):
                raise _unauthorized()
            if alg in {"ES256", "RS256"}:
                kid = header.get("kid")
                if not isinstance(kid, str) or not kid or len(kid) > 256:
                    raise _unauthorized()
                key = self.jwks.get_signing_key_from_jwt(token)
                if key.algorithm_name != alg:
                    raise _unauthorized()
                claims = self._decode(token, key.key, signature=True)
                user = self._online_user(token)
            else:
                # legacy HS256 没有任何本地共享 secret。固定项目 Auth 服务
                # 首先验证完整令牌；其成功后才检查相同令牌的标准声明。
                user = self._online_user(token)
                claims = self._decode(token, signature=False)
        except PyJWKClientConnectionError:
            raise _unavailable() from None
        except (PyJWTError, PyJWKClientError, ValueError, TypeError):
            raise _unauthorized() from None

        try:
            user_id = str(UUID(user.get("id", "")))
        except (ValueError, TypeError, AttributeError):
            raise _unavailable() from None
        if (
            user_id != str(UUID(claims["sub"]))
            or user.get("is_anonymous") is True
            or user.get("deleted_at")
        ):
            raise _unauthorized()
        banned_until = user.get("banned_until")
        if banned_until:
            try:
                banned = datetime.fromisoformat(banned_until.replace("Z", "+00:00"))
                if banned.tzinfo is None or banned > datetime.now(timezone.utc):
                    raise _unauthorized()
            except (ValueError, TypeError, AttributeError):
                raise _unavailable() from None
        email = user.get("email") or None
        if email is not None and not isinstance(email, str):
            raise _unavailable()
        # Email-based assignments require an email confirmed by the provider.
        confirmed = user.get("email_confirmed_at")
        if not email or not isinstance(confirmed, str) or not confirmed:
            raise _unauthorized()
        try:
            timestamp = datetime.fromisoformat(confirmed.replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
                raise _unavailable()
        except ValueError:
            raise _unavailable() from None
        metadata = user.get("user_metadata")
        name = (
            (metadata.get("display_name") or metadata.get("full_name"))
            if isinstance(metadata, dict)
            else None
        )
        display_name = (
            name.strip()[:120] if isinstance(name, str) and name.strip() else (email or user_id)
        )
        return Identity(user_id=user_id, email=email, display_name=display_name)
