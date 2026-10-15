"""Native RLS/trigger and full authenticated HTTP password-provisioning checks."""
import time
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.accounting.admin_identity import AdminIdentityService
from app.accounting.admin_identity_models import AccountRequestCreate
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import DepartmentCreate
from app.auth.supabase import Identity
from app.business.models import SubmissionPolicyUpdate
from app.business.submission import SubmissionPolicyService
from test_admin_password_accounts import PASSWORD, PasswordProvider
from test_postgres_integration import ADMIN_URL, APP_URL, case, peer, postgres
from test_stage2_api import client, headers, setup_books

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要本机隔离 PostgreSQL 测试连接")


def service_request(case):
    directory = DirectoryService(case.db)
    provider = PasswordProvider()
    service = AdminIdentityService(case.db, provider)
    dep = directory.create_department(case.actor, case.company, DepartmentCreate(name="虚构员工部门"))
    req = AccountRequestCreate(idempotency_key="password-" + uuid4().hex, name="虚构企业员工",
                               email=uuid4().hex + "@example.test", company_id=case.company,
                               company_role="employee", department_id=dep["id"], provisioning_mode="password", initial_password=PASSWORD)
    return directory, provider, service, req


def test_pg_password_account_atomic_completion_rls_and_no_ledger_grant(case):
    directory, provider, service, req = service_request(case)
    dep = directory.create_department(case.actor, case.company, DepartmentCreate(name="虚构销售部"))
    req.department_id = dep["id"]
    result = service.create(case.actor, req)
    assert result["request"]["status"] == "completed" and not result["password_already_created"]
    uid = provider.users[req.email]["user_id"]
    with case.db.transaction(actor=case.actor) as conn:
        row = conn.execute("SELECT * FROM company_memberships WHERE company_id=? AND user_id=?", (case.company, uid)).fetchone()
        assert row["role"] == "employee" and row["department_id"] == dep["id"]
        audit = conn.execute("SELECT details FROM audit_events WHERE object_id=? AND action='admin.account.password_created'", (result["request"]["id"],)).fetchone()[0]
        assert uid in audit and PASSWORD not in audit
        assert not conn.execute("SELECT 1 FROM memberships WHERE firm_id=? AND user_id=?", (case.actor.firm_id, uid)).fetchone()
        assert not conn.execute("SELECT 1 FROM book_access WHERE book_id=? AND user_id=?", (case.book, uid)).fetchone()
    assert case.service.workbench(Actor(uid, case.actor.firm_id))["books"] == []
    for actor in (Actor(uid, case.actor.firm_id), peer(case, firm_role="owner", other_firm=True)):
        with case.db.transaction(write=True, actor=actor) as conn:
            assert not conn.execute("SELECT * FROM admin_account_requests WHERE id=?", (result["request"]["id"],)).fetchall()
            assert conn.execute("UPDATE admin_account_requests SET status='cancelled' WHERE id=?", (result["request"]["id"],)).rowcount == 0


def test_pg_native_password_completion_requires_grant_and_live_lease_and_mode_immutable(case):
    directory, provider, service, req = service_request(case)
    def unavailable():
        raise AccountingError("模拟响应丢失", 503)
    provider.callback = unavailable
    result = service.create(case.actor, req)
    request_id = result["request"]["id"]
    uid = provider.users[req.email]["user_id"]
    directory.register_identity(Identity(uid, req.email, req.name))
    for statement in (
        "UPDATE admin_account_requests SET provisioning_mode='activation' WHERE id=?",
        "UPDATE admin_account_requests SET status='completed',provider_user_id='forged',completed_by='forged' WHERE id=?",
    ):
        with pytest.raises(Exception):
            with case.db.transaction(write=True, actor=case.actor) as conn:
                conn.execute(statement, (request_id,))
    with pytest.raises(Exception):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute("UPDATE admin_account_requests SET status='pending',lease_token='synthetic',lease_until=? WHERE id=?", (int(time.time()) + 180, request_id))
            conn.execute("UPDATE admin_account_requests SET status='completed',provider_user_id=?,completed_by=? WHERE id=?", (uid, uid, request_id))
    # The failed native attempt rolled back; the legitimate same request recovers
    # its remote account and publishes its scoped membership exactly once.
    recovered = service.retry(case.actor, request_id, SecretStr("different retry password"))
    assert recovered["request"]["status"] == "completed" and recovered["password_already_created"]


