"""FSC01: five-company profile matrix, unknown blocking, and manager-only confirm."""

import json

from fastapi.testclient import TestClient

from app.accounting.database import Database
from app.main import create_app
from app.fsc.profiles import REQUIRED, SECTIONS
from test_business_api import auth


def local_client(app):
    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 8801)
        return await call_next(request)

    return TestClient(app, base_url="http://127.0.0.1")


def profile_payload(*, confirmed_sections=REQUIRED):
    confirmations = {
        key: {
            "status": "confirmed",
            "owner": "财务负责人",
            "evidence_ref": f"客户确认/2026/{key}",
            "confirmed_at": "2026-11-01T09:00:00+08:00",
        }
        for key in confirmed_sections
    }
    payload = {key: None for key in REQUIRED}
    payload["_confirmations"] = confirmations
    return payload


def test_five_company_profiles_require_explicit_confirmation(tmp_path):
    path = tmp_path / "fsc-profiles.db"
    app = create_app(path, seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager = auth(client, "accountant")
        book = client.get("/api/workbench", headers=manager).json()["books"][0]
        manager_id = client.get("/api/me", headers=manager).json()["user"]["id"]
        with Database(path).connect() as conn:
            firm_id = conn.execute("SELECT firm_id FROM books WHERE id=?", (book["id"],)).fetchone()[0]
            for index in range(1, 6):
                company_id = f"fsc-company-{index}"
                conn.execute("INSERT INTO companies(id,name) VALUES(?,?)", (company_id, f"FSC 合成企业 {index}"))
                conn.execute("INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')", (firm_id, company_id))
                conn.execute("INSERT INTO company_memberships(company_id,user_id,role) VALUES(?,?,'finance')", (company_id, manager_id))

        profile_ids = []
        for index in range(1, 6):
            confirmed = REQUIRED if index < 5 else tuple(key for key in REQUIRED if key != "tax_boundary")
            response = client.post(
                "/api/fsc/profiles",
                headers=manager,
                params={"company_id": f"fsc-company-{index}"},
                json={"industry": f"行业{index}", "payload": profile_payload(confirmed_sections=confirmed)},
            )
            assert response.status_code == 200, response.text
            profile_ids.append(response.json()["id"])

        for profile_id in profile_ids[:4]:
            confirmed = client.post(
                f"/api/fsc/profiles/{profile_id}/confirm",
                headers=manager,
                json={"expected_version": 1},
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "confirmed"

        blocked = client.post(
            f"/api/fsc/profiles/{profile_ids[4]}/confirm",
            headers=manager,
            json={"expected_version": 1},
        )
        assert blocked.status_code == 422
        assert "税务登记与申报边界" in blocked.text

        matrix = client.get("/api/fsc/profiles/matrix", headers=manager)
        assert matrix.status_code == 200, matrix.text
        rows = {row["company_id"]: row for row in matrix.json()["items"]}
        assert rows["fsc-company-1"]["status"] == "confirmed"
        assert rows["fsc-company-5"]["status"] == "draft"
        assert "税务登记与申报边界" in rows["fsc-company-5"]["unresolved"]
        assert rows["fsc-company-1"]["unresolved"] == []

        active = client.get(
            "/api/fsc/profiles/active",
            headers=manager,
            params={"company_id": "fsc-company-1"},
        )
        assert active.status_code == 200, active.text
        assert active.json()["profile"]["status"] == "confirmed"

        employee = auth(client, "employee")
        denied = client.get(
            "/api/fsc/profiles/active",
            headers=employee,
            params={"company_id": "fsc-company-1"},
        )
        assert denied.status_code in {403, 404}
