"""Real SQL boundary checks for employee originals, independent from API guards."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")
STAMP = "2026-10-11T12:00:00+00:00"
BUSINESS_TABLES = ("business_events", "business_versions", "business_attachments", "business_actions", "business_mappings")


@pytest.fixture(scope="module")
def business_postgres():
    import psycopg
    from scripts.migrate_accounting import migrate

    migrate(ADMIN_URL)
    return Database(APP_URL), psycopg


@pytest.fixture
def business_case(business_postgres):
    db, psycopg = business_postgres
    suffix = uuid4().hex
    owner = Actor("biz-owner-" + suffix, "biz-firm-" + suffix)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.users(id,name) VALUES(%s,'事务所管理员')", (owner.user_id,))
        conn.execute("INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'隔离业务测试',%s)", (owner.firm_id, owner.user_id))
        conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,'owner')", (owner.firm_id, owner.user_id))
    service = AccountingService(db)
    book = service.create_book(owner, BookCreate(name="业务范围测试企业", period="2026-10"))
    other_book = service.create_book(owner, BookCreate(name="其他业务范围测试企业", period="2026-10"))
    departments = ["biz-department-" + uuid4().hex for _ in range(2)]
    with psycopg.connect(ADMIN_URL) as conn:
        for dep in departments:
            conn.execute("INSERT INTO accounting.departments VALUES(%s,%s,'测试部门')", (dep, book["company_id"]))
    case = SimpleNamespace(db=db, psycopg=psycopg, owner=owner, service=service, book=book["id"], company=book["company_id"], other_book=other_book["id"], other_company=other_book["company_id"], department=departments[0], other_department=departments[1])

    def person(company_role=None, department=None, firm_role=None, book_role=None, company=None):
        actor = Actor("biz-user-" + uuid4().hex, owner.firm_id)
        with psycopg.connect(ADMIN_URL) as conn:
            conn.execute("INSERT INTO accounting.users(id,name) VALUES(%s,'业务测试人员')", (actor.user_id,))
            if company_role:
                conn.execute("INSERT INTO accounting.company_memberships(company_id,user_id,role,department_id) VALUES(%s,%s,%s,%s)", (company or case.company, actor.user_id, company_role, department))
            if firm_role:
                conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,%s)", (owner.firm_id, actor.user_id, firm_role))
            if book_role:
                conn.execute("INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,%s)", (case.book, owner.firm_id, actor.user_id, book_role))
        return actor

    case.person = person
    case.employee = person("employee", case.department)
    case.colleague = person("employee", case.department)
    case.approver = person("employee", case.department)
    case.finance = person("finance", case.other_department, book_role="editor")
    case.company_admin = person("admin", case.department)
    case.company_owner = person("owner", case.department)
    case.reviewer = person(firm_role="reviewer", book_role="reviewer")
    case.viewer = person(firm_role="viewer", book_role="viewer")
    case.unassigned = person(firm_role="reviewer")
    with db.transaction(write=True, actor=owner) as conn:
        conn.execute("INSERT INTO department_approvers VALUES(?,?,?,?)", (owner.firm_id, case.company, case.department, case.approver.user_id))
    return case


def insert_event(case, applicant=None, department=None):
    applicant = applicant or case.employee
    department = case.department if department is None else department
    event_id = "biz-event-" + uuid4().hex
    with case.db.transaction(write=True, actor=applicant) as conn:
        conn.execute("INSERT INTO business_events(id,firm_id,company_id,department_id,applicant_id,template_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,'reimbursement',?,?,?)", (event_id, applicant.firm_id, case.company, department, applicant.user_id, json.dumps({"amount": "12.34", "purpose": "办公费用"}, ensure_ascii=False), STAMP, STAMP))
    return event_id


def attach(case, event_id, applicant=None):
    applicant = applicant or case.employee
    attachment_id = uuid4().hex
    with case.db.transaction(write=True, actor=applicant) as conn:
        conn.execute("INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_at,created_by) VALUES(?,?,?,?,?,'票据.pdf','application/pdf',?,10,?,?)", (attachment_id, event_id, applicant.firm_id, case.company, "private/" + attachment_id, "a" * 64, STAMP, applicant.user_id))
    return attachment_id


def submit(case, event_id, applicant=None):
    applicant = applicant or case.employee
    with case.db.transaction(write=True, actor=applicant) as conn:
        payload = conn.execute("SELECT payload FROM business_events WHERE id=?", (event_id,)).fetchone()[0]
        conn.execute("INSERT INTO business_versions(event_id,firm_id,company_id,event_version,payload,template_snapshot,attachment_snapshot,created_at,created_by) VALUES(?,?,?,2,?,'{}','[]',?,?)", (event_id, applicant.firm_id, case.company, payload, STAMP, applicant.user_id))
        conn.execute("UPDATE business_events SET status='submitted',version=2,submitted_version=2 WHERE id=?", (event_id,))


def approve(case, event_id, stage="department", actor=None):
    actor = actor or (case.approver if stage == "department" else case.finance)
    with case.db.transaction(write=True, actor=actor) as conn:
        conn.execute("UPDATE business_events SET status=?,version=version+1 WHERE id=?", ("department_approved" if stage == "department" else "finance_approved", event_id))
        conn.execute("INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,decision,created_by,created_at) VALUES(?,?,?,?,2,?,'approve',?,?)", (uuid4().hex, event_id, actor.firm_id, case.company, stage, actor.user_id, STAMP))


def handoff(case, event_id):
    source_id, draft_id = uuid4().hex, uuid4().hex
    with case.db.transaction(write=True, actor=case.finance) as conn:
        conn.execute("INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) VALUES(?,?,?,'业务事项','2026-10-01','业务测试','供应商',1234,'expense','业务附件',?,'business')", (source_id, case.book, event_id, uuid4().hex))
        conn.execute("INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,'业务测试','仍需会计复核')", (draft_id, case.book, source_id))
        conn.execute("INSERT INTO business_mappings(event_id,firm_id,company_id,book_id,source_id,draft_id,submitted_version,idempotency_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,2,?,?,?,?)", (event_id, case.finance.firm_id, case.company, case.book, source_id, draft_id, "handoff-" + event_id, uuid4().hex, case.finance.user_id, STAMP))
        conn.execute("UPDATE business_events SET status='handed_off',version=version+1 WHERE id=?", (event_id,))
        conn.execute("INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,decision,created_by,created_at) VALUES(?,?,?,?,2,'handoff','handoff',?,?)", (uuid4().hex, event_id, case.finance.firm_id, case.company, case.finance.user_id, STAMP))
    return source_id, draft_id


def visible(case, actor, table, event_id):
    with case.db.transaction(actor=actor) as conn:
        return conn.execute(f"SELECT * FROM {table} WHERE {'id' if table == 'business_events' else 'event_id'}=?", (event_id,)).fetchall()


def full_event(case):
    event_id = insert_event(case)
    attach(case, event_id)
    submit(case, event_id)
    approve(case, event_id)
    approve(case, event_id, "finance")
    return event_id


def test_business_postgres_sql_scopes_are_person_department_and_finance(business_case):
    case = business_case
    event_id = full_event(case)
    for actor in (case.employee, case.approver, case.finance):
        for table in BUSINESS_TABLES[:-1]:
            assert visible(case, actor, table, event_id)
    for actor in (case.colleague, case.company_admin, case.company_owner, case.owner, case.reviewer, case.viewer, case.unassigned):
        for table in BUSINESS_TABLES:
            assert visible(case, actor, table, event_id) == []
    outsider = case.person("employee", case.other_department)
    assert visible(case, outsider, "business_events", event_id) == []


def test_business_postgres_ledger_read_only_opens_handed_off_originals(business_case):
    case = business_case
    event_id = full_event(case)
    assert visible(case, case.reviewer, "business_events", event_id) == []
    source_id, _ = handoff(case, event_id)
    for actor in (case.owner, case.reviewer, case.viewer, case.finance):
        for table in BUSINESS_TABLES:
            assert visible(case, actor, table, event_id)
    for actor in (case.company_owner, case.company_admin, case.colleague, case.unassigned):
        for table in BUSINESS_TABLES:
            assert visible(case, actor, table, event_id) == []
    assert visible(case, case.employee, "business_mappings", event_id) == []
    assert visible(case, case.approver, "business_mappings", event_id) == []
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT source_kind FROM sources WHERE id=?", (source_id,)).fetchone()[0] == "business"
        assert conn.execute("SELECT status FROM drafts WHERE source_id=?", (source_id,)).fetchone()[0] == "draft"


@pytest.mark.parametrize("table", BUSINESS_TABLES)
def test_business_postgres_wrong_firm_and_suspended_client_hide_every_layer(business_case, table):
    case = business_case
    event_id = full_event(case)
    handoff(case, event_id)
    for actor in (case.employee, case.finance, case.owner, case.reviewer):
        assert visible(case, Actor(actor.user_id, "other-firm"), table, event_id) == []
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s", (case.owner.firm_id, case.company))
    for actor in (case.employee, case.approver, case.finance, case.owner):
        assert visible(case, actor, table, event_id) == []


def test_business_postgres_department_grant_and_company_membership_revocation_are_live(business_case):
    case = business_case
    event_id = full_event(case)
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("DELETE FROM department_approvers WHERE firm_id=? AND department_id=? AND user_id=?", (case.owner.firm_id, case.department, case.approver.user_id))
    assert visible(case, case.approver, "business_events", event_id) == []
    assert visible(case, case.finance, "business_events", event_id)
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("INSERT INTO department_approvers VALUES(?,?,?,?)", (case.owner.firm_id, case.company, case.department, case.approver.user_id))
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (case.company, case.approver.user_id))
        assert conn.execute("SELECT * FROM department_approvers WHERE user_id=?", (case.approver.user_id,)).fetchall() == []
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (case.company, case.employee.user_id))
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (case.company, case.finance.user_id))
    for actor in (case.employee, case.approver, case.finance):
        assert visible(case, actor, "business_events", event_id) == []


def test_business_postgres_disabled_account_cannot_read_own_originals(business_case):
    case = business_case
    event_id = insert_event(case)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (case.employee.user_id,))
    assert visible(case, case.employee, "business_events", event_id) == []


@pytest.mark.parametrize("actor_name", ["employee", "approver", "finance", "reviewer"])
def test_business_postgres_only_organization_managers_configure_approvers(business_case, actor_name):
    case = business_case
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=getattr(case, actor_name)) as conn:
            conn.execute("INSERT INTO department_approvers VALUES(?,?,?,?)", (case.owner.firm_id, case.company, case.other_department, case.colleague.user_id))
    with case.db.transaction(write=True, actor=case.company_admin) as conn:
        conn.execute("INSERT INTO department_approvers VALUES(?,?,?,?)", (case.owner.firm_id, case.company, case.other_department, case.colleague.user_id))


def test_business_postgres_applicant_cannot_forge_department_or_other_applicant(business_case):
    case = business_case
    for department, applicant in ((case.other_department, case.employee.user_id), (case.department, case.colleague.user_id)):
        with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
            with case.db.transaction(write=True, actor=case.employee) as conn:
                conn.execute("INSERT INTO business_events(id,firm_id,company_id,department_id,applicant_id,template_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,'reimbursement','{}',?,?)", (uuid4().hex, case.owner.firm_id, case.company, department, applicant, STAMP, STAMP))


@pytest.mark.parametrize("stage", ["department", "finance"])
def test_business_postgres_direct_sql_self_approval_is_rejected(business_case, stage):
    case = business_case
    actor = case.approver if stage == "department" else case.finance
    dep = case.department if stage == "department" else case.other_department
    event_id = insert_event(case, actor, dep)
    submit(case, event_id, actor)
    with case.db.transaction(write=True, actor=actor) as conn:
        assert conn.execute("UPDATE business_events SET status='finance_approved' WHERE id=?", (event_id,)).rowcount == 0
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=actor) as conn:
            conn.execute("INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,decision,created_by,created_at) VALUES(?,?,?,?,2,?,'approve',?,?)", (uuid4().hex, event_id, actor.firm_id, case.company, stage, actor.user_id, STAMP))


@pytest.mark.parametrize("table", ["business_versions", "business_attachments", "business_actions", "business_mappings"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_business_postgres_runtime_cannot_rewrite_originals_or_approvals(business_case, table, verb):
    case = business_case
    event_id = full_event(case)
    handoff(case, event_id)
    statement = f"UPDATE {table} SET company_id=company_id WHERE event_id=?" if verb == "UPDATE" else f"DELETE FROM {table} WHERE event_id=?"
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.finance) as conn:
            conn.execute(statement, (event_id,))


def test_business_postgres_event_identity_cannot_be_retargeted(business_case):
    case = business_case
    event_id = insert_event(case)
    for statement, value in (("applicant_id", case.colleague.user_id), ("department_id", case.other_department), ("template_id", "daily_cashflow")):
        with pytest.raises(case.psycopg.errors.CheckViolation):
            with case.db.transaction(write=True, actor=case.employee) as conn:
                conn.execute(f"UPDATE business_events SET {statement}=? WHERE id=?", (value, event_id))


def test_business_postgres_cross_company_mapping_fails_composite_foreign_key(business_case):
    case = business_case
    event_id = full_event(case)
    with case.psycopg.connect(ADMIN_URL) as conn:
        source, draft = uuid4().hex, uuid4().hex
        conn.execute("INSERT INTO accounting.sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(%s,%s,%s,'其他企业','2026-10-01','测试','测试',1234,'expense','发票','fingerprint')", (source, case.other_book, uuid4().hex))
        conn.execute("INSERT INTO accounting.drafts(id,book_id,source_id,description,explanation) VALUES(%s,%s,%s,'其他企业','测试')", (draft, case.other_book, source))
    with pytest.raises(case.psycopg.errors.ForeignKeyViolation):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("INSERT INTO accounting.business_mappings(event_id,firm_id,company_id,book_id,source_id,draft_id,submitted_version,idempotency_key,request_fingerprint,created_by,created_at) VALUES(%s,%s,%s,%s,%s,%s,2,%s,'fp',%s,%s)", (event_id, case.owner.firm_id, case.company, case.other_book, source, draft, uuid4().hex, case.finance.user_id, STAMP))


def test_business_postgres_submission_readiness_is_private_and_live(business_case):
    case = business_case
    def ready(actor, applicant=None, department=None):
        with case.db.transaction(actor=actor) as conn:
            return conn.execute("SELECT accounting.business_submission_ready(?,?,?,?)", (actor.firm_id, case.company, department or case.department, applicant or case.employee.user_id)).fetchone()[0]
    assert ready(case.employee) is True
    assert ready(case.employee, applicant=case.colleague.user_id) is False
    assert ready(case.employee, department=case.other_department) is False
    assert ready(case.owner) is False
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT * FROM department_approvers WHERE company_id=?", (case.company,)).fetchall() == []
        assert len(conn.execute("SELECT * FROM company_memberships WHERE company_id=?", (case.company,)).fetchall()) == 1
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (case.approver.user_id,))
    assert ready(case.employee) is False


def test_business_postgres_all_new_tables_force_rls_and_functions_are_private(business_case):
    case = business_case
    with case.db.transaction(actor=case.owner) as conn:
        rows = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='accounting' AND (relname LIKE 'business_%' OR relname='department_approvers') AND relkind='r'").fetchall()
        assert len(rows) == 6 and all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)
        functions = conn.execute("SELECT p.oid,p.proname,p.prosecdef,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='accounting' AND p.proname LIKE 'business_%'").fetchall()
        assert functions
        for function in functions:
            assert any(value.startswith("search_path=pg_catalog, accounting, pg_temp") for value in function["proconfig"])
            assert not conn.execute("SELECT has_function_privilege('public',?,'EXECUTE')", (function["oid"],)).fetchone()[0]
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(system=True) as conn:
            conn.execute("SELECT * FROM business_events")


def create_service_event(case, template="reimbursement", **changes):
    from app.business.models import BusinessEventCreate
    from app.business.service import BusinessService

    service = BusinessService(case.db)
    payload = {"occurred_on": "2026-10-03", "description": "垫付办公耗材", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office", **changes}
    event = service.create(case.employee, BusinessEventCreate(company_id=case.company, template_id=template, payload=payload))
    attachment = uuid4().hex
    event = service.add_attachment(case.employee, event["id"], event["version"], {"id": attachment, "storage_key": attachment, "filename": "凭证.pdf", "media_type": "application/pdf", "sha256": "c" * 64, "size_bytes": 100})
    event = service.submit(case.employee, event["id"], event["version"])
    event = service.approve_department(case.approver, event["id"], event["version"])
    return service, service.approve_finance(case.finance, event["id"], event["version"])


@pytest.mark.parametrize("template,changes,credit", [("reimbursement", {}, "2241"), ("procurement_expense", {}, "2202"), ("procurement_expense", {"payment_status": "paid"}, "1002"), ("daily_cashflow", {"payment_status": "paid"}, "1002"), ("daily_cashflow", {"payment_status": "paid", "direction": "income"}, "6001")])
def test_business_postgres_real_service_templates_bridge_review_and_export(business_case, template, changes, credit):
    from app.accounting.models import ApprovalRequest, ExportRequest
    from app.business.models import HandoffRequest

    case = business_case
    service, event = create_service_event(case, template, **changes)
    request = HandoffRequest(expected_version=event["version"], book_id=case.book, idempotency_key="bridge-" + event["id"])
    result = service.handoff(case.finance, event["id"], request)
    assert service.handoff(case.finance, event["id"], request) == result
    assert result["event"]["status"] == "handed_off"
    assert "storage_key" not in str(result["event"])
    assert result["event"]["submitted_version"] == result["event"]["versions"][0]["event_version"]
    book = case.service.get_book(case.reviewer, case.book)
    assert book["sources"][0]["source_kind"] == "business"
    draft = book["drafts"][0]
    assert draft["status"] == "draft"
    assert any(line["account_code"] == credit and line["credit"] == "0.30" for line in draft["lines"])
    assert sum(int(line["debit"].replace(".", "")) for line in draft["lines"]) == 30
    case.service.approve(case.reviewer, case.book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    exported = case.service.export(case.reviewer, case.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key="export-" + event["id"]))
    assert exported["count"] == 1
    assert case.service.get_export(case.reviewer, case.book, exported["id"])["csv"] == exported["csv"]


def test_business_postgres_real_service_concurrent_handoff_is_single_creation(business_case):
    from app.business.models import HandoffRequest

    case = business_case
    service, event = create_service_event(case)
    request = HandoffRequest(expected_version=event["version"], book_id=case.book, idempotency_key="concurrent-" + event["id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.handoff(case.finance, event["id"], request), range(4)))
    assert all(result == results[0] for result in results)
    with case.db.transaction(actor=case.finance) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE book_id=?", (case.book,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM drafts WHERE book_id=?", (case.book,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM business_mappings WHERE event_id=?", (event["id"],)).fetchone()[0] == 1


def test_business_postgres_real_service_return_preserves_submitted_version(business_case):
    from app.business.models import BusinessEventUpdate, ReturnRequest
    from app.business.service import BusinessService

    case = business_case
    service = BusinessService(case.db)
    from app.business.models import BusinessEventCreate
    payload = {"occurred_on": "2026-10-03", "description": "垫付办公耗材", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office"}
    event = service.create(case.employee, BusinessEventCreate(company_id=case.company, template_id="reimbursement", payload=payload))
    attachment = uuid4().hex
    event = service.add_attachment(case.employee, event["id"], event["version"], {"id": attachment, "storage_key": attachment, "filename": "凭证.pdf", "media_type": "application/pdf", "sha256": "d" * 64, "size_bytes": 100})
    first = service.submit(case.employee, event["id"], event["version"])
    returned = service.return_event(case.approver, event["id"], ReturnRequest(expected_version=first["version"], reason="补充金额依据"))
    changed = service.update(case.employee, event["id"], BusinessEventUpdate(expected_version=returned["version"], payload={**payload, "amount": "1.20"}))
    second = service.submit(case.employee, event["id"], changed["version"])
    assert [version["payload"]["amount"] for version in second["versions"]] == ["0.30", "1.20"]
    assert second["actions"][0]["submitted_version"] == first["submitted_version"]
    assert second["actions"][0]["reason"] == "补充金额依据"


def test_business_postgres_real_service_create_retry_deduplicates_and_detects_changed_content(business_case):
    from app.accounting.domain import AccountingError
    from app.business.models import BusinessEventCreate
    from app.business.service import BusinessService

    case = business_case
    service = BusinessService(case.db)
    payload = {"occurred_on": "2026-10-03", "description": "垫付办公耗材", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office"}
    request = BusinessEventCreate(company_id=case.company, template_id="reimbursement", payload=payload, idempotency_key="create-" + uuid4().hex)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.create(case.employee, request), range(4)))
    assert all(event == results[0] for event in results)
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_events WHERE applicant_id=? AND create_key=?", (case.employee.user_id, request.idempotency_key)).fetchone()[0] == 1
    changed = BusinessEventCreate(company_id=case.company, template_id="reimbursement", payload={**payload, "amount": "1.20"}, idempotency_key=request.idempotency_key)
    with pytest.raises(AccountingError) as error:
        service.create(case.employee, changed)
    assert error.value.status == 409
    # Another applicant uses its own form key namespace, without opening the
    # first applicant's response or document to that applicant.
    other = service.create(case.colleague, request)
    assert other["id"] != results[0]["id"]
    assert visible(case, case.colleague, "business_events", results[0]["id"]) == []
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.employee) as conn:
            conn.execute("UPDATE business_events SET create_key=? WHERE id=?", ("changed-" + uuid4().hex, results[0]["id"]))


def test_business_postgres_applicant_label_only_follows_existing_event_scope(business_case):
    case = business_case
    event_id = insert_event(case)
    for actor in (case.employee, case.approver, case.finance):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT accounting.business_applicant_name(?,?,?)", (event_id, case.owner.firm_id, case.company)).fetchone()[0] == "业务测试人员"
    for actor in (case.colleague, case.company_owner, case.owner, case.reviewer, Actor(case.employee.user_id, "another-firm")):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT accounting.business_applicant_name(?,?,?)", (event_id, case.owner.firm_id, case.company)).fetchone()[0] is None
