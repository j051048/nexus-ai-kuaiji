"""受限运行账号和真实 RLS 下的异常、补件及原件追加能力。"""
from uuid import uuid4
import pytest
from app.business.service import BusinessService
from app.cases.models import CaseCreate, CaseCommand, CaseComment, CaseRespond, CaseReviewResponse, CaseAssign
from app.cases.service import CaseService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres, full_event, handoff

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PG 管理员及受限运行测试连接")


def prepare(c, kind="supplement"):
    event_id = full_event(c)
    handoff(c, event_id)
    cases = CaseService(c.db, BusinessService(c.db))
    row = cases.create(c.reviewer, CaseCreate(event_id=event_id, kind=kind, title="核对合同原件", reason="请补充合同编号与清晰原件", required_fields=["合同编号"] if kind == "supplement" else [], idempotency_key=uuid4().hex))
    return cases, event_id, row


def command(row, model=CaseCommand, **changes):
    return model(expected_version=row["version"], idempotency_key=uuid4().hex, reason="已核对补件事实", **changes)


def test_pg_cases_force_rls_internal_notes_and_current_scope(business_case):
    c = business_case
    cases, event, internal = prepare(c, "exception")
    for actor in (c.employee, c.colleague, c.approver, c.viewer, c.unassigned, c.company_admin):
        with c.db.transaction(actor=actor) as conn:
            assert not conn.execute("SELECT * FROM case_tasks WHERE id=?", (internal["id"],)).fetchall()
            assert not conn.execute("SELECT * FROM case_history WHERE case_id=?", (internal["id"],)).fetchall()
    public = cases.create(c.reviewer, CaseCreate(event_id=event, kind="supplement", title="补充用途", reason="请说明用途", idempotency_key=uuid4().hex))
    public = cases.command(c.reviewer, public["id"], "comment", command(public, CaseComment, audience="internal"))
    with c.db.transaction(actor=c.employee) as conn:
        assert len(conn.execute("SELECT * FROM case_tasks WHERE id=?", (public["id"],)).fetchall()) == 1
        assert len(conn.execute("SELECT * FROM case_history WHERE case_id=?", (public["id"],)).fetchall()) == 1
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ("case_tasks", "case_responses", "case_attachments", "case_history", "case_commands", "case_supersessions"):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("accounting." + table,)).fetchone() == (True, True)


def test_pg_accept_capability_adds_canonical_originals_fact_and_supersession_atomically(business_case):
    c = business_case
    cases, event, row = prepare(c)
    with c.db.transaction(actor=c.reviewer) as conn:
        previous = conn.execute("SELECT fact_version FROM business_events WHERE id=?", (event,)).fetchone()[0]
        old_id = conn.execute("SELECT id FROM business_attachments WHERE event_id=?", (event,)).fetchone()[0]
    stored = {"id": uuid4().hex, "storage_key": "dd/" + uuid4().hex, "filename": "清晰票据.pdf", "media_type": "application/pdf", "sha256": "d" * 64, "size_bytes": 100}
    row = cases.add_attachment(c.employee, row["id"], row["version"], uuid4().hex, stored)["case"]
    row = cases.command(c.employee, row["id"], "respond", command(row, CaseRespond, fields={"合同编号": "PG-HT-01"}))
    row = cases.command(c.reviewer, row["id"], "review-response", command(row, CaseReviewResponse, decision="accept", supersedes_attachment_ids=[old_id]))
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute("SELECT fact_version FROM business_events WHERE id=?", (event,)).fetchone()[0] == previous + 1
        assert conn.execute("SELECT event_id FROM business_attachments WHERE id=?", (stored["id"],)).fetchone()[0] == event
        assert cases.superseded_attachment_ids_in_transaction(conn, c.reviewer, event) == {old_id}
    assert row["state"] == "in_review"


def test_pg_applicant_cannot_accept_state_comment_or_canonical_attachment(business_case):
    c = business_case
    cases, event, row = prepare(c)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.employee) as conn:
            conn.execute("UPDATE case_tasks SET state='closed',version=version+1 WHERE id=?", (row["id"],))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.employee) as conn:
            conn.execute("SELECT accounting.case_accept_evidence(?,?)", (row["id"], row["version"]))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.employee) as conn:
            conn.execute("INSERT INTO business_attachments(id,event_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,created_by,created_at) VALUES(?,?,?,?,?,'forged.pdf','application/pdf',?,100,?,'2026-10-12')", (uuid4().hex, event, c.owner.firm_id, c.company, "ee/" + uuid4().hex, "e" * 64, c.employee.user_id))


def test_pg_assignees_scoped_capability_does_not_grant_or_expose_directory(business_case):
    c = business_case
    cases, _, row = prepare(c)
    options = {item["user_id"] for item in cases.assignees(c.reviewer, row["id"])["assignees"]}
    assert c.reviewer.user_id in options and c.finance.user_id in options
    assert c.employee.user_id not in options and c.colleague.user_id not in options
    assigned = cases.command(c.reviewer, row["id"], "assign", command(row, CaseAssign, assignee_id=c.finance.user_id))
    assert assigned["assignee_id"] == c.finance.user_id
    with c.db.transaction(actor=c.employee) as conn:
        assert not conn.execute("SELECT * FROM accounting.case_assignees(?)", (row["id"],)).fetchall()


