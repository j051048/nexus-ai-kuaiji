"""AI SQL boundary tests on a real, explicitly configured PostgreSQL database."""
import json
from uuid import uuid4

import pytest

from app.accounting.service import Actor
from app.business.models import BusinessEventCreate, HandoffRequest
from app.business.service import BusinessService
from test_business_postgres import (  # Reuse the actual multi-company fixture.
    ADMIN_URL, APP_URL, STAMP, attach, business_case,
    business_postgres, full_event, handoff, insert_event,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


def ocr_run(case, event_id, attachment_id, actor=None, **changes):
    actor = actor or case.employee
    run_id = "ai-run-" + uuid4().hex
    params = {
        "id": run_id, "firm_id": actor.firm_id, "company_id": case.company,
        "event_id": event_id, "kind": "ocr", "attachment_id": attachment_id,
        "input_version": 1, "input_fingerprint": "facts-" + uuid4().hex,
        "idempotency_key": uuid4().hex, "status": "running", "provider": "compatible",
        "model": "configured-vision", "prompt_version": "ocr-v1",
        "created_by": actor.user_id, "created_at": STAMP, **changes,
    }
    columns = ",".join(params)
    with case.db.transaction(write=True, actor=actor) as conn:
        conn.execute(f"INSERT INTO ai_runs({columns}) VALUES({','.join('?' for _ in params)})", tuple(params.values()))
    return run_id


def finish(case, run_id, actor=None):
    with case.db.transaction(write=True, actor=actor or case.employee) as conn:
        return conn.execute("UPDATE ai_runs SET status='succeeded',result_json=?,usage_json=?,finished_at=? WHERE id=?",
                            (json.dumps({"total_amount": "12.34", "seller": "供应商"}), '{"input_tokens":100}', STAMP, run_id)).rowcount


def finding(case, event_id, run_id=None, actor=None, **changes):
    actor = actor or case.employee
    finding_id = "ai-finding-" + uuid4().hex
    params = {"id": finding_id, "run_id": run_id, "firm_id": actor.firm_id, "company_id": case.company,
              "event_id": event_id, "code": "amount_mismatch", "severity": "blocking",
              "payload_json": '{"expected":"12.34","observed":"13.00"}', "fingerprint": uuid4().hex,
              "created_at": STAMP, **changes}
    with case.db.transaction(write=True, actor=actor) as conn:
        conn.execute(f"INSERT INTO ai_findings({','.join(params)}) VALUES({','.join('?' for _ in params)})", tuple(params.values()))
    return finding_id


def review(case, event_id, finding_id, actor, **changes):
    params = {"id": uuid4().hex, "finding_id": finding_id, "firm_id": actor.firm_id,
              "company_id": case.company, "event_id": event_id, "reason": "已核对原件并保留处理依据",
              "created_by": actor.user_id, "created_at": STAMP, **changes}
    with case.db.transaction(write=True, actor=actor) as conn:
        conn.execute(f"INSERT INTO ai_finding_reviews({','.join(params)}) VALUES({','.join('?' for _ in params)})", tuple(params.values()))
    return params["id"]


def count_visible(case, actor, table, event_id):
    with case.db.transaction(actor=actor) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE event_id=?", (event_id,)).fetchone()[0]


def paid_mapped(case, payment_status="paid"):
    # Payment facts belong to the original draft input. Never amend an already
    # approved payload as fixture setup: stage 10 requires an independent
    # revision for that change, including administrator SQL writes.
    business = BusinessService(case.db)
    event = business.create(case.employee, BusinessEventCreate(
        company_id=case.company, department_id=case.department,
        template_id="daily_cashflow" if payment_status == "paid" else "procurement_expense",
        payload={"amount": "12.34", "occurred_on": "2026-10-01", "description": "业务测试", "counterparty": "供应商",
                 "direction": "expense", "payment_status": payment_status, "category": "office", "purpose": "办公费用"},
    ))
    # Metadata-only original intentionally reused by these SQL permission tests;
    # the fixture does not pretend to OCR a real file or call a supplier.
    attach(case, event["id"])
    event = business.submit(case.employee, event["id"], event["version"])
    event = business.approve_department(case.approver, event["id"], event["version"])
    event = business.approve_finance(case.finance, event["id"], event["version"])
    receipt = business.handoff(case.finance, event["id"], HandoffRequest(
        expected_version=event["version"], book_id=case.book, idempotency_key="sql-boundary-handoff-" + uuid4().hex))
    return event["id"], receipt["source_id"], receipt["draft_id"]


def bank_source(case, book_id=None):
    source_id, draft_id = uuid4().hex, uuid4().hex
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint) VALUES(%s,%s,%s,'银行流水','2026-10-01','支付采购','供应商',1234,'expense','bank.pdf',%s)", (source_id, book_id or case.book, uuid4().hex, uuid4().hex))
        conn.execute("INSERT INTO accounting.drafts(id,book_id,source_id,description,explanation) VALUES(%s,%s,%s,'支付采购','人工复核')", (draft_id, book_id or case.book, source_id))
    return source_id, draft_id


