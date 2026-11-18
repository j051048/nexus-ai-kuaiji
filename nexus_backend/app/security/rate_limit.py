"""Small in-process token buckets; explicit single-process approximation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import math
import os
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

SCOPE = "single_process"
EXEMPT_PATHS = frozenset({"/api/health", "/api/public/config", "/health", "/openapi.json"})
EXPENSIVE_PREFIXES = (
    "/api/import",
    "/api/intelligence",
    "/api/reports",
    "/api/monthly/batch",
    "/api/tax/",
    "/api/backups",
)


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimiter:
    read_per_minute: int = 6000
    write_per_minute: int = 1200
    expensive_per_minute: int = 600
    clock: callable = time.monotonic
    _buckets: dict[tuple[str, str], _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    limited_total: int = 0
    limited_by_class: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def limit_for(self, category: str) -> int:
        if category == "read":
            return self.read_per_minute
        if category == "write":
            return self.write_per_minute
        return self.expensive_per_minute

    def check(self, identity: str, category: str):
        if category not in {"read", "write", "expensive"}:
            raise ValueError("未知限流类别")
        limit = self.limit_for(category)
        if limit <= 0:
            return True, 0
        now = self.clock()
        rate = limit / 60.0
        with self._lock:
            bucket = self._buckets.get((identity, category))
            if bucket is None:
                bucket = _Bucket(float(limit), now)
                self._buckets[(identity, category)] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(float(limit), bucket.tokens + elapsed * rate)
                bucket.updated_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            retry_after = max(1, math.ceil((1.0 - bucket.tokens) / rate))
            self.limited_total += 1
            self.limited_by_class[category] += 1
            return False, retry_after

    def snapshot(self):
        with self._lock:
            return {
                "scope": SCOPE,
                "limited_total": self.limited_total,
                "limited_by_class": dict(self.limited_by_class),
                "tracked_buckets": len(self._buckets),
            }


def limiter_from_environment():
    def number(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as error:
            raise ValueError(f"{name} 必须是正整数") from error
        if value < 0:
            raise ValueError(f"{name} 不能为负数")
        return value

    return RateLimiter(
        read_per_minute=number("ACCOUNTING_RATE_LIMIT_READ_PER_MINUTE", 6000),
        write_per_minute=number("ACCOUNTING_RATE_LIMIT_WRITE_PER_MINUTE", 1200),
        expensive_per_minute=number("ACCOUNTING_RATE_LIMIT_EXPENSIVE_PER_MINUTE", 600),
    )


def request_category(method: str, path: str):
    if path in EXEMPT_PATHS or path.startswith(("/docs", "/redoc", "/static/")):
        return None
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if path.startswith(EXPENSIVE_PREFIXES):
        return "expensive"
    return "write"


def _identity(request, trust_proxy: bool):
    token = request.headers.get("authorization", "")
    if token:
        return "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    host = request.client.host if request.client else "unknown"
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            host = forwarded.split(",", 1)[0].strip() or host
    return "ip:" + host


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, limiter: RateLimiter, trust_proxy: bool | None = None):
        super().__init__(app)
        self.limiter = limiter
        self.trust_proxy = (
            os.getenv("ACCOUNTING_TRUST_PROXY", "0") == "1" if trust_proxy is None else trust_proxy
        )

    async def dispatch(self, request, call_next):
        category = request_category(request.method, request.url.path)
        if category is None:
            return await call_next(request)
        allowed, retry_after = self.limiter.check(_identity(request, self.trust_proxy), category)
        if not allowed:
            return JSONResponse(
                {"detail": "请求过于频繁，请稍后重试", "retry_after": retry_after},
                status_code=429,
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
            )
        return await call_next(request)
