"""Real PostgreSQL API integration with an explicit test-only auth provider.

Signature/online provider behavior is independently exercised by auth tests.
No test identity endpoint is installed in the real application.
"""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthSettings, AuthError, Identity
from app.main import create_app
from scripts.migrate_accounting import migrate

ADMIN = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
RUNTIME = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN or not RUNTIME, reason="需要独立测试 PostgreSQL 连接")


class TestIdentityProvider:
    __test__ = False

    def __init__(self):
        suffix = uuid4().hex
        self.identities = {
            key: Identity(str(uuid4()), f"{key}-{suffix}@example.test", f"测试{key}", aal="aal2")
            for key in ("owner", "operator", "reviewer", "viewer", "employee", "stranger")
        }

    def authenticate(self, token):
        if token not in self.identities:
            raise AuthError("测试非法登录", 401)
        return self.identities[token]


@pytest.fixture
def client(monkeypatch):
    # This fixture intentionally exercises self-service firm creation. The real
    # deployment is invitation-only by default and separately tested below.
    monkeypatch.setenv("ACCOUNTING_ALLOW_FIRM_SIGNUP", "1")
    migrate(ADMIN)
    provider = TestIdentityProvider()
    settings = AuthSettings(mode="supabase", supabase_url="https://isolated-auth.example.test", publishable_key="sb_publishable_test_only")
    app = create_app(RUNTIME, seed=False, auth_settings=settings, authenticator=provider)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 43210)) as client:
        client.provider = provider
        for token in provider.identities:
            assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        yield client


def headers(token, firm=None):
    return {"Authorization": f"Bearer {token}", **({"X-Firm-Id": firm} if firm else {})}


def test_management_http_denies_password_only_session_without_blocking_business(client):
    firm, company, book = setup_books(client)
    original = client.provider.identities["owner"]
    client.provider.identities["owner"] = Identity(original.user_id, original.email, original.display_name)
    weak = headers("owner", firm)
    for method, path, body in [
        ("GET", "/api/admin/companies", None),
        ("POST", "/api/companies", {"name": "不能创建"}),
        ("POST", "/api/books", {"name": "不能隐式创建企业", "period": "2026-10"}),
        ("PUT", f"/api/companies/{company}/members", {"email": client.provider.identities["employee"].email, "role": "employee"}),
        ("POST", f"/api/books/{book}/ledger/managers", {"user_id": original.user_id, "role": "manager"}),
        ("POST", f"/api/books/{book}/legacy-migrations/project/access", {"user_id": original.user_id, "can_review": True}),
        ("DELETE", f"/api/books/{book}/legacy-migrations/project/access/user", None),
    ]:
        response = client.request(method, path, headers=weak, json=body)
        assert response.status_code == 403
        assert response.json()["code"] == "admin_mfa_required"
    assert client.get("/api/me", headers=weak).status_code == 200
    assert client.get("/api/workbench", headers=weak).status_code == 200


def test_production_firm_signup_is_closed_without_explicit_opt_in(client, monkeypatch):
    monkeypatch.delenv("ACCOUNTING_ALLOW_FIRM_SIGNUP", raising=False)
    settings = AuthSettings(mode="supabase", supabase_url="https://isolated-auth.example.test", publishable_key="sb_publishable_test_only")
    app = create_app(RUNTIME, seed=False, auth_settings=settings, authenticator=client.provider)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 43210)) as closed:
        assert closed.get("/api/auth/config").json()["allow_firm_signup"] is False
        response = closed.post("/api/firms", headers=headers("owner"), json={"name": "禁止自行开通"})
        assert response.status_code == 403
        assert "仅允许管理员" in response.json()["detail"]


def setup_books(client):
    firm_response = client.post("/api/firms", headers=headers("owner"), json={"name": "真实PG接口试点"})
    assert firm_response.status_code == 201, firm_response.text
    firm = firm_response.json()["id"]
    company_response = client.post("/api/companies", headers=headers("owner", firm), json={"name": "接口测试客户"})
    assert company_response.status_code == 201, company_response.text
    company = company_response.json()["id"]
    book_response = client.post("/api/books", headers=headers("owner", firm), json={"company_id": company, "name": "试点账套", "period": "2026-10"})
    assert book_response.status_code == 201, book_response.text
    return firm, company, book_response.json()["id"]


def test_public_configuration_real_mode_has_no_demo_session_and_forged_scope_fails(client):
    config = client.get("/api/auth/config").json()
    assert config["mode"] == "supabase" and "publishable_key" in config
    assert client.get("/api/session").status_code == 404
    assert client.get("/api/workbench").status_code == 401
    assert client.get("/api/me", headers=headers("bad")).status_code == 401
    firm, _, _ = setup_books(client)
    assert client.get("/api/me", headers=headers("stranger", firm)).status_code == 403
    assert client.get("/api/workbench", headers=headers("stranger", firm)).status_code == 403


