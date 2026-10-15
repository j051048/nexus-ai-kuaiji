"""Synthetic provider identities only; no external accounts or emails are created."""
import hashlib
import json
from types import SimpleNamespace
from uuid import NAMESPACE_DNS, uuid5

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.accounting.admin_identity import AdminIdentityService
from app.accounting.admin_identity_models import AccountRequestCreate, AccountRequestRetry
from app.accounting.admin_models import AdminCompanyStatus
from app.accounting.admin_service import AdminService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyCreate, CompanyMemberGrant, DepartmentCreate
from app.auth.supabase import Identity
from app.business.models import SubmissionPolicyUpdate
from app.business.submission import SubmissionPolicyService
from test_admin_identity import FakeProvider, provider_with, request
from test_directory import case

PASSWORD = "synthetic initial password"


class PasswordProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.users = {}

    def provision_password(self, email, name, request_id, password):
        self.calls.append((email, name, request_id))
        recovered = email in self.users
        if not recovered:
            self.users[email] = {"user_id": str(uuid5(NAMESPACE_DNS, email)), "request_id": request_id, "password": password}
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        return {"user_id": self.users[email]["user_id"], "existing_user": False, "password_already_created": recovered}


@pytest.fixture
def accounts(case):
    provider = PasswordProvider()
    dep = case.directory.create_department(case.actor, case.company["id"], DepartmentCreate(name="虚构员工部门"))
    return SimpleNamespace(case=case, provider=provider, department_id=dep["id"], service=AdminIdentityService(case.db, provider))


def password_request(accounts, **changes):
    return request(accounts, **{"provisioning_mode": "password", "initial_password": PASSWORD,
                                "department_id": accounts.department_id, **changes})


def test_new_password_account_is_scoped_and_does_not_persist_password(accounts):
    a, c = accounts, accounts.case
    dep = c.directory.create_department(c.actor, c.company["id"], DepartmentCreate(name="虚构研发部"))
    result = a.service.create(c.actor, password_request(a, department_id=dep["id"]))
    assert result["request"]["status"] == "completed"
    assert result["request"]["provisioning_mode"] == "password"
    assert result["password_already_created"] is False and "activation_url" not in result
    uid = a.provider.users[result["request"]["email"]]["user_id"]
    with c.db.transaction(actor=c.actor) as conn:
        row = dict(conn.execute("SELECT * FROM admin_account_requests").fetchone())
        assert row["token_digest"] == "" and row["expires_at"] == 0
        grants = [dict(item) for item in conn.execute("SELECT * FROM company_memberships WHERE user_id=?", (uid,))]
        assert len(grants) == 1 and grants[0]["department_id"] == dep["id"] and grants[0]["role"] == "employee"
        assert not conn.execute("SELECT * FROM memberships WHERE user_id=?", (uid,)).fetchall()
        assert not conn.execute("SELECT * FROM book_access WHERE user_id=?", (uid,)).fetchall()
        persisted = json.dumps([row, *[dict(item) for item in conn.execute("SELECT * FROM audit_events")]])
        assert PASSWORD not in persisted and hashlib.sha256(PASSWORD.encode()).hexdigest() not in persisted
        assert "initial_password" not in persisted
    profile = c.directory.me(uid)
    assert profile["current_firm_id"] == c.actor.firm_id and not any(profile["permissions"].values())
    assert c.service.workbench(Actor(uid, c.actor.firm_id))["books"] == []
    with pytest.raises(AccountingError):
        a.service.accept(Identity(uid, row["email"], row["name"]), row["id"], "x" * 32)


def test_password_account_completed_replay_never_changes_password_or_restores_revoked_grant(accounts):
    a, c = accounts, accounts.case
    original = a.service.create(c.actor, password_request(a))
    uid = a.provider.users[original["request"]["email"]]["user_id"]
    c.directory.revoke_company_member(c.actor, c.company["id"], uid)
    replay = a.service.create(c.actor, password_request(a, initial_password="different retry password"))
    assert replay["password_already_created"] is True and len(a.provider.calls) == 1
    retry = a.service.retry(c.actor, original["request"]["id"])
    assert retry["password_already_created"] is True and len(a.provider.calls) == 1
    assert a.provider.users[original["request"]["email"]]["password"] == PASSWORD
    assert all(item["user_id"] != uid for item in c.directory.list_company_members(c.actor, c.company["id"])["members"])


