"""Real PostgreSQL checks, enabled only with dedicated explicit test DSNs."""
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.models import ApprovalRequest, BookCreate, ExportRequest, ImportRequest
from app.accounting.directory import DirectoryService
from app.accounting.models import BookMemberGrant, CompanyMemberGrant, DepartmentCreate, FirmCreate
from app.accounting.service import AccountingError, AccountingService, Actor

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要独立测试 PostgreSQL 的管理员及受限运行 DSN")


@pytest.fixture(scope="module")
def postgres():
    import psycopg
    from scripts.migrate_accounting import migrate
    migrate(ADMIN_URL)
    return Database(APP_URL), psycopg


@pytest.fixture
def case(postgres):
    db, psycopg = postgres
    suffix = uuid4().hex
    actor = Actor(user_id="pg-user-" + suffix, firm_id="pg-firm-" + suffix)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)", (actor.user_id, "测试会计", suffix + "@example.test"))
        conn.execute("INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)", (actor.firm_id, "隔离测试事务所", actor.user_id))
        conn.execute("INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')", (actor.firm_id, actor.user_id))
    service = AccountingService(db)
    book = service.create_book(actor, BookCreate(name="真实PG测试企业", period="2026-10"))
    return SimpleNamespace(db=db, psycopg=psycopg, service=service, actor=actor, book=book["id"], company=book["company_id"])


def request(**changes):
    row = {"external_id": "bank-001", "date": "2026-10-01", "description": "办公用品", "counterparty": "测试供应商", "amount": "0.30", "direction": "expense", "evidence_ref": "测试发票"}
    return ImportRequest(source_name="PG集成测试", rows=[{**row, **changes}])


def create_draft(case, **changes):
    case.service.import_sources(case.actor, case.book, request(**changes))
    case.service.suggestions(case.actor, case.book)
    return case.service.get_book(case.actor, case.book)["drafts"][0]


def peer(case, firm_role=None, book_role=None, company_role=None, other_firm=False):
    actor = Actor(user_id="pg-peer-" + uuid4().hex, firm_id="other-firm-" + uuid4().hex if other_firm else case.actor.firm_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.users(id,name) VALUES(%s,'测试协作者')", (actor.user_id,))
        if other_firm:
            conn.execute("INSERT INTO accounting.firms(id,name) VALUES(%s,'其他测试事务所')", (actor.firm_id,))
        if firm_role:
            conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,%s)", (actor.firm_id, actor.user_id, firm_role))
        if company_role:
            conn.execute("INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,%s)", (case.company, actor.user_id, company_role))
        if book_role:
            conn.execute("INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,%s)", (case.book, case.actor.firm_id, actor.user_id, book_role))
    return actor


def test_postgres_full_workflow_precision_dedup_close_and_receipt(case):
    draft = create_draft(case, amount="999999999999.99")
    assert draft["source_amount"] == "999999999999.99"
    assert case.service.import_sources(case.actor, case.book, request(amount="999999999999.99")) == {"imported": 0, "duplicates": 1}
    case.service.approve(case.actor, case.book, draft["id"], ApprovalRequest(expected_version=1))
    export = ExportRequest(draft_ids=[draft["id"]], idempotency_key="pg-complete-export")
    receipt = case.service.export(case.actor, case.book, export)
    case.service.close_period(case.actor, case.book, "2026-10")
    assert case.service.export(case.actor, case.book, export) == receipt
    assert case.service.get_export(case.actor, case.book, receipt["id"]) == receipt
    with pytest.raises(AccountingError, match="已关闭"):
        case.service.import_sources(case.actor, case.book, request(external_id="new"))
    with case.db.transaction(actor=case.actor) as conn:
        row = conn.execute("SELECT sum(amount_cents) AS total FROM sources WHERE book_id=?", (case.book,)).fetchone()
        assert row[0] == row["total"] == 99999999999999 and isinstance(row[0], int)


def test_postgres_concurrent_import_approval_and_export_are_atomic(case):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: case.service.import_sources(case.actor, case.book, request()), range(4)))
    assert sum(x["imported"] for x in results) == 1
    case.service.suggestions(case.actor, case.book)
    draft = case.service.get_book(case.actor, case.book)["drafts"][0]
    def approve(_):
        try:
            return case.service.approve(case.actor, case.book, draft["id"], ApprovalRequest(expected_version=1))["status"]
        except AccountingError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(approve, range(4)))
    assert results.count("approved") == 1 and results.count(409) == 3
    export = ExportRequest(draft_ids=[draft["id"]], idempotency_key="pg-concurrent-export")
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: case.service.export(case.actor, case.book, export), range(4)))
    assert all(item == receipts[0] for item in receipts)
    assert len(case.service.list_exports(case.actor, case.book)["exports"]) == 1


