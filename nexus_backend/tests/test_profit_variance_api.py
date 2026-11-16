from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


class FakeVariance:
    def variance(self, actor, book_id, baseline_book_id, line_code=""):
        assert line_code == "revenue"
        return {
            "current_book_id": book_id,
            "baseline_book_id": baseline_book_id,
            "lines": [],
            "contributors": {},
            "checks": {"causal_claim": False},
        }


def test_profit_variance_route_returns_deterministic_payload_without_causal_claim(tmp_path):
    app = create_app(tmp_path / "variance-api.db", seed=True, attachment_root=tmp_path / "private")
    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 6789)
        return await call_next(request)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        app.state.profit = FakeVariance()
        manager = auth(client, "accountant")
        book = client.post("/api/books", headers=manager, json={"name": "偏差归因虚构企业", "period": "2026-10"})
        assert book.status_code == 201, book.text
        body = book.json()
        response = client.get(
            f"/api/reports/profit/variance?book_id={body['id']}&baseline_book_id={body['id']}&line_code=revenue",
            headers=manager,
        )
        assert response.status_code == 200, response.text
        assert response.json()["checks"]["causal_claim"] is False