def test_password_lost_provider_response_requires_reentry_and_recovers_original_credentials(accounts):
    a, c = accounts, accounts.case
    def unavailable():
        raise AccountingError("身份服务暂不可用", 503)
    a.provider.callback = unavailable
    result = a.service.create(c.actor, password_request(a))
    assert result["request"]["status"] == "failed"
    with pytest.raises(AccountingError, match="重新输入初始密码"):
        a.service.retry(c.actor, result["request"]["id"])
    retried = a.service.retry(c.actor, result["request"]["id"], SecretStr("different retry password"))
    assert retried["request"]["status"] == "completed" and retried["password_already_created"] is True
    assert a.provider.users[result["request"]["email"]]["password"] == PASSWORD


@pytest.mark.parametrize("change", ["cancel", "suspend", "revoke"])
def test_password_provision_rechecks_current_authorization_before_any_grant(accounts, change):
    a, c = accounts, accounts.case
    manager = c.user("temporary-company-admin")
    c.directory.grant_company_member(c.actor, c.company["id"], CompanyMemberGrant(email=manager.email, role="admin"))
    actor = Actor(manager.user_id, c.actor.firm_id)
    def revoke():
        if change == "cancel":
            a.service.cancel(c.actor, a.provider.calls[-1][2])
        elif change == "suspend":
            AdminService(c.db).change_status(c.actor, c.company["id"], AdminCompanyStatus(status="suspended", expected_version=1, reason="暂停模拟企业"))
        else:
            c.directory.revoke_company_member(c.actor, c.company["id"], manager.user_id)
    a.provider.callback = revoke
    if change == "cancel":
        assert a.service.create(actor, password_request(a))["request"]["status"] == "cancelled"
    else:
        with pytest.raises(AccountingError):
            a.service.create(actor, password_request(a))
    uid = a.provider.users["employee-new@example.test"]["user_id"]
    with c.db.transaction(actor=c.actor) as conn:
        assert not conn.execute("SELECT 1 FROM company_memberships WHERE user_id=?", (uid,)).fetchone()


def test_password_company_admin_cannot_grant_other_company_or_owner_or_firm(accounts):
    a, c = accounts, accounts.case
    manager = c.user("company-manager")
    c.directory.grant_company_member(c.actor, c.company["id"], CompanyMemberGrant(email=manager.email, role="admin"))
    actor = Actor(manager.user_id, c.actor.firm_id)
    other = c.directory.create_company(c.actor, CompanyCreate(name="另一家模拟企业"))
    for changes in ({"company_id": other["id"]}, {"company_role": "owner"}):
        with pytest.raises(AccountingError):
            a.service.create(actor, password_request(a, **changes))
    wrong_dep = c.directory.create_department(c.actor, other["id"], DepartmentCreate(name="其他企业部门"))
    with pytest.raises(AccountingError, match="部门"):
        a.service.create(c.actor, password_request(a, department_id=wrong_dep["id"]))
    assert a.provider.calls == []


