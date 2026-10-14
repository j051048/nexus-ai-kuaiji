from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.admin_identity import AdminIdentityService
from app.accounting.admin_models import AdminCompanyStatus
from app.accounting.admin_service import AdminService
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyMemberGrant, FirmMemberGrant
from app.auth.supabase import Identity
from test_admin_identity import FakeProvider, request, recipient
from test_postgres_integration import ADMIN_URL, APP_URL, case, peer, postgres
from scripts.bootstrap_admin import bootstrap_owner
from scripts.migrate_accounting import admin_connection

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要本机隔离 PostgreSQL 测试连接")


@pytest.fixture
def accounts(case):
    c = SimpleNamespace(**vars(case))
    c.company = {"id": case.company}
    c.book = {"id": case.book}
    c.directory = DirectoryService(c.db)
    def user(name):
        identity = Identity(str(uuid4()), uuid4().hex + "@example.test", name)
        c.directory.register_identity(identity)
        return identity
    c.user = user
    return SimpleNamespace(case=c, provider=FakeProvider(), service=AdminIdentityService(c.db, FakeProvider()))


def test_pg_invitation_accepts_scoped_employee_and_audits(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    identity, token = recipient(result)
    assert a.service.accept(identity, result["request"]["id"], token)["completed"]
    with c.db.transaction(actor=c.actor) as conn:
        assert conn.execute("SELECT role FROM company_memberships WHERE company_id=? AND user_id=?", (c.company["id"], identity.user_id)).fetchone()[0] == "employee"
        audit = conn.execute("SELECT details FROM audit_events WHERE object_id=? AND action='admin.account.accept'", (result["request"]["id"],)).fetchone()[0]
        assert identity.user_id in audit
    assert c.service.workbench(Actor(identity.user_id, c.actor.firm_id))["books"] == []


def test_pg_foreign_firm_and_employee_cannot_read_or_change_requests(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    for actor in (peer(c, firm_role="owner", other_firm=True), peer(c, firm_role="viewer")):
        with c.db.transaction(write=True, actor=actor) as conn:
            assert conn.execute("SELECT * FROM admin_account_requests WHERE id=?", (result["request"]["id"],)).fetchall() == []
            assert conn.execute("UPDATE admin_account_requests SET status='cancelled' WHERE id=?", (result["request"]["id"],)).rowcount == 0


def test_pg_native_scope_and_identity_mutation_denied(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    for statement in ("UPDATE admin_account_requests SET company_role='admin' WHERE id=?", "UPDATE admin_account_requests SET email='evil@example.test' WHERE id=?", "DELETE FROM admin_account_requests WHERE id=?"):
        with pytest.raises(Exception):
            with c.db.transaction(write=True, actor=c.actor) as conn:
                conn.execute(statement, (result["request"]["id"],))


def test_pg_native_completion_without_grant_is_denied(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    identity, token = recipient(result)
    c.directory.register_identity(identity)
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.actor) as conn:
            conn.execute("UPDATE admin_account_requests SET status='completed',completed_by=? WHERE id=?", (identity.user_id, result["request"]["id"]))


def test_pg_suspended_company_cannot_complete_invitation(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    identity, token = recipient(result)
    AdminService(c.db).change_status(c.actor, c.company["id"], AdminCompanyStatus(status="suspended", expected_version=1, reason="暂停测试公司"))
    with pytest.raises(AccountingError):
        a.service.accept(identity, result["request"]["id"], token)
    with c.db.transaction(write=True, actor=c.actor) as conn:
        assert conn.execute("UPDATE admin_account_requests SET status='completed',completed_by=? WHERE id=?", (identity.user_id, result["request"]["id"])).rowcount == 0


def test_pg_originator_revocation_blocks_both_service_and_native_completion(accounts):
    a, c = accounts, accounts.case
    manager = c.user("临时管理员")
    c.directory.grant_firm_member(c.actor, FirmMemberGrant(email=manager.email, role="admin"))
    result = a.service.create(Actor(manager.user_id, c.actor.firm_id), request(a))
    identity, token = recipient(result)
    c.directory.revoke_firm_member(c.actor, manager.user_id)
    with pytest.raises(AccountingError):
        a.service.accept(identity, result["request"]["id"], token)
    c.directory.grant_company_member(c.actor, c.company["id"], CompanyMemberGrant(email=identity.email, role="employee"))
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.actor) as conn:
            conn.execute("UPDATE admin_account_requests SET status='completed',completed_by=? WHERE id=?", (identity.user_id, result["request"]["id"]))


def test_pg_account_audit_cannot_claim_another_scope_or_unaccepted_recipient(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    for company, action in ((None, "admin.account.link_created"), (c.company["id"], "admin.account.accept")):
        with pytest.raises(Exception):
            with c.db.transaction(write=True, actor=c.actor) as conn:
                conn.execute("INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at,company_id) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP::text,?)",
                             (c.actor.firm_id, c.actor.user_id, action, result["request"]["id"], '{"accepted_by":"forged"}', company))


def test_pg_identity_role_cannot_mutate_pending_invitation(accounts):
    a, c = accounts, accounts.case
    result = a.service.create(c.actor, request(a))
    with c.db.transaction(system=True) as conn:
        assert conn.execute("SELECT id FROM admin_account_requests WHERE id=?", (result["request"]["id"],)).fetchone()
    with pytest.raises(Exception):
        with c.db.transaction(write=True, system=True) as conn:
            conn.execute("UPDATE admin_account_requests SET status='cancelled' WHERE id=?", (result["request"]["id"],))


def test_pg_explicit_bootstrap_is_once_only_and_runtime_cannot_change_receipt(accounts):
    c = accounts.case
    target = {"id": str(uuid4()), "email": uuid4().hex + "@example.test"}
    with admin_connection(ADMIN_URL) as conn:
        result = bootstrap_owner(conn, target, name="虚构负责人", firm_name="已有事务所", firm_id=c.actor.firm_id)
    assert result["already_bootstrapped"] is False
    with c.db.transaction(actor=Actor(target["id"], c.actor.firm_id)) as conn:
        assert conn.execute("SELECT role FROM memberships WHERE user_id=? AND firm_id=?", (target["id"], c.actor.firm_id)).fetchone()[0] == "owner"
    with admin_connection(ADMIN_URL) as conn:
        assert bootstrap_owner(conn, target, name="虚构负责人", firm_name="已有事务所", firm_id=c.actor.firm_id)["already_bootstrapped"]
        with pytest.raises(ValueError, match="已完成初始化"):
            bootstrap_owner(conn, {"id": str(uuid4()), "email": "other@example.test"}, name="另一人", firm_name="已有事务所", firm_id=c.actor.firm_id)
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.actor) as conn:
            conn.execute("DELETE FROM admin_bootstrap_records WHERE firm_id=?", (c.actor.firm_id,))
