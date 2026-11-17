"""FSC03: firm organization projection is read-only and never grants access."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.accounting.database import Database
from app.main import create_app
from test_business_api import auth
from test_fsc01_profiles import local_client


def test_firm_organization_projection_reports_gaps_without_granting_access(tmp_path):
    path = tmp_path / "fsc-org.db"
    app = create_app(path, seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager_headers = auth(client, "accountant")
        manager_id = client.get("/api/me", headers=manager_headers).json()["user"]["id"]
        book = client.get("/api/workbench", headers=manager_headers).json()["books"][0]
        with Database(path).connect() as conn:
            firm_id = conn.execute("SELECT firm_id FROM books WHERE id=?", (book["id"],)).fetchone()[0]
            for index in range(1, 6):
                company_id = f"fsc03-company-{index}"
                conn.execute("INSERT INTO companies(id,name) VALUES(?,?)", (company_id, f"FSC03 合成企业 {index}"))
                conn.execute("INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')", (firm_id, company_id))
            backup_id = "fsc03-backup"
            conn.execute("INSERT INTO users(id,name,active) VALUES(?,?,1)", (backup_id, "备援"))
            conn.execute(
                "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'operator')", (firm_id, backup_id)
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO service_plan_versions(book_id,firm_id,company_id,version,lead_user_id,reviewer_user_id,backup_user_id,contact_user_id,due_on,notes,reason,updated_by,updated_at) "
                    "VALUES(?,?,?,1,?,?,?,NULL, '2026-11-20','合成 FSC03 服务计划','FSC03 验收',?,'2026-11-16T09:00:00+08:00')",
                    (book["id"], firm_id, book["company_id"], manager_id, manager_id, backup_id, manager_id),
                )
            conn.execute(
                "INSERT INTO service_plan_versions(book_id,firm_id,company_id,version,lead_user_id,reviewer_user_id,backup_user_id,contact_user_id,due_on,notes,reason,updated_by,updated_at) "
                "VALUES(?,?,?,1,?,NULL,?,NULL, '2026-11-20','合成 FSC03 服务计划','FSC03 验收',?,'2026-11-16T09:00:00+08:00')",
                (book["id"], firm_id, book["company_id"], manager_id, manager_id, manager_id),
            )

        response = client.get("/api/fsc/organization", headers=manager_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["company_count"] >= 5
        assert body["summary"]["companies"] == body["company_count"]
        names = {item["company_name"] for item in body["companies"]}
        assert "FSC03 合成企业 1" in names and "FSC03 合成企业 5" in names
        assert sum(1 for name in names if name.startswith("FSC03 合成企业")) == 5
        plan_book = next(
            b for item in body["companies"] for b in item["books"] if b["book_id"] == book["id"]
        )
        assert plan_book["plan"]["backup_user_id"] == manager_id
        assert manager_id in plan_book["book_roles"]
        assert backup_id not in plan_book["book_roles"]
        assert any(
            warning["kind"] == "missing_service_plan"
            for item in body["companies"] for warning in item["warnings"]
        )

        employee = auth(client, "employee")
        denied = client.get("/api/fsc/organization", headers=employee)
        assert denied.status_code in {403, 404}

        # The projection is not an authorization path: the backup user still
        # has no book role and cannot use the book merely because it was named.
        with Database(path).connect() as conn:
            assert conn.execute(
                "SELECT 1 FROM book_access WHERE book_id=? AND user_id=?", (book["id"], backup_id)
            ).fetchone() is None
