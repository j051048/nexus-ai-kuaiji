import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_DNS, uuid5

import httpx
import jwt
import pytest
from pydantic import ValidationError

from app.accounting.admin_identity import AdminIdentityService
from app.accounting.admin_identity_models import AccountRequestCreate
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyCreate, CompanyMemberGrant, FirmMemberGrant
from app.auth.admin import SupabaseAdmin
from app.auth.supabase import Identity
from test_directory import case


class FakeProvider:
    configured = True
    public_app_url = "https://demo.example.test"

    def __init__(self):
        self.calls = []
        self.callback = None
        self.existing = False

    def provision(self, email, name, request_id):
        self.calls.append((email, name, request_id))
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        return {"user_id": str(uuid5(NAMESPACE_DNS, email)), "existing_user": self.existing, "token_hash": "synthetic-otp-hash"}


@pytest.fixture
def accounts(case):
    provider = FakeProvider()
    return SimpleNamespace(case=case, provider=provider, service=AdminIdentityService(case.db, provider))


def request(accounts, **overrides):
    return AccountRequestCreate(**{
        "idempotency_key": "account-request-001", "name": "虚构员工", "email": "employee-new@example.test",
        "company_id": accounts.case.company["id"], "company_role": "employee", **overrides,
    })


def recipient(response):
    fragment = parse_qs(urlsplit(response["activation_url"]).fragment)
    row = response["request"]
    return Identity(str(uuid5(NAMESPACE_DNS, row["email"])), row["email"], row["name"]), fragment["token"][0]


