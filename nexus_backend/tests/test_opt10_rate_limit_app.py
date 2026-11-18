"""OPT10-C：限流必须在真实 FastAPI 应用链路上返回 429。"""

from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.main import create_app


class _LoopbackClient(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.scope["client"] = ("127.0.0.1", 45678)
        return await call_next(request)


def test_real_app_returns_429_and_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_RATE_LIMIT_READ_PER_MINUTE", "2")
    monkeypatch.setenv("ACCOUNTING_RATE_LIMIT_WRITE_PER_MINUTE", "100")
    monkeypatch.setenv("ACCOUNTING_RATE_LIMIT_EXPENSIVE_PER_MINUTE", "100")
    app = create_app(tmp_path / "rate-limit-app.db", seed=False, queue_worker_enabled=False)
    app.add_middleware(_LoopbackClient)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"Origin": "http://127.0.0.1:8080"}
        assert client.get("/api/session", headers=headers).status_code == 200
        assert client.get("/api/session", headers=headers).status_code == 200
        limited = client.get("/api/session", headers=headers)
        assert limited.status_code == 429
        assert limited.headers["Retry-After"].isdigit()
        assert int(limited.headers["Retry-After"]) >= 1
        assert limited.json()["detail"] == "请求过于频繁，请稍后重试"
