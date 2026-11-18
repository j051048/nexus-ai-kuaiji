from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.rate_limit import (
    RateLimiter,
    RateLimitMiddleware,
    request_category,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _app(limiter: RateLimiter, *, trust_proxy: bool = False):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/companies")
    def companies():
        return []

    @app.post("/api/companies")
    def create_company():
        return {"ok": True}

    @app.post("/api/tax/workpaper")
    def expensive():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, limiter=limiter, trust_proxy=trust_proxy)
    return app


def test_token_bucket_separates_classes_and_refills():
    clock = FakeClock()
    limiter = RateLimiter(read_per_minute=2, write_per_minute=1, expensive_per_minute=1, clock=clock)
    assert limiter.check("ip:a", "read") == (True, 0)
    assert limiter.check("ip:a", "read") == (True, 0)
    assert limiter.check("ip:a", "read")[0] is False
    assert limiter.check("ip:a", "write") == (True, 0)
    assert limiter.check("ip:b", "write") == (True, 0)
    denied, retry_after = limiter.check("ip:a", "write")
    assert denied is False and retry_after >= 1
    clock.now += 60
    assert limiter.check("ip:a", "write")[0] is True
    snapshot = limiter.snapshot()
    assert snapshot["scope"] == "single_process"
    assert snapshot["limited_total"] == 2


def test_middleware_returns_429_with_retry_after_and_exempts_health():
    clock = FakeClock()
    limiter = RateLimiter(write_per_minute=1, read_per_minute=1, clock=clock)
    client = TestClient(_app(limiter))
    for _ in range(5):
        assert client.get("/api/health").status_code == 200
    assert client.post("/api/companies").status_code == 200
    limited = client.post("/api/companies")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json()["retry_after"] == 60
    assert limiter.snapshot()["limited_by_class"] == {"write": 1}


def test_category_classification():
    assert request_category("GET", "/api/health") is None
    assert request_category("GET", "/api/companies") == "read"
    assert request_category("POST", "/api/companies") == "write"
    assert request_category("POST", "/api/tax/workpaper") == "expensive"
    assert request_category("POST", "/api/monthly/batch-export") == "expensive"


def test_forwarded_for_is_ignored_unless_explicitly_trusted():
    clock = FakeClock()
    limiter = RateLimiter(write_per_minute=1, clock=clock)
    client = TestClient(_app(limiter, trust_proxy=False))
    assert client.post("/api/companies", headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
    assert client.post("/api/companies", headers={"x-forwarded-for": "2.2.2.2"}).status_code == 429

    trusted = TestClient(_app(RateLimiter(write_per_minute=1, clock=clock), trust_proxy=True))
    assert (
        trusted.post("/api/companies", headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
    )
    assert (
        trusted.post("/api/companies", headers={"x-forwarded-for": "2.2.2.2"}).status_code == 200
    )
    assert (
        trusted.post("/api/companies", headers={"x-forwarded-for": "1.1.1.1"}).status_code == 429
    )
