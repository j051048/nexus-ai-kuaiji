"""Bounded Supabase Storage REST adapter; no public URLs or upload overwrite.

REST paths match the official storage client. Secret API keys use apikey;
legacy service-role JWTs additionally use Authorization: Bearer.
"""

import json
import time
from contextlib import nullcontext

import httpx

from ..accounting.domain import AccountingError
from .local import check_key

MAX_CONTROL_BYTES = 64 * 1024


def _unique_object(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON field")
        output[key] = value
    return output


class SupabaseBlobBackend:
    root = None

    def __init__(self, settings, client=None):
        if settings.backend != "supabase":
            raise ValueError("SupabaseBlobBackend 需要 supabase 存储配置")
        self.settings, self._client = settings, client
        self._base = settings.supabase_url.rstrip("/") + "/storage/v1"

    def _request(
        self,
        method,
        path,
        *,
        max_bytes=MAX_CONTROL_BYTES,
        data=None,
        media_type=None,
        payload=None,
        unknown_upload=False,
    ):
        headers = {"apikey": self.settings.server_key, "Accept-Encoding": "identity"}
        if not self.settings.server_key.startswith("sb_secret_"):
            headers["Authorization"] = "Bearer " + self.settings.server_key
        if data is not None:
            headers.update(
                {"Content-Type": media_type, "x-upsert": "false", "Cache-Control": "no-store"}
            )
        client_context = (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(
                timeout=self.settings.timeout_seconds, follow_redirects=False, trust_env=False
            )
        )
        started = time.monotonic()
        try:
            with client_context as client:
                with client.stream(
                    method,
                    self._base + path,
                    headers=headers,
                    content=data,
                    json=payload,
                    follow_redirects=False,
                    timeout=self.settings.timeout_seconds,
                ) as response:
                    if response.status_code not in {200, 201}:
                        # Never include a supplier response, endpoint or secret
                        # in an error exposed to a browser or a worker log.
                        raise AccountingError("私有存储请求未成功，请核验服务端存储配置", 503)
                    if response.headers.get("Content-Encoding", "identity").lower() not in {
                        "",
                        "identity",
                    }:
                        raise AccountingError("私有存储响应格式无效", 503)
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isdigit() or int(length) > max_bytes):
                        raise AccountingError("私有存储响应超出读取上限", 503)
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if (
                            total > max_bytes
                            or time.monotonic() - started > self.settings.timeout_seconds
                        ):
                            raise AccountingError("私有存储响应超出读取上限或超时", 503)
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.HTTPError:
            message = (
                "原件上传结果暂不确定，系统未覆盖或删除任何对象；请核验状态后重试"
                if unknown_upload
                else "私有存储暂不可用，请稍后重试"
            )
            raise AccountingError(message, 503) from None

    def verify_private(self):
        raw = self._request("GET", "/bucket/" + self.settings.bucket)
        try:
            metadata = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError):
            raise AccountingError("私有存储 bucket 响应无效", 503) from None
        if (
            not isinstance(metadata, dict)
            or metadata.get("id") != self.settings.bucket
            or metadata.get("public") is not False
        ):
            raise AccountingError("原件 bucket 必须存在且为私有，禁止使用公开 bucket", 503)
        return {
            "id": self.settings.bucket,
            "private": True,
            "file_size_limit": metadata.get("file_size_limit"),
        }

    def put(self, key, data, media_type):
        check_key(key)
        self.verify_private()
        self._request(
            "POST",
            "/object/" + self.settings.bucket + "/" + key,
            data=data,
            media_type=media_type,
            unknown_upload=True,
        )

    def read(self, key, max_bytes):
        check_key(key)
        self.verify_private()
        return self._request(
            "GET", "/object/" + self.settings.bucket + "/" + key, max_bytes=max_bytes
        )

    def discard(self, key):
        check_key(key)
        self.verify_private()
        # Exact newly-created opaque key only; never a folder/prefix purge.
        self._request("DELETE", "/object/" + self.settings.bucket, payload={"prefixes": [key]})