def test_account_creation_requires_recipient_acceptance_and_persists_no_secrets(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    assert result["request"]["status"] == "awaiting_activation"
    identity, token = recipient(result)
    with c.db.transaction(actor=c.actor) as conn:
        row = dict(conn.execute("SELECT * FROM admin_account_requests").fetchone())
        assert row["token_digest"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in json.dumps(row) and "synthetic-otp-hash" not in json.dumps(row)
        assert conn.execute("SELECT 1 FROM company_memberships WHERE user_id=?", (identity.user_id,)).fetchone() is None
    public = a.service.list_requests(c.actor)
    assert "token_digest" not in json.dumps(public) and "activation_url" not in json.dumps(public)
    assert a.service.accept(identity, result["request"]["id"], token)["completed"]
    members = c.directory.list_company_members(c.actor, c.company["id"])["members"]
    assert next(m for m in members if m["user_id"] == identity.user_id)["role"] == "employee"
    assert c.service.workbench(Actor(identity.user_id, c.actor.firm_id))["books"] == []


def test_duplicate_create_does_not_issue_another_secret_or_change_roles(accounts):
    a, c = accounts, accounts.case
    first = a.service.create(c.actor, request(a))
    second = a.service.create(c.actor, request(a))
    assert first["request"]["id"] == second["request"]["id"]
    assert "activation_url" not in second and len(a.provider.calls) == 1
    with pytest.raises(AccountingError, match="不同人员"):
        a.service.create(c.actor, request(a, company_role="admin"))


def test_rotated_invitation_invalidates_old_token(accounts):
    a, c = accounts, accounts.case
    first = a.service.create(c.actor, request(a))
    identity, old_token = recipient(first)
    second = a.service.retry(c.actor, first["request"]["id"])
    with pytest.raises(AccountingError, match="不匹配"):
        a.service.accept(identity, first["request"]["id"], old_token)
    assert a.service.accept(identity, first["request"]["id"], recipient(second)[1])["completed"]


def test_wrong_identity_and_expired_link_are_denied(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    identity, token = recipient(result)
    other = Identity(identity.user_id, "other@example.test", "别的邮箱")
    with pytest.raises(AccountingError):
        a.service.accept(other, result["request"]["id"], token)
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("UPDATE admin_account_requests SET expires_at=0 WHERE id=?", (result["request"]["id"],))
    with pytest.raises(AccountingError, match="失效"):
        a.service.accept(identity, result["request"]["id"], token)


def test_completed_replay_never_restores_a_revoked_company_membership(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    identity, token = recipient(result)
    a.service.accept(identity, result["request"]["id"], token)
    c.directory.revoke_company_member(c.actor, c.company["id"], identity.user_id)
    assert a.service.accept(identity, result["request"]["id"], token)["completed"]
    assert all(m["user_id"] != identity.user_id for m in c.directory.list_company_members(c.actor, c.company["id"])["members"])


def test_request_originator_revocation_blocks_final_accept(accounts):
    a, c = accounts, accounts.case
    manager = c.user("manager")
    c.directory.grant_firm_member(c.actor, FirmMemberGrant(email=manager.email, role="admin"))
    actor = Actor(manager.user_id, c.actor.firm_id)
    result = a.service.create(actor, request(a))
    c.directory.revoke_firm_member(c.actor, manager.user_id)
    with pytest.raises(AccountingError):
        a.service.accept(*[recipient(result)[0], result["request"]["id"], recipient(result)[1]])
    original_calls = len(a.provider.calls)
    with pytest.raises(AccountingError, match="发起人的授权"):
        a.service.retry(c.actor, result["request"]["id"])
    assert len(a.provider.calls) == original_calls


def test_company_admin_scope_cannot_create_firm_or_other_company_accounts(accounts):
    a, c = accounts, accounts.case
    manager = c.user("companyadmin")
    c.directory.grant_company_member(c.actor, c.company["id"], CompanyMemberGrant(email=manager.email, role="admin"))
    actor = Actor(manager.user_id, c.actor.firm_id)
    other = c.directory.create_company(c.actor, CompanyCreate(name="另一个客户"))
    for change in ({"company_id": other["id"]}, {"company_id": None, "company_role": None, "firm_role": "admin"}, {"company_role": "owner"}):
        with pytest.raises(AccountingError):
            a.service.create(actor, request(a, **change))
    assert a.service.create(actor, request(a))["request"]["status"] == "awaiting_activation"


def test_provider_failure_can_retry_without_granting_partial_permissions(accounts):
    a, c = accounts, accounts.case
    def failure():
        raise AccountingError("身份服务暂时不可用", 503)
    a.provider.callback = failure
    first = a.service.create(c.actor, request(a))
    assert first["request"]["status"] == "failed" and "activation_url" not in first
    second = a.service.retry(c.actor, first["request"]["id"])
    assert second["request"]["attempts"] == 2
    a.service.accept(recipient(second)[0], first["request"]["id"], recipient(second)[1])


def test_cancel_during_provider_call_never_returns_activation_url(accounts):
    a, c = accounts, accounts.case
    def cancel():
        request_id = a.provider.calls[-1][2]
        a.service.cancel(c.actor, request_id)
    a.provider.callback = cancel
    result = a.service.create(c.actor, request(a))
    assert result["request"]["status"] == "cancelled" and "activation_url" not in result


def test_older_attempt_cannot_return_secret_for_newer_lease(accounts):
    a, c = accounts, accounts.case
    newer = []
    def overlap():
        request_id = a.provider.calls[-1][2]
        with c.db.transaction(write=True, actor=c.actor) as conn:
            conn.execute("UPDATE admin_account_requests SET lease_until=0 WHERE id=?", (request_id,))
        newer.append(a.service.retry(c.actor, request_id))
    a.provider.callback = overlap
    older = a.service.create(c.actor, request(a))
    assert "activation_url" not in older and "activation_url" in newer[0]
    identity, token = recipient(newer[0])
    assert a.service.accept(identity, older["request"]["id"], token)["completed"]


def test_tampering_with_request_scope_is_rejected_by_database(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    for sql in ("UPDATE admin_account_requests SET company_role='admin' WHERE id=?", "DELETE FROM admin_account_requests WHERE id=?"):
        with pytest.raises(Exception):
            with c.db.transaction(write=True, actor=c.actor) as conn:
                conn.execute(sql, (result["request"]["id"],))


def test_existing_user_activation_never_resets_credentials(accounts):
    a, c = accounts, accounts.case
    a.provider.existing = True
    result = a.service.create(c.actor, request(a))
    fragment = parse_qs(urlsplit(result["activation_url"]).fragment)
    assert fragment["mode"] == ["existing"] and "token_hash" not in fragment
    assert result["request"]["existing_user"] is True


def test_firm_member_provision_requires_owner_and_still_needs_book_assignment(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a, company_id=None, company_role=None, firm_role="reviewer"))
    identity, token = recipient(result)
    a.service.accept(identity, result["request"]["id"], token)
    assert c.directory.me(identity.user_id)["current_firm_id"] == c.actor.firm_id
    assert c.service.workbench(Actor(identity.user_id, c.actor.firm_id))["books"] == []


@pytest.mark.parametrize("changes", [{"name": "  "}, {"idempotency_key": "  "}, {"password": "should-not-be-accepted"}, {"company_id": None}, {"firm_role": "owner"}])
def test_invalid_or_password_bearing_requests_are_rejected(accounts, changes):
    with pytest.raises(ValidationError):
        request(accounts, **changes)


def provider_with(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SupabaseAdmin("https://identity.example.test", "sb_secret_test", "https://demo.example.test", client)


def test_real_client_does_not_mutate_existing_verified_user_or_send_mail():
    user = {"id": str(uuid5(NAMESPACE_DNS, "existing")), "email": "existing@example.test", "email_confirmed_at": "2026-01-01"}
    seen = []
    def handler(req):
        seen.append(req.method + " " + req.url.path)
        assert req.method == "GET"
        return httpx.Response(200, json={"users": [user]})
    provider = provider_with(handler)
    result = provider.provision(user["email"], "已有账号", "request-one")
    assert result == {"user_id": user["id"], "existing_user": True}
    assert seen == ["GET /auth/v1/admin/users"]


def test_client_resumes_owned_unconfirmed_identity_and_only_generates_invite():
    user = {"id": str(uuid5(NAMESPACE_DNS, "owned")), "email": "new@example.test", "app_metadata": {"accounting_provision_request": "owned-request"}}
    seen = []
    def handler(req):
        seen.append(req.url.path)
        if req.method == "GET":
            return httpx.Response(200, json={"users": [user]})
        assert req.url.path == "/auth/v1/admin/generate_link"
        assert json.loads(req.content)["type"] == "invite"
        return httpx.Response(200, json={**user, "hashed_token": "synthetic-only"})
    result = provider_with(handler).provision(user["email"], "新员工", "owned-request")
    assert result["existing_user"] is False
    assert seen == ["/auth/v1/admin/users", "/auth/v1/admin/generate_link"]


def test_unknown_unconfirmed_identity_is_not_taken_over():
    user = {"id": str(uuid5(NAMESPACE_DNS, "unowned")), "email": "new@example.test", "app_metadata": {}}
    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json={"users": [user]})
    assert provider_with(handler).provision(user["email"], "新员工", "request")["existing_user"] is True


def test_provider_errors_never_leak_body_or_secret():
    def handler(req):
        return httpx.Response(500, json={"message": "PRIVATE_PROVIDER_BODY", "key": "sb_secret_test"})
    provider = provider_with(handler)
    with pytest.raises(AccountingError) as failure:
        provider.provision("new@example.test", "测试", "request")
    assert "PRIVATE_PROVIDER_BODY" not in str(failure.value)
    assert "sb_secret_test" not in repr(provider) + str(failure.value)


def test_new_user_creation_is_unconfirmed_and_owned_without_password_or_email_send():
    user_id = str(uuid5(NAMESPACE_DNS, "synthetic-create"))
    seen = []
    def handler(req):
        seen.append(req.url.path)
        if req.method == "GET":
            return httpx.Response(200, json={"users": []})
        body = json.loads(req.content)
        if req.url.path == "/auth/v1/admin/users":
            assert body["email_confirm"] is False
            assert "password" not in body
            assert body["user_metadata"] == {"display_name": "虚构员工"}
            assert body["app_metadata"] == {"accounting_provision_request": "owned"}
            return httpx.Response(200, json={"id": user_id, **body})
        assert req.url.path == "/auth/v1/admin/generate_link" and body["type"] == "invite"
        return httpx.Response(200, json={"id": user_id, "email": "new@example.test", "hashed_token": "synthetic-hash"})
    assert provider_with(handler).provision("new@example.test", "虚构员工", "owned")["existing_user"] is False
    assert seen == ["/auth/v1/admin/users", "/auth/v1/admin/users", "/auth/v1/admin/generate_link"]


def test_bootstrap_existing_verified_identity_never_changes_its_password():
    user = {"id": str(uuid5(NAMESPACE_DNS, "bootstrap-existing")), "email": "existing@example.test", "email_confirmed_at": "2026-01-01"}
    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json={"users": [user]})
    assert provider_with(handler).bootstrap_user(user["email"], "负责人", "synthetic-password")["id"] == user["id"]


@pytest.mark.parametrize("key", ["sb_publishable_public", "plain-invalid", jwt.encode({"role": "anon"}, "synthetic", algorithm="HS256")])
def test_admin_key_rejects_public_and_unknown_keys(key):
    with pytest.raises(AccountingError):
        SupabaseAdmin("https://identity.example.test", key, "https://demo.example.test").validate()


@pytest.mark.parametrize("url", ["http://identity.example.test", "https://localhost", "https://127.0.0.1", "https://identity.example.test/path", "https://someone:secret@identity.example.test"])
def test_admin_secret_not_sent_to_invalid_urls(url):
    with pytest.raises(AccountingError):
        SupabaseAdmin(url, "sb_secret_test", "https://demo.example.test").validate()
