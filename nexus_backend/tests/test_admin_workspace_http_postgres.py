"""Real auth dependency -> admin router -> restricted PostgreSQL integration."""

import pytest

from test_stage2_api import ADMIN, RUNTIME, client, headers, setup_books

pytestmark = pytest.mark.skipif(not ADMIN or not RUNTIME, reason="需要本机隔离 PostgreSQL 测试连接")


def test_http_admin_permissions_lifecycle_and_export_boundary(client):
    firm, company, book = setup_books(client)
    owner = headers("owner", firm)
    email = client.provider.identities["employee"].email
    added = client.put(
        f"/api/companies/{company}/members", headers=owner, json={"email": email, "role": "admin"}
    )
    assert added.status_code == 200, added.text
    admin = headers("employee", firm)
    assert client.get("/api/me", headers=admin).json()["permissions"]["manage_admin"]
    assert client.get("/api/admin/companies", headers=admin).json()["total"] == 1
    other = client.post("/api/companies", headers=owner, json={"name": "另一家企业"}).json()["id"]
    assert client.get(f"/api/admin/companies/{other}", headers=admin).status_code == 404
    assert client.get("/api/admin/companies", headers=headers("stranger", firm)).status_code == 403
    path = f"/api/admin/companies/{company}"
    assert (
        client.patch(
            path,
            headers=admin,
            json={"name": "修正企业名称", "reason": "工商更名", "expected_version": 1},
        ).status_code
        == 200
    )
    assert (
        client.post(
            path + "/status",
            headers=admin,
            json={"status": "suspended", "reason": "暂停", "expected_version": 2},
        ).status_code
        == 403
    )
    status = client.post(
        path + "/status",
        headers=owner,
        json={"status": "archived", "reason": "试点结束", "expected_version": 2},
    )
    assert status.status_code == 200, status.text
    assert client.get(path, headers=owner).json()["books"][0]["id"] == book
    assert client.get(f"/api/books/{book}", headers=owner).status_code == 404
    assert client.get("/api/admin/audit?company_id=" + company, headers=owner).json()["total"] == 5
    assert (
        client.post(
            path + "/status",
            headers=owner,
            json={"status": "active", "reason": "恢复试点", "expected_version": 3},
        ).status_code
        == 200
    )
    assert client.get(f"/api/books/{book}", headers=owner).status_code == 200


def test_http_ordinary_employee_cannot_open_admin_workspace(client):
    firm, company, _book = setup_books(client)
    email = client.provider.identities["employee"].email
    assert (
        client.put(
            f"/api/companies/{company}/members",
            headers=headers("owner", firm),
            json={"email": email, "role": "employee"},
        ).status_code
        == 200
    )
    actor = headers("employee", firm)
    assert client.get("/api/admin/companies", headers=actor).status_code == 403
    assert client.get("/api/admin/audit", headers=actor).status_code == 403
    assert client.get(f"/api/admin/companies/{company}", headers=actor).status_code == 403
