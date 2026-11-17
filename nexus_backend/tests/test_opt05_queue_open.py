"""OPT05-C: monthly open returns a queued batch, not inline company results."""

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_business_api import auth


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "queue-open.db", seed=True, attachment_root=tmp_path / "private")

    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 1234)
        return await call_next(request)

    with TestClient(app, base_url="http://127.0.0.1") as value:
        yield value


def test_legacy_monthly_open_returns_queued_batch(client):
    headers = auth(client, "accountant")
    payload = {
        "period": "2026-11",
        "scope": "awaiting",
        "reason": "月初批量开期进入队列",
        "generate_materials": True,
    }
    response = client.post("/api/monthly/batch/open", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    batch = response.json()
    assert batch["status"] == "queued"
    assert batch["kind"] == "monthly_open"
    assert batch["items"]
    assert all(item["status"] == "queued" for item in batch["items"])
    assert "results" not in batch

    same = client.post("/api/monthly/batch/open", headers=headers, json=payload)
    assert same.status_code == 202
    assert same.json()["id"] == batch["id"]
