from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


def test_fde_adapter_catalog_is_read_only_and_manager_scoped(tmp_path):
    app = create_app(tmp_path / "fde-adapters.db", seed=True, attachment_root=tmp_path / "private")

    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 6789)
        return await call_next(request)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = auth(client, "accountant")
        book = client.post("/api/books", headers=headers, json={"name": "FDE 适配器虚构企业", "period": "2026-10"})
        assert book.status_code == 201, book.text
        body = book.json()
        response = client.get(f"/api/firm/fde/adapters?company_id={body['company_id']}", headers=headers)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["live_external"] is False
        assert value["entity_tags"]["vehicles"] == 0
        assert {item["code"] for item in value["adapters"]} >= {
            "tax_xlsx", "transport_master", "oa_snapshot", "tplus54_synthetic", "sync_stub"
        }
        assert all(item["live_external"] is False for item in value["adapters"])
        assert "不授予写入" in value["note"]
        employee = auth(client, "employee")
        denied = client.get(f"/api/firm/fde/adapters?company_id={body['company_id']}", headers=employee)
        assert denied.status_code in {403, 404}