def test_password_mode_is_immutable_and_activation_fingerprint_remains_compatible(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    with c.db.transaction(actor=c.actor) as conn:
        row = conn.execute("SELECT * FROM admin_account_requests WHERE id=?", (result["request"]["id"],)).fetchone()
        legacy_payload = request(a).model_dump(exclude={"idempotency_key", "initial_password", "provisioning_mode"})
        assert row["fingerprint"] == hashlib.sha256(json.dumps(legacy_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.actor) as conn:
            conn.execute("UPDATE admin_account_requests SET provisioning_mode='password' WHERE id=?", (result["request"]["id"],))
    with pytest.raises(AccountingError, match="不同人员"):
        a.service.create(c.actor, password_request(a))


@pytest.mark.parametrize("changes", [
    {"initial_password": None}, {"initial_password": "short"}, {"initial_password": "x" * 129},
    {"initial_password": 123456789012}, {"company_id": None, "company_role": None, "firm_role": "operator"},
])
def test_password_input_contract(accounts, changes):
    with pytest.raises(ValidationError):
        password_request(accounts, **changes)


def test_password_input_is_redacted_and_preserves_whitespace(accounts):
    value = "  synthetic password  "
    payload = password_request(accounts, initial_password=value)
    assert payload.initial_password.get_secret_value() == value
    assert value not in repr(payload)
    assert AccountRequestRetry(initial_password=value).initial_password.get_secret_value() == value


def test_department_policy_requires_employee_department_before_provider_call(accounts):
    a, c = accounts, accounts.case
    with pytest.raises(AccountingError, match="所属部门"):
        a.service.create(c.actor, password_request(a, department_id=None))
    assert a.provider.calls == []
    with c.db.transaction(actor=c.actor) as conn:
        assert not conn.execute("SELECT * FROM admin_account_requests").fetchall()


@pytest.mark.parametrize("failed", [True, False])
def test_policy_change_allows_cancel_or_completed_replay_without_regrant(accounts, failed):
    a, c = accounts, accounts.case
    policy = SubmissionPolicyService(c.db)
    policy.update(c.actor, c.company["id"], SubmissionPolicyUpdate(mode="direct", expected_version=0, reason="模拟直接移交"))
    if failed:
        def unavailable():
            raise AccountingError("模拟网络响应丢失", 503)
        a.provider.callback = unavailable
    payload = password_request(a, department_id=None)
    result = a.service.create(c.actor, payload)
    policy.update(c.actor, c.company["id"], SubmissionPolicyUpdate(mode="department", expected_version=1, reason="模拟启用部门审批"))
    assert a.service.list_requests(c.actor)["requests"][0]["id"] == result["request"]["id"]
    if failed:
        with pytest.raises(AccountingError, match="所属部门"):
            a.service.retry(c.actor, result["request"]["id"], SecretStr(PASSWORD))
        assert a.service.cancel(c.actor, result["request"]["id"])["request"]["status"] == "cancelled"
    else:
        uid = a.provider.users[payload.email]["user_id"]
        c.directory.revoke_company_member(c.actor, c.company["id"], uid)
        assert a.service.create(c.actor, payload)["password_already_created"] is True
        assert a.service.retry(c.actor, result["request"]["id"])["password_already_created"] is True
        assert all(item["user_id"] != uid for item in c.directory.list_company_members(c.actor, c.company["id"])["members"])
    assert len(a.provider.calls) == 1


def test_policy_change_during_provider_call_cannot_publish_unready_employee_grant(accounts):
    a, c = accounts, accounts.case
    policy = SubmissionPolicyService(c.db)
    policy.update(c.actor, c.company["id"], SubmissionPolicyUpdate(mode="direct", expected_version=0, reason="模拟直接移交"))
    a.provider.callback = lambda: policy.update(c.actor, c.company["id"], SubmissionPolicyUpdate(mode="department", expected_version=1, reason="并发启用部门审批"))
    with pytest.raises(AccountingError, match="所属部门"):
        a.service.create(c.actor, password_request(a, department_id=None))
    uid = a.provider.users["employee-new@example.test"]["user_id"]
    with c.db.transaction(actor=c.actor) as conn:
        row = conn.execute("SELECT * FROM admin_account_requests").fetchone()
        assert row["status"] == "pending"
        assert not conn.execute("SELECT * FROM company_memberships WHERE user_id=?", (uid,)).fetchall()
    assert a.service.cancel(c.actor, row["id"])["request"]["status"] == "cancelled"


def test_provider_password_creation_never_sends_mail_and_recovers_lost_create_response():
    email = "synthetic-new@example.test"
    user = {"id": str(uuid5(NAMESPACE_DNS, email)), "email": email, "email_confirmed_at": "2026-01-01",
            "app_metadata": {"accounting_provision_request": "owned", "accounting_provision_mode": "password"}}
    created = False
    calls = []
    def handler(req):
        nonlocal created
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json={"users": [user] if created else []})
        body = json.loads(req.content)
        assert req.method == "POST" and req.url.path == "/auth/v1/admin/users"
        assert body["password"] == PASSWORD and body["email_confirm"] is True
        assert body["app_metadata"] == user["app_metadata"]
        created = True
        raise httpx.ReadTimeout("synthetic network interruption")
    provider = provider_with(handler)
    result = provider.provision_password(email, "虚构员工", "owned", PASSWORD)
    assert result["password_already_created"] is True
    assert provider.provision_password(email, "虚构员工", "owned", "different retry password")["password_already_created"] is True
    assert sum(method == "POST" for method, _ in calls) == 1


@pytest.mark.parametrize("metadata,confirmed", [({}, True), ({"accounting_provision_request": "different", "accounting_provision_mode": "password"}, True),
    ({"accounting_provision_request": "owned"}, True), ({"accounting_provision_request": "owned", "accounting_provision_mode": "password"}, False)])
def test_provider_never_takes_over_existing_or_unconfirmed_users(metadata, confirmed):
    user = {"id": str(uuid5(NAMESPACE_DNS, "existing")), "email": "existing@example.test", "app_metadata": metadata}
    if confirmed:
        user["email_confirmed_at"] = "2026-01-01"
    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json={"users": [user]})
    with pytest.raises(AccountingError, match="已有账号"):
        provider_with(handler).provision_password(user["email"], "已有账号", "owned", PASSWORD)