def bank_link(case, event_id, source_id, actor=None):
    actor = actor or case.finance
    with case.db.transaction(write=True, actor=actor) as conn:
        conn.execute("INSERT INTO ai_bank_links(bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                     (source_id, actor.firm_id, case.company, event_id, case.book, actor.user_id, STAMP))


def test_ai_settings_management_does_not_open_employee_originals(business_case):
    case = business_case
    event_id = insert_event(case)
    attachment_id = attach(case, event_id)
    run_id = ocr_run(case, event_id, attachment_id)
    finish(case, run_id)
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("INSERT INTO ai_company_settings VALUES(?,?,1,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (case.owner.firm_id, case.company, case.owner.user_id, STAMP))
    for actor in (case.employee, case.approver, case.finance, case.owner, case.company_owner, case.viewer):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT enabled FROM ai_company_settings WHERE company_id=?", (case.company,)).fetchone()[0] == 1
    for actor in (case.owner, case.company_owner, case.company_admin, case.viewer, case.reviewer, case.colleague, case.unassigned):
        assert count_visible(case, actor, "ai_runs", event_id) == 0
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("UPDATE ai_company_settings SET enabled=0,updated_by=? WHERE company_id=?", (case.employee.user_id, case.company)).rowcount == 0


@pytest.mark.parametrize("table", ["ai_runs", "ai_findings", "ai_finding_reviews"])
def test_ai_records_follow_current_person_department_company_scope(business_case, table):
    case = business_case
    event_id = insert_event(case)
    attachment_id = attach(case, event_id)
    run_id = ocr_run(case, event_id, attachment_id)
    finish(case, run_id)
    finding_id = finding(case, event_id, run_id)
    review(case, event_id, finding_id, case.finance)
    for actor in (case.employee, case.approver, case.finance):
        assert count_visible(case, actor, table, event_id) == 1
    for actor in (case.colleague, case.owner, case.company_owner, case.reviewer, case.viewer,
                  Actor(case.employee.user_id, "other-firm")):
        assert count_visible(case, actor, table, event_id) == 0
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s", (case.owner.firm_id, case.company))
    for actor in (case.employee, case.approver, case.finance):
        assert count_visible(case, actor, table, event_id) == 0


def test_ai_ledger_viewer_can_read_handed_off_evidence_but_cannot_start_cost(business_case):
    case = business_case
    event_id = full_event(case)
    with case.db.transaction(actor=case.employee) as conn:
        attachment_id = conn.execute("SELECT id FROM business_attachments WHERE event_id=?", (event_id,)).fetchone()[0]
    run_id = ocr_run(case, event_id, attachment_id)
    finish(case, run_id)
    handoff(case, event_id)
    assert count_visible(case, case.viewer, "ai_runs", event_id) == 1
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        ocr_run(case, event_id, attachment_id, case.viewer)
    reviewer_run = ocr_run(case, event_id, attachment_id, case.reviewer)
    assert finish(case, reviewer_run, case.reviewer) == 1


def test_ai_actor_cannot_forge_run_owner_or_attachment_scope(business_case):
    case = business_case
    event_id, other_id = insert_event(case), insert_event(case, case.colleague)
    attachment_id, other_attachment = attach(case, event_id), attach(case, other_id, case.colleague)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        ocr_run(case, event_id, attachment_id, created_by=case.finance.user_id)
    with pytest.raises(case.psycopg.errors.ForeignKeyViolation):
        ocr_run(case, event_id, other_attachment)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        ocr_run(case, event_id, attachment_id, company_id=case.other_company)


def test_ai_runs_claim_is_unique_and_input_and_terminal_output_are_immutable(business_case):
    case = business_case
    event_id = insert_event(case)
    attachment_id = attach(case, event_id)
    key = "retry-" + uuid4().hex
    run_id = ocr_run(case, event_id, attachment_id, idempotency_key=key)
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        ocr_run(case, event_id, attachment_id, idempotency_key=key)
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.employee) as conn:
            conn.execute("UPDATE ai_runs SET status='failed',finished_at=?,input_fingerprint='changed' WHERE id=?", (STAMP, run_id))
    assert finish(case, run_id) == 1
    # RLS makes a terminal row non-updatable by runtime; administrator writes
    # are rejected by the immutable trigger too.
    assert finish(case, run_id) == 0
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.ai_runs SET result_json='{}' WHERE id=%s", (run_id,))


def test_ai_other_actor_cannot_finish_a_run(business_case):
    case = business_case
    event_id = insert_event(case)
    attachment_id = attach(case, event_id)
    run_id = ocr_run(case, event_id, attachment_id)
    assert finish(case, run_id, case.finance) == 0
    assert finish(case, run_id) == 1


def test_ai_revoked_requester_can_abandon_without_reading_or_publishing_output(business_case):
    case = business_case
    event_id = insert_event(case)
    attachment_id = attach(case, event_id)
    run_id = ocr_run(case, event_id, attachment_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s", (case.company, case.employee.user_id))
    assert count_visible(case, case.employee, "ai_runs", event_id) == 0
    assert finish(case, run_id) == 0
    with case.db.transaction(write=True, actor=case.finance) as conn:
        conn.execute("SELECT accounting.abandon_ai_run(?)", (run_id,))
    with case.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT status FROM accounting.ai_runs WHERE id=%s", (run_id,)).fetchone()[0] == "running"
    with case.db.transaction(write=True, actor=case.employee) as conn:
        conn.execute("SELECT accounting.abandon_ai_run(?)", (run_id,))
    with case.psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute("SELECT status,result_json,error_code,finished_at FROM accounting.ai_runs WHERE id=%s", (run_id,)).fetchone()
        assert row[0:3] == ("stale", None, "authorization_changed")
        assert row[3]
    assert count_visible(case, case.employee, "ai_runs", event_id) == 0


@pytest.mark.parametrize("actor_name", ["employee", "approver", "company_owner", "viewer"])
def test_ai_findings_only_independent_finance_or_reviewer_can_acknowledge(business_case, actor_name):
    case = business_case
    event_id = full_event(case)
    finding_id = finding(case, event_id)
    handoff(case, event_id)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        review(case, event_id, finding_id, getattr(case, actor_name))
    review(case, event_id, finding_id, case.finance)
    review(case, event_id, finding_id, case.reviewer)
    assert count_visible(case, case.employee, "ai_finding_reviews", event_id) == 2


def test_ai_finance_cannot_acknowledge_its_own_business_event(business_case):
    case = business_case
    event_id = insert_event(case, case.finance, case.other_department)
    finding_id = finding(case, event_id, actor=case.finance)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        review(case, event_id, finding_id, case.finance)


def test_ai_findings_and_reviews_are_immutable_and_cannot_cross_events(business_case):
    case = business_case
    event_id, other_event = insert_event(case), insert_event(case)
    finding_id = finding(case, event_id)
    review_id = review(case, event_id, finding_id, case.finance)
    with pytest.raises(case.psycopg.errors.ForeignKeyViolation):
        review(case, other_event, finding_id, case.finance)
    with pytest.raises(case.psycopg.errors.CheckViolation):
        review(case, event_id, finding_id, case.finance, reason="  ")
    for table, record_id in (("ai_findings", finding_id), ("ai_finding_reviews", review_id)):
        with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
            with case.db.transaction(write=True, actor=case.finance) as conn:
                conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
        with pytest.raises(case.psycopg.errors.CheckViolation):
            with case.psycopg.connect(ADMIN_URL) as conn:
                conn.execute(f"DELETE FROM accounting.{table} WHERE id=%s", (record_id,))


def test_ai_finding_fingerprint_does_not_accept_a_review_for_new_facts(business_case):
    case = business_case
    event_id = insert_event(case)
    old = finding(case, event_id, fingerprint="old-facts")
    review(case, event_id, old, case.finance)
    new = finding(case, event_id, fingerprint="new-facts")
    with case.db.transaction(actor=case.finance) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_finding_reviews WHERE finding_id=?", (new,)).fetchone()[0] == 0
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        finding(case, event_id, fingerprint="old-facts")


def test_ai_bank_links_hide_ledger_metadata_from_staff_and_department(business_case):
    case = business_case
    event_id, _, _ = paid_mapped(case)
    bank_id, _ = bank_source(case)
    bank_link(case, event_id, bank_id)
    for actor in (case.finance, case.reviewer, case.viewer, case.owner):
        assert count_visible(case, actor, "ai_bank_links", event_id) == 1
    for actor in (case.employee, case.approver, case.colleague, Actor(case.reviewer.user_id, "other-firm")):
        assert count_visible(case, actor, "ai_bank_links", event_id) == 0
    with case.db.transaction(write=True, actor=case.viewer) as conn:
        assert conn.execute("DELETE FROM ai_bank_links WHERE bank_source_id=?", (bank_id,)).rowcount == 0
    with case.db.transaction(write=True, actor=case.reviewer) as conn:
        assert conn.execute("DELETE FROM ai_bank_links WHERE bank_source_id=?", (bank_id,)).rowcount == 1


@pytest.mark.parametrize("invalid", ["other_company", "business_source", "approved_bank", "approved_business", "unpaid"])
def test_ai_bank_link_wrong_scope_and_finalized_or_unpaid_documents_are_blocked(business_case, invalid):
    case = business_case
    event_id, business_source_id, business_draft_id = paid_mapped(case, "unpaid" if invalid == "unpaid" else "paid")
    bank_id, bank_draft_id = bank_source(case, case.other_book if invalid == "other_company" else None)
    if invalid == "business_source":
        bank_id = business_source_id
    with case.psycopg.connect(ADMIN_URL) as conn:
        if invalid == "approved_bank":
            conn.execute("UPDATE accounting.drafts SET status='approved' WHERE id=%s", (bank_draft_id,))
        elif invalid == "approved_business":
            conn.execute("UPDATE accounting.drafts SET status='approved' WHERE id=%s", (business_draft_id,))
    with pytest.raises((case.psycopg.errors.CheckViolation, case.psycopg.errors.ForeignKeyViolation)):
        bank_link(case, event_id, bank_id)


def test_ai_bank_source_is_linked_at_most_once_and_link_freezes_after_review(business_case):
    case = business_case
    event_id, _, business_draft_id = paid_mapped(case)
    other_event, _, _ = paid_mapped(case)
    bank_id, _ = bank_source(case)
    bank_link(case, event_id, bank_id)
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        bank_link(case, other_event, bank_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.drafts SET status='approved' WHERE id=%s", (business_draft_id,))
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.finance) as conn:
            conn.execute("DELETE FROM ai_bank_links WHERE bank_source_id=?", (bank_id,))


def test_ai_proposal_application_binds_run_event_book_and_draft_and_keeps_draft_unapproved(business_case):
    case = business_case
    event_id, _, draft_id = paid_mapped(case)
    run_id = ocr_run(case, event_id, None, actor=case.finance, kind="proposal", book_id=case.book, draft_id=draft_id)
    finish(case, run_id, case.finance)
    params = (uuid4().hex, run_id, case.owner.firm_id, case.company, event_id, case.book, draft_id, 1, case.finance.user_id, STAMP)
    sql = "INSERT INTO ai_proposal_applications(id,run_id,firm_id,company_id,event_id,book_id,draft_id,applied_draft_version,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)"
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.viewer) as conn:
            conn.execute(sql, (*params[:8], case.viewer.user_id, STAMP))
    with pytest.raises(case.psycopg.errors.ForeignKeyViolation):
        with case.db.transaction(write=True, actor=case.finance) as conn:
            conn.execute(sql, (*params[:6], "other-draft", *params[7:]))
    with case.db.transaction(write=True, actor=case.finance) as conn:
        conn.execute(sql, params)
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == "draft"
    assert count_visible(case, case.reviewer, "ai_proposal_applications", event_id) == 1
    assert count_visible(case, case.employee, "ai_proposal_applications", event_id) == 0
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        with case.db.transaction(write=True, actor=case.finance) as conn:
            conn.execute(sql, (uuid4().hex, *params[1:]))


def test_ai_system_identity_role_cannot_read_business_intelligence(business_case):
    case = business_case
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(system=True) as conn:
            conn.execute("SELECT * FROM ai_runs")


def test_ai_open_event_reveals_only_scoped_boolean_and_freezes_staff_after_ledger_review(business_case):
    case = business_case
    event_id, _, draft_id = paid_mapped(case)

    def event_open(actor, company=None):
        with case.db.transaction(actor=actor) as conn:
            return conn.execute("SELECT accounting.ai_event_open(?,?,?)", (event_id, actor.firm_id, company or case.company)).fetchone()[0]

    for actor in (case.employee, case.approver, case.finance, case.reviewer, case.viewer):
        assert event_open(actor) is True
    for actor in (case.colleague, case.company_owner, case.company_admin, case.unassigned,
                  Actor(case.employee.user_id, "other-firm")):
        assert event_open(actor) is False
    assert event_open(case.employee, case.other_company) is False
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_mappings WHERE event_id=?", (event_id,)).fetchone()[0] == 0
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.drafts SET status='approved' WHERE id=%s", (draft_id,))
    for actor in (case.employee, case.approver, case.finance, case.reviewer, case.viewer):
        assert event_open(actor) is False
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(system=True) as conn:
            conn.execute("SELECT accounting.ai_event_open(?,?,?)", (event_id, case.owner.firm_id, case.company))
