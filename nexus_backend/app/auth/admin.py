"""Server-only Supabase account provisioning. Never returns provider error bodies."""

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt

from .supabase import AuthSettings

from app.accounting.domain import AccountingError


@dataclass(frozen=True)
class SupabaseAdmin:
    url: str = ""
    key: str = field(default="", repr=False)
    public_app_url: str = ""
    client: object = field(default=None, repr=False, compare=False)

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("ACCOUNTING_SUPABASE_URL", "").strip().rstrip("/"),
            os.getenv("ACCOUNTING_SUPABASE_ADMIN_KEY", "").strip(),
            os.getenv("ACCOUNTING_PUBLIC_APP_URL", "").strip().rstrip("/"),
        )

    @property
    def configured(self):
        return bool(self.url and self.key and self.public_app_url)

    def validate(self):
        if not self.configured:
            raise AccountingError("账号开通尚未配置，请设置服务端身份管理密钥与前端公开地址", 503)
        for value in (self.url, self.public_app_url):
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme == "https"
                    and parsed.hostname
                    and parsed.port in (None, 443)
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.path in ("", "/")
                )
            except ValueError:
                valid = False
            if not valid:
                raise AccountingError("身份管理地址必须配置为固定 HTTPS 根地址", 503)
            try:
                AuthSettings(
                    mode="supabase",
                    supabase_url=value,
                    publishable_key="sb_publishable_configuration_check",
                )
            except RuntimeError:
                raise AccountingError(
                    "身份管理地址必须为有效 HTTPS 域名，不含 IP 或本机地址", 503
                ) from None
        if re.fullmatch(r"sb_secret_[A-Za-z0-9_-]+", self.key):
            return
        try:
            claims = jwt.decode(self.key, options={"verify_signature": False})
            valid_key = claims.get("role") == "service_role"
        except jwt.PyJWTError:
            valid_key = False
        if not valid_key:
            raise AccountingError(
                "账号开通仅接受服务端 secret 或 legacy service_role 管理密钥", 503
            )

    def _request(self, method, path, *, body=None, params=None):
        self.validate()
        # The client is short lived; no credentials are put into URLs, logs or exceptions.
        client = self.client or httpx.Client(timeout=20.0, follow_redirects=False)
        try:
            response = client.request(
                method,
                self.url + "/auth/v1" + path,
                headers={"apikey": self.key, "Authorization": "Bearer " + self.key},
                json=body,
                params=params,
                follow_redirects=False,
            )
            if response.status_code not in (200, 201):
                raise AccountingError("身份服务未完成开户，请检查服务端配置后重试", 503)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise AccountingError(
                "身份服务暂不可用，稍后可重试；当前尚未授予任何权限", 503
            ) from None
        finally:
            if self.client is None:
                client.close()

    def find_user(self, email):
        # GoTrue's admin filter is a search, not identity proof: exact-match the
        # returned email and bound pagination. No Auth directory reaches clients.
        for page in range(1, 101):
            payload = self._request(
                "GET", "/admin/users", params={"page": page, "per_page": 100, "filter": email}
            )
            users = payload.get("users")
            if not isinstance(users, list):
                raise AccountingError("身份服务返回格式错误，请联系管理员", 503)
            matches = [
                user for user in users if str(user.get("email", "")).lower() == email.lower()
            ]
            if len(matches) > 1:
                raise AccountingError("邮箱存在身份冲突，请联系管理员核实", 409)
            if matches:
                return matches[0]
            if len(users) < 100:
                return None
        raise AccountingError("身份目录超过试点查询上限，请联系管理员", 503)

    @staticmethod
    def _user(payload, email):
        user = payload.get("user", payload)
        try:
            UUID(user["id"])
            valid = user.get("email", "").lower() == email.lower()
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise AccountingError("身份服务返回的账号不匹配，当前尚未授予权限", 503)
        return user

    def provision(self, email, name, request_id):
        user = self.find_user(email)
        if user is None:
            try:
                payload = self._request(
                    "POST",
                    "/admin/users",
                    body={
                        "email": email,
                        "email_confirm": False,
                        "user_metadata": {"display_name": name},
                        "app_metadata": {"accounting_provision_request": request_id},
                    },
                )
                user = self._user(payload, email)
            except AccountingError:
                # The response may be lost after creation. Recover by exact email;
                # only our server-written marker allows issuing an activation OTP.
                user = self.find_user(email)
                if user is None:
                    raise
        user = self._user(user, email)
        owned = (user.get("app_metadata") or {}).get("accounting_provision_request") == request_id
        if user.get("email_confirmed_at") or not owned:
            return {"user_id": user["id"], "existing_user": True}
        generated = self._request(
            "POST",
            "/admin/generate_link",
            body={
                "type": "invite",
                "email": email,
                "redirect_to": self.public_app_url + "/account-activation",
            },
        )
        generated_user = self._user(generated, email)
        token_hash = generated.get("hashed_token") or (generated.get("properties") or {}).get(
            "hashed_token"
        )
        if generated_user["id"] != user["id"] or not isinstance(token_hash, str) or not token_hash:
            raise AccountingError("未能生成安全激活链接，请重试", 503)
        return {"user_id": user["id"], "existing_user": False, "token_hash": token_hash}

    def bootstrap_user(self, email, name, password):
        """Explicit operator bootstrap only; existing users' passwords stay intact."""
        user = self.find_user(email)
        if user:
            user = self._user(user, email)
            if not user.get("email_confirmed_at"):
                raise AccountingError("已有账号尚未验证邮箱，请先完成原账号验证", 409)
            return user
        if len(password) < 8:
            raise AccountingError("初始密码至少需要 8 个字符", 422)
        return self._user(
            self._request(
                "POST",
                "/admin/users",
                body={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"display_name": name},
                },
            ),
            email,
        )
