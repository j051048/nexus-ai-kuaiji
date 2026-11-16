"""Closing radar is a read-only manager projection."""

from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


def local_client(app):
    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 7799)
        return await call_next(request)

    return TestClient(app, base_url="http://127.0.0.1")


def test_closing_radar_lists_blockers_without_mutating_period(tmp_path):
    app = create_app(tmp_path / "radar.db", seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager = auth(client, "accountant")
        book = client.get("/api/workbench", headers=manager).json()["books"][0]
        response = client.get(
            "/api/firm/observability/closing-radar",
            headers=manager,
            params={"period": book["period"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["period"] == book["period"]
        assert body["books"]
        assert body["counts"]["books"] == len(body["books"])
        assert body["counts"]["blocked"] == sum(1 for item in body["books"] if item["blockers"])
        assert body["note"].startswith("只读雷达")
        assert all(item["period_status"] in {"open", "closed"} for item in body["books"])
        assert all("closable" in item and "blockers" in item for item in body["books"])

        employee = auth(client, "employee")
        denied = client.get(
            "/api/firm/observability/closing-radar",
            headers=employee,
            params={"period": book["period"]},
        )
        assert denied.status_code == 403