def test_pg_password_audit_cannot_claim_incomplete_or_activation_request(case):
    _directory, provider, service, req = service_request(case)
    provider.callback = lambda: (_ for _ in ()).throw(AccountingError("模拟失败", 503))
    result = service.create(case.actor, req)
    with pytest.raises(Exception):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute("INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at,company_id) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP::text,?)",
                         (case.actor.firm_id, case.actor.user_id, "admin.account.password_created", result["request"]["id"], '{"created_user_id":"forged"}', case.company))


@pytest.mark.parametrize("failed", [True, False])
def test_pg_department_policy_change_preserves_cancellation_and_completed_replay(case, failed):
    directory, provider, service, req = service_request(case)
    policy = SubmissionPolicyService(case.db)
    policy.update(case.actor, case.company, SubmissionPolicyUpdate(mode="direct", expected_version=0, reason="模拟直接移交"))
    req.department_id = None
    if failed:
        provider.callback = lambda: (_ for _ in ()).throw(AccountingError("模拟响应丢失", 503))
    result = service.create(case.actor, req)
    policy.update(case.actor, case.company, SubmissionPolicyUpdate(mode="department", expected_version=1, reason="模拟部门审批"))
    if failed:
        with pytest.raises(AccountingError, match="所属部门"):
            service.retry(case.actor, result["request"]["id"], SecretStr(PASSWORD))
        assert service.cancel(case.actor, result["request"]["id"])["request"]["status"] == "cancelled"
    else:
        uid = provider.users[req.email]["user_id"]
        directory.revoke_company_member(case.actor, case.company, uid)
        assert service.create(case.actor, req)["password_already_created"] is True
        assert service.retry(case.actor, result["request"]["id"])["password_already_created"] is True
        with case.db.transaction(actor=case.actor) as conn:
            assert not conn.execute("SELECT * FROM company_memberships WHERE company_id=? AND user_id=?", (case.company, uid)).fetchall()


def test_http_password_account_requires_mfa_and_employee_lands_in_own_workspace(client):
    firm, company, book = setup_books(client)
    provider = PasswordProvider()
    client.app.state.admin_identity.provider = provider
    owner = headers("owner", firm)
    department = client.post(f"/api/companies/{company}/departments", headers=owner,
                             json={"name": "虚构员工部门"})
    assert department.status_code == 201, department.text
    payload = {"idempotency_key": "http-password-" + uuid4().hex, "name": "虚构公司员工",
               "email": uuid4().hex + "@example.test", "company_id": company,
               "company_role": "employee", "department_id": department.json()["id"],
               "provisioning_mode": "password", "initial_password": PASSWORD}
    original = client.provider.identities["owner"]
    client.provider.identities["owner"] = Identity(original.user_id, original.email, original.display_name)
    denied = client.post("/api/admin/account-requests", headers=owner, json=payload)
    assert denied.status_code == 403 and denied.json()["code"] == "admin_mfa_required"
    assert provider.calls == []
    client.provider.identities["owner"] = original
    created = client.post("/api/admin/account-requests", headers=owner, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["request"]["status"] == "completed"
    assert created.headers["cache-control"] == "no-store" and PASSWORD not in created.text
    user_id = provider.users[payload["email"]]["user_id"]
    client.provider.identities["new-employee"] = Identity(user_id, payload["email"], payload["name"])
    employee = headers("new-employee", firm)
    profile = client.get("/api/workspace/profile", headers=employee)
    assert profile.status_code == 200, profile.text
    assert profile.json()["role"] == "employee" and not profile.json()["can_review"]
    assert not client.get("/api/me", headers=employee).json()["permissions"]["manage_admin"]
    assert client.get("/api/workbench", headers=employee).json()["books"] == []
    assert client.get("/api/admin/companies", headers=employee).status_code == 403
    assert client.get(f"/api/books/{book}", headers=employee).status_code == 404
    replay = client.post("/api/admin/account-requests", headers=owner,
                         json={**payload, "initial_password": "different retry password"})
    assert replay.status_code == 201 and replay.json()["password_already_created"] is True
    assert len(provider.calls) == 1
    bad = client.post("/api/admin/account-requests", headers=owner,
                      json={**payload, "initial_password": "short"})
    assert bad.status_code == 422 and "short" not in bad.text