def test_postgres_service_bypass_queries_still_respect_rls(case):
    create_draft(case)
    actors = [peer(case, firm_role="reviewer"), peer(case, firm_role="owner", other_firm=True), peer(case, company_role="employee", book_role="reviewer")]
    for actor in actors:
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT * FROM books WHERE id=?", (case.book,)).fetchall() == []
            assert conn.execute("SELECT * FROM sources WHERE book_id=?", (case.book,)).fetchall() == []
            assert conn.execute("SELECT * FROM drafts WHERE book_id=?", (case.book,)).fetchall() == []
    finance = peer(case, company_role="finance", book_role="reviewer")
    assert case.service.get_book(finance, case.book)["sources"]
    with case.db.transaction(actor=finance) as conn:
        assert conn.execute("SELECT * FROM sources WHERE book_id=?", (case.book,)).fetchone()


def test_postgres_viewer_cannot_write_and_operator_cannot_approve(case):
    draft = create_draft(case)
    viewer = peer(case, firm_role="viewer", book_role="reviewer")
    with case.db.transaction(write=True, actor=viewer) as conn:
        assert conn.execute("UPDATE drafts SET description='forbidden' WHERE id=?", (draft["id"],)).rowcount == 0
    with pytest.raises(AccountingError) as error:
        case.service.import_sources(viewer, case.book, request(external_id="forbidden"))
    assert error.value.status == 403
    operator = peer(case, firm_role="operator", book_role="reviewer")
    with pytest.raises(AccountingError) as error:
        case.service.approve(operator, case.book, draft["id"], ApprovalRequest(expected_version=1))
    assert error.value.status == 403


def test_postgres_suspended_customer_and_revoked_user_lose_access_immediately(case):
    create_draft(case)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s", (case.actor.firm_id, case.company))
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT * FROM sources WHERE book_id=?", (case.book,)).fetchall() == []
    with pytest.raises(AccountingError) as error:
        case.service.get_book(case.actor, case.book)
    assert error.value.status == 404
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.firm_clients SET status='active' WHERE firm_id=%s AND company_id=%s", (case.actor.firm_id, case.company))
        conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (case.actor.user_id,))
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT * FROM sources WHERE book_id=?", (case.book,)).fetchall() == []
    with pytest.raises(AccountingError) as error:
        case.service.get_book(case.actor, case.book)
    assert error.value.status == 403


def test_postgres_read_transaction_keeps_consistent_snapshot(case):
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE book_id=?", (case.book,)).fetchone()[0] == 0
        case.service.import_sources(case.actor, case.book, request())
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE book_id=?", (case.book,)).fetchone()[0] == 0
    assert len(case.service.get_book(case.actor, case.book)["sources"]) == 1


def test_postgres_system_role_cannot_access_ledger_and_runtime_is_restricted(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(system=True) as conn:
            conn.execute("SELECT * FROM sources")
    with pytest.raises(RuntimeError, match="禁止"):
        Database(ADMIN_URL)
    with pytest.raises(ValueError, match="actor"):
        with case.db.transaction():
            pass
    with case.db.transaction(actor=case.actor) as conn:
        tables = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='accounting' AND relkind='r'").fetchall()
        assert tables and all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in tables)


def test_postgres_onboarding_first_owner_and_multi_firm_discovery(postgres):
    db, _ = postgres
    user_id = "pg-new-user-" + uuid4().hex
    with db.transaction(write=True, system=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES(?,'新会计')", (user_id,))
    firms = ["pg-new-firm-" + uuid4().hex for _ in range(2)]
    for firm in firms:
        with db.transaction(write=True, actor=Actor(user_id=user_id, firm_id="")) as conn:
            conn.execute("INSERT INTO firms(id,name,created_by) VALUES(?,'新事务所',?)", (firm, user_id))
            conn.execute("INSERT INTO memberships VALUES(?,?,'owner')", (firm, user_id))
    with db.transaction(actor=Actor(user_id=user_id, firm_id="")) as conn:
        discovered = {row[0] for row in conn.execute("SELECT f.id FROM firms f JOIN memberships m ON m.firm_id=f.id WHERE m.user_id=?", (user_id,))}
        assert set(firms) <= discovered


def test_postgres_export_audit_failure_rolls_back_status_and_receipt(case, monkeypatch):
    draft = create_draft(case)
    case.service.approve(case.actor, case.book, draft["id"], ApprovalRequest(expected_version=1))
    def fail(*args):
        raise RuntimeError("模拟审计失败")
    monkeypatch.setattr(case.service, "_audit", fail)
    with pytest.raises(RuntimeError, match="模拟"):
        case.service.export(case.actor, case.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key="pg-rollback-export"))
    assert case.service.list_exports(case.actor, case.book) == {"exports": []}
    assert case.service.get_book(case.actor, case.book)["drafts"][0]["status"] == "approved"


