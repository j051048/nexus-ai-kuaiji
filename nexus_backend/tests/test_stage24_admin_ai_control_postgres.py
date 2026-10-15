"""Authenticated admin AI controls exercise native RLS and MFA with fake identities."""

import pytest

from app.auth.supabase import Identity
from test_operations_api import policy_body
from test_stage2_api import ADMIN, RUNTIME, client, headers, setup_books  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN or not RUNTIME, reason="需要本机隔离 PostgreSQL 测试连接")


def control_body(value, enabled):
    return {
        "enabled": enabled,
        "reason": "明确暂停或恢复本企业后台AI处理",
        "expected_enabled": value["ai"]["enabled"],
        "expected_updated_at": value["ai"]["updated_at"],
        "expected_company_version": value["company"]["version"],
    }


def test_http_pg_ai_controls_require_mfa_on_new_and_legacy_mutations(client):
    firm, company, _book = setup_books(client)
    owner = headers("owner", firm)
    path = f"/api/admin/companies/{company}/ai-control"
    current = client.get(path, headers=owner)
    assert current.status_code == 200, current.text
    assert current.json()["ai"]["enabled"] is True
    command = control_body(current.json(), False)
    original = client.provider.identities["owner"]
    client.provider.identities["owner"] = Identity(
        original.user_id, original.email, original.display_name
    )
    for method, endpoint, body in (
        ("GET", path, None),
        ("PATCH", path, command),
        ("PUT", path + "/policy", {**policy_body(), "reason": "管理员明确设置上限"}),
        ("PUT", f"/api/companies/{company}/intelligence/settings", {"enabled": False}),
        ("PUT", f"/api/operations/companies/{company}/policy", policy_body()),
    ):
        response = client.request(method, endpoint, headers=owner, json=body)
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "admin_mfa_required"
    client.provider.identities["owner"] = original
    # Failed AAL1 attempts leave current state and budget untouched.
    value = client.get(path, headers=owner).json()
    assert value["ai"] == current.json()["ai"] and value["policy"]["version"] == 0
    changed = client.patch(path, headers=owner, json=command)
    assert changed.status_code == 200, changed.text
    assert changed.json()["ai"]["enabled"] is False
    assert (
        client.put(
            f"/api/companies/{company}/intelligence/settings", headers=owner, json={"enabled": True}
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/operations/companies/{company}/policy", headers=owner, json=policy_body()
        ).status_code
        == 200
    )


def test_http_pg_ai_control_company_scope_cas_and_immediate_revoke(client):
    firm, company, _book = setup_books(client)
    owner = headers("owner", firm)
    other = client.post(
        "/api/companies", headers=owner, json={"name": "不可越权的另一企业"}
    ).json()["id"]
    identity = client.provider.identities["employee"]
    granted = client.put(
        f"/api/companies/{company}/members",
        headers=owner,
        json={"email": identity.email, "role": "admin"},
    )
    assert granted.status_code == 200, granted.text
    company_admin = headers("employee", firm)
    path = f"/api/admin/companies/{company}/ai-control"
    current = client.get(path, headers=company_admin)
    assert current.status_code == 200, current.text
    assert (
        client.get(f"/api/admin/companies/{other}/ai-control", headers=company_admin).status_code
        == 404
    )
    initial = current.json()
    paused = client.patch(path, headers=company_admin, json=control_body(initial, False))
    assert paused.status_code == 200, paused.text
    stale = client.patch(path, headers=owner, json=control_body(initial, True))
    assert stale.status_code == 409
    refreshed = client.get(path, headers=company_admin).json()
    renamed = client.patch(
        f"/api/admin/companies/{company}",
        headers=owner,
        json={"name": "企业资料已更新", "reason": "工商名称更正", "expected_version": 1},
    )
    assert renamed.status_code == 200, renamed.text
    assert (
        client.patch(path, headers=company_admin, json=control_body(refreshed, True)).status_code
        == 409
    )
    refreshed = client.get(path, headers=company_admin).json()
    quota = client.put(
        path + "/policy",
        headers=company_admin,
        json={**policy_body(), "reason": "企业管理员批准本月上限"},
    )
    assert quota.status_code == 200, quota.text
    audit = client.get(
        "/api/admin/audit",
        headers=company_admin,
        params={"company_id": company, "action": "admin.ai.policy"},
    )
    assert audit.status_code == 200 and audit.json()["total"] == 1, audit.text
    assert audit.json()["events"][0]["details"]["reason"] == "企业管理员批准本月上限"
    assert (
        client.delete(
            f"/api/companies/{company}/members/{identity.user_id}", headers=owner
        ).status_code
        == 200
    )
    for method, endpoint, body in (
        ("GET", path, None),
        ("PATCH", path, control_body(refreshed, True)),
        (
            "PUT",
            path + "/policy",
            {**policy_body(), "expected_version": 1, "reason": "已撤权者尝试修改"},
        ),
    ):
        assert client.request(method, endpoint, headers=company_admin, json=body).status_code in (
            403,
            404,
        )
    assert client.get(path, headers=owner).json()["ai"]["enabled"] is False


@pytest.mark.parametrize("role", ["employee", "finance"])
def test_http_pg_finance_and_employee_cannot_manage_ai_budget_even_with_mfa(client, role):
    firm, company, _book = setup_books(client)
    owner = headers("owner", firm)
    identity = client.provider.identities["employee"]
    assert (
        client.put(
            f"/api/companies/{company}/members",
            headers=owner,
            json={"email": identity.email, "role": role},
        ).status_code
        == 200
    )
    limited = headers("employee", firm)
    path = f"/api/admin/companies/{company}/ai-control"
    current = client.get(path, headers=owner).json()
    for method, endpoint, body in (
        ("GET", path, None),
        ("PATCH", path, control_body(current, False)),
        ("PUT", path + "/policy", {**policy_body(), "reason": "不应获得管理员权限"}),
        ("PUT", f"/api/companies/{company}/intelligence/settings", {"enabled": False}),
        ("PUT", f"/api/operations/companies/{company}/policy", policy_body()),
    ):
        assert client.request(method, endpoint, headers=limited, json=body).status_code in (
            403,
            404,
        )
    assert client.get(path, headers=owner).json()["ai"]["enabled"] is True


def test_http_pg_archived_company_returns_metadata_only_and_retains_pause(client):
    firm, company, _book = setup_books(client)
    owner = headers("owner", firm)
    path = f"/api/admin/companies/{company}/ai-control"
    current = client.get(path, headers=owner).json()
    assert client.patch(path, headers=owner, json=control_body(current, False)).status_code == 200
    assert (
        client.post(
            f"/api/admin/companies/{company}/status",
            headers=owner,
            json={"status": "archived", "reason": "保存证据后归档", "expected_version": 1},
        ).status_code
        == 200
    )
    metadata = client.get(path, headers=owner)
    assert metadata.status_code == 200, metadata.text
    value = metadata.json()
    assert not value["active"] and not value["ai"]["can_manage"] and value["usage"] is None
    assert value["policy"] is None and value["costs"] == []
    assert client.patch(path, headers=owner, json=control_body(value, True)).status_code == 404
    restored = client.post(
        f"/api/admin/companies/{company}/status",
        headers=owner,
        json={"status": "active", "reason": "仅恢复企业不取消AI暂停", "expected_version": 2},
    )
    assert restored.status_code == 200, restored.text
    assert client.get(path, headers=owner).json()["ai"]["enabled"] is False