def test_pg_pre_handoff_request_gains_only_current_mapped_review_scope(business_case):
    c = business_case
    event = full_event(c)
    cases = CaseService(c.db, BusinessService(c.db))
    row = cases.create(c.finance, CaseCreate(event_id=event, kind="supplement", title="移交前的补件", reason="补充合同用途", idempotency_key=uuid4().hex))
    assert cases.list(c.reviewer)["total"] == 0
    source_id, draft_id = handoff(c, event)
    assert cases.list(c.reviewer, book_id=c.book)["total"] == 1
    current = cases.get(c.reviewer, row["id"])
    assert current["book_id"] == c.book and current["draft_id"] == draft_id
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.periods SET status='closed' WHERE book_id=%s", (c.book,))
    with c.db.transaction(actor=c.employee) as conn:
        assert not conn.execute("SELECT accounting.case_source_open(?)", (row["id"],)).fetchone()[0]
    with pytest.raises(Exception):
        cases.require_upload(c.employee, row["id"], row["version"])


def test_pg_assigned_finance_editor_collects_only_public_material_for_accountant_acceptance(business_case):
    from app.accounting.models import ImportRequest
    c = business_case
    c.service.import_sources(c.owner, c.book, ImportRequest(source_name="合成银行流水", rows=[{"external_id": "bank-case-" + uuid4().hex, "date": "2026-10-04", "description": "办公室房租", "counterparty": "出租方", "amount": "10.00", "direction": "expense", "evidence_ref": ""}]))
    c.service.suggestions(c.owner, c.book)
    draft = c.service.get_book(c.reviewer, c.book)["drafts"][0]
    cases = CaseService(c.db, BusinessService(c.db))
    task = cases.create(c.reviewer, CaseCreate(book_id=c.book, draft_id=draft["id"], title="内部银行调查", reason="内部调查依据不可见", idempotency_key=uuid4().hex))
    assert cases.list(c.finance)["total"] == 0
    task = cases.command(c.reviewer, task["id"], "assign", CaseAssign(expected_version=task["version"], idempotency_key=uuid4().hex, reason="请补充租赁合同", assignee_id=c.finance.user_id))
    own = cases.get(c.finance, task["id"])
    assert own["title"] == "银行流水资料补充" and "内部调查依据不可见" not in str(own)
    with c.db.transaction(actor=c.finance) as conn:
        assert not conn.execute("SELECT * FROM case_tasks WHERE id=?", (task["id"],)).fetchall()
        safe = conn.execute("SELECT * FROM accounting.case_collector_tasks() WHERE id=?", (task["id"],)).fetchone()
        assert safe["request_reason"] == "请补充租赁合同" and safe["title"] == "银行流水资料补充"
        assert all(row["audience"] == "requester" for row in conn.execute("SELECT * FROM case_history WHERE case_id=?", (task["id"],)))
    stored = {"id": uuid4().hex, "storage_key": "ef/" + uuid4().hex, "filename": "租赁合同.pdf", "media_type": "application/pdf", "sha256": "e" * 64, "size_bytes": 100}
    own = cases.add_attachment(c.finance, task["id"], own["version"], uuid4().hex, stored)["case"]
    own = cases.command(c.finance, task["id"], "respond", command(own, CaseRespond))
    with pytest.raises(Exception):
        cases.command(c.finance, task["id"], "review-response", command(own, CaseReviewResponse, decision="accept"))
    accepted = cases.command(c.reviewer, task["id"], "review-response", command(own, CaseReviewResponse, decision="accept"))
    assert accepted["state"] == "in_review"


def test_pg_finance_viewer_material_case_does_not_grant_journal_review(business_case):
    from app.accounting.models import ApprovalRequest, DraftUpdate
    from app.accounting.domain import AccountingError
    c = business_case
    event = full_event(c)
    _, draft_id = handoff(c, event)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.book_access SET role='viewer' WHERE book_id=%s AND user_id=%s", (c.book, c.finance.user_id))
    cases = CaseService(c.db, BusinessService(c.db))
    row = cases.create(c.finance, CaseCreate(event_id=event, kind="supplement", title="财务资料核对", reason="仅处理公司事项补件", idempotency_key=uuid4().hex))
    row = cases.command(c.employee, row["id"], "respond", command(row, CaseRespond))
    row = cases.command(c.finance, row["id"], "review-response", command(row, CaseReviewResponse, decision="accept"))
    assert row["state"] == "in_review"
    draft = c.service.get_draft(c.finance, c.book, draft_id)
    with pytest.raises(AccountingError):
        c.service.update_draft(c.finance, c.book, draft_id, DraftUpdate(expected_version=draft["version"], description=draft["description"], lines=[{"account_code": "6602.02", "debit": "12.34", "credit": "0.00"}, {"account_code": "1002", "debit": "0.00", "credit": "12.34"}]))
    with pytest.raises(AccountingError):
        c.service.approve(c.finance, c.book, draft_id, ApprovalRequest(expected_version=draft["version"]))
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute("SELECT book_id FROM audit_events WHERE action='case.create' AND object_id=?", (row["id"],)).fetchone()[0] is None