def test_postgres_company_admin_cannot_change_mixed_firm_member_book_permissions(case):
    directory = DirectoryService(case.db)
    client_admin = peer(case, company_role="admin", book_role="viewer")
    mixed = peer(case, firm_role="operator", company_role="finance", book_role="editor")
    for action in (
        lambda: directory.grant_book_member(client_admin, case.book, mixed.user_id, BookMemberGrant(role="reviewer")),
        lambda: directory.revoke_book_member(client_admin, case.book, mixed.user_id),
    ):
        with pytest.raises(AccountingError) as error:
            action()
        assert error.value.status == 403


def test_postgres_directory_grants_department_and_client_firm_discovery(case):
    directory = DirectoryService(case.db)
    department = directory.create_department(case.actor, case.company, DepartmentCreate(name="销售部"))
    user_id = "pg-client-" + uuid4().hex
    email = uuid4().hex + "@example.test"
    with case.db.transaction(write=True, system=True) as conn:
        conn.execute("INSERT INTO users(id,name,email) VALUES(?,?,?)", (user_id, "企业财务", email))
    directory.grant_company_member(case.actor, case.company, CompanyMemberGrant(email=email, role="finance", department_id=department["id"]))
    directory.grant_book_member(case.actor, case.book, user_id, BookMemberGrant(role="reviewer"))
    discovered = directory.me(user_id)
    assert discovered["current_firm_id"] == case.actor.firm_id
    assert discovered["permissions"]["manage_firm"] is False
    finance = Actor(user_id, case.actor.firm_id)
    draft = create_draft(case)
    assert case.service.approve(finance, case.book, draft["id"], ApprovalRequest(expected_version=1))["status"] == "approved"
    directory.revoke_company_member(case.actor, case.company, user_id)
    with pytest.raises(AccountingError) as error:
        case.service.get_book(finance, case.book)
    assert error.value.status == 404


def test_postgres_first_owner_cannot_claim_another_created_firm(case):
    stranger = peer(case)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=stranger) as conn:
            conn.execute("INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'owner')", (case.actor.firm_id, stranger.user_id))


def test_postgres_firm_revocation_removes_grants_for_suspended_customers(case):
    directory = DirectoryService(case.db)
    member = peer(case, firm_role="reviewer", book_role="reviewer")
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s", (case.actor.firm_id, case.company))
    assert directory.revoke_firm_member(case.actor, member.user_id) == {"revoked": True}
    with case.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT COUNT(*) FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (case.book, member.user_id)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM accounting.memberships WHERE firm_id=%s AND user_id=%s", (case.actor.firm_id, member.user_id)).fetchone()[0] == 0


@pytest.mark.parametrize("table", ["sources", "source_evidence", "exports"])
def test_postgres_runtime_cannot_rewrite_source_or_delivered_receipt(case, table):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.actor) as conn:
            conn.execute(f"UPDATE {table} SET book_id=? WHERE book_id=?", (case.book, case.book))


def test_postgres_close_period_and_import_race_cannot_write_after_close(case):
    import threading
    barrier = threading.Barrier(2)
    def import_source():
        barrier.wait()
        try:
            return case.service.import_sources(case.actor, case.book, request())
        except AccountingError as error:
            return error.status
    def close():
        barrier.wait()
        return case.service.close_period(case.actor, case.book, "2026-10")
    with ThreadPoolExecutor(max_workers=2) as pool:
        imported = pool.submit(import_source)
        closed = pool.submit(close)
        assert closed.result()["status"] == "closed"
        result = imported.result()
    assert result in (403, {"imported": 1, "duplicates": 0})
    assert len(case.service.get_book(case.actor, case.book)["sources"]) == (0 if result == 403 else 1)
    with pytest.raises(AccountingError, match="已关闭"):
        case.service.import_sources(case.actor, case.book, request(external_id="later"))