def test_real_postgres_http_workflow_with_duties_and_immediate_revocation(client):
    firm, company, book = setup_books(client)
    owner = headers("owner", firm)
    for token, role in [("operator", "operator"), ("reviewer", "reviewer"), ("viewer", "viewer")]:
        identity = client.provider.identities[token]
        assert client.put("/api/firm/members", headers=owner, json={"email": identity.email, "role": role}).status_code == 200
        assert client.get("/api/workbench", headers=headers(token, firm)).json()["books"] == []
        assert client.put(f"/api/books/{book}/members/{identity.user_id}", headers=owner, json={"role": "reviewer"}).status_code == 200
    operator, reviewer, viewer = headers("operator", firm), headers("reviewer", firm), headers("viewer", firm)
    payload = {"source_name": "试点接口.csv", "rows": [{"external_id": "bank:001", "date": "2026-10-01", "description": "办公用品", "amount": "0.30", "direction": "expense", "evidence_ref": "虚构发票"}]}
    assert client.post(f"/api/books/{book}/imports", headers=viewer, json=payload).status_code == 403
    assert client.post(f"/api/books/{book}/imports", headers=operator, json=payload).json() == {"imported": 1, "duplicates": 0}
    assert client.post(f"/api/books/{book}/suggestions", headers=operator, json={}).json()["created"] == 1
    draft = client.get(f"/api/books/{book}", headers=operator).json()["drafts"][0]
    assert client.post(f"/api/books/{book}/drafts/{draft['id']}/approve", headers=operator, json={"expected_version": 1}).status_code == 403
    approved = client.post(f"/api/books/{book}/drafts/{draft['id']}/approve", headers=reviewer, json={"expected_version": 1})
    assert approved.status_code == 200, approved.text
    receipt = client.post(f"/api/books/{book}/exports", headers=reviewer, json={"draft_ids": [draft["id"]], "idempotency_key": "http-receipt-001"})
    assert receipt.status_code == 200, receipt.text
    assert "0.30" in receipt.json()["csv"]
    receipt_id = receipt.json()["id"]
    assert client.get(f"/api/books/{book}/exports/{receipt_id}", headers=viewer).status_code == 403
    assert client.get(f"/api/books/{book}/exports/{receipt_id}", headers=operator).status_code == 403
    assert client.get(f"/api/books/{book}/exports/{receipt_id}", headers=reviewer).json() == receipt.json()
    reviewer_id = client.provider.identities["reviewer"].user_id
    assert client.delete(f"/api/books/{book}/members/{reviewer_id}", headers=owner).status_code == 200
    assert client.get(f"/api/books/{book}", headers=reviewer).status_code == 404
    employee = client.provider.identities["employee"]
    assert client.put(f"/api/companies/{company}/members", headers=owner, json={"email": employee.email, "role": "employee"}).status_code == 200
    assert client.get("/api/me", headers=headers("employee")).json()["current_firm_id"] == firm
    assert client.get("/api/workbench", headers=headers("employee", firm)).json()["books"] == []
    assert client.post("/api/books", headers=headers("employee", firm), json={"name": "非法账套", "period": "2026-10", "company_id": company}).status_code == 403


def test_same_identity_switches_independent_firms_without_data_leak(client):
    first, _, first_book = setup_books(client)
    response = client.post("/api/firms", headers=headers("owner"), json={"name": "第二独立事务所"})
    second = response.json()["id"]
    assert len(client.get("/api/me", headers=headers("owner")).json()["firms"]) == 2
    assert client.get("/api/workbench", headers=headers("owner", second)).json()["books"] == []
    assert client.get(f"/api/books/{first_book}", headers=headers("owner", second)).status_code == 404
    assert client.get(f"/api/books/{first_book}", headers=headers("owner", first)).status_code == 200


def test_actor_workspace_revocation_rejects_same_authenticated_session(client):
    firm, company, _ = setup_books(client)
    owner = headers("owner", firm)
    identity = client.provider.identities["employee"]
    assert (
        client.put(
            f"/api/companies/{company}/members",
            headers=owner,
            json={"email": identity.email, "role": "employee"},
        ).status_code
        == 200
    )
    employee = headers("employee", firm)
    assert client.get("/api/business/context", headers=employee).status_code == 200
    assert (
        client.delete(
            f"/api/companies/{company}/members/{identity.user_id}", headers=owner
        ).status_code
        == 200
    )
    denied = client.get("/api/business/context", headers=employee)
    assert denied.status_code == 403 and "工作空间授权" in denied.json()["detail"]
