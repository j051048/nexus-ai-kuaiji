"""Server-only storage configuration, independent of ephemeral container disks."""
import base64
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class StorageSettings:
    backend: str = "local"
    supabase_url: str = ""
    bucket: str = ""
    server_key: str = field(default="", repr=False)
    timeout_seconds: int = 20
    allow_http: bool = False

    def __post_init__(self):
        if self.backend not in {"local", "supabase"}:
            raise ValueError("ACCOUNTING_STORAGE_BACKEND 必须是 local 或 supabase")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60:
            raise ValueError("存储超时须为 1–60 秒")
        if self.backend == "local":
            return
        try:
            parsed = urlsplit(self.supabase_url)
            parsed.port
        except ValueError:
            raise ValueError("Supabase 存储 URL 无效") from None
        local_http = self.allow_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or re.search(r"[\s\\]", self.supabase_url):
            raise ValueError("Supabase 存储 URL 须为可信项目根 HTTPS 地址；本地 HTTP 仅用于明确启用的测试")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", self.bucket):
            raise ValueError("私有存储 bucket 名称无效")
        if not self.server_key or len(self.server_key) > 4096 or re.search(r"[\s]", self.server_key):
            raise ValueError("Supabase 存储须配置服务端密钥")
        if self.server_key.startswith("sb_secret_") and len(self.server_key) > len("sb_secret_"):
            return
        # Legacy service_role JWTs are accepted for existing projects. This is
        # only a key-type check; Supabase validates their actual signatures.
        try:
            parts = self.server_key.split(".")
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))) if len(parts) == 3 else {}
            accepted = isinstance(payload, dict) and payload.get("role") == "service_role"
        except (ValueError, IndexError, UnicodeError):
            accepted = False
        if not accepted:
            raise ValueError("存储只接受服务端 secret key 或 legacy service_role key；不能使用浏览器 publishable/anon key")

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        try:
            timeout = int(env.get("ACCOUNTING_STORAGE_TIMEOUT_SECONDS", "20"))
        except (ValueError, TypeError):
            raise ValueError("ACCOUNTING_STORAGE_TIMEOUT_SECONDS 须为整数") from None
        raw_http = env.get("ACCOUNTING_STORAGE_ALLOW_HTTP", "false").lower()
        if raw_http not in {"true", "false"}:
            raise ValueError("ACCOUNTING_STORAGE_ALLOW_HTTP 必须是 true 或 false")
        return cls(backend=env.get("ACCOUNTING_STORAGE_BACKEND", "local"),
                   supabase_url=env.get("ACCOUNTING_STORAGE_SUPABASE_URL", ""),
                   bucket=env.get("ACCOUNTING_STORAGE_BUCKET", ""),
                   server_key=env.get("ACCOUNTING_STORAGE_SERVER_KEY", ""),
                   timeout_seconds=timeout, allow_http=raw_http == "true")
