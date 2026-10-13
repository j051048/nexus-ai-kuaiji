"""Native SQL policy/event guards and actual PostgreSQL workbench queries."""

from uuid import uuid4

import pytest

from app.accounting.review import ReviewService
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, BookMemberGrant, ImportRequest
from app.accounting.directory import DirectoryService
from app.accounting.service import fingerprint
from app.business.models import (
    BusinessEventCreate,
    BusinessEventUpdate,
    HandoffRequest,
    ReturnRequest,
    SubmissionPolicyUpdate,
)
from app.business.service import BusinessService
from app.cases.service import CaseService
from app.workspace.service import WorkspaceService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage16_submission_postgres import direct_event
from test_business import payload

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要隔离 PostgreSQL")


def test_policy_raw_sql_cannot_jump_version_change_scope_or_start_above_one(business_case):
    c = business_case
    service, _, _ = direct_event(c)
    for statement, arguments in (
        (
            "UPDATE business_submission_policies SET version=version+4 WHERE company_id=?",
            (c.company,),
        ),
        (
            "UPDATE business_submission_policies SET company_id=?,version=version+1 WHERE company_id=?",
            (c.other_company, c.company),
        ),
        (
            "INSERT INTO business_submission_policies(firm_id,company_id,mode,version,reason,updated_by,updated_at) VALUES(?,?,'direct',7,'跳版本',?,CURRENT_TIMESTAMP)",
            (c.owner.firm_id, c.other_company, c.owner.user_id),
        ),
    ):
        with pytest.raises(Exception):
            with c.db.transaction(write=True, actor=c.owner) as conn:
                conn.execute(statement, arguments)
    assert (
        service.submission.update(
            c.owner,
            c.company,
            SubmissionPolicyUpdate(
                mode="department",
                expected_version=1,
                reason="只影响新事项",
            ),
        )["version"]
        == 2
    )
    with c.db.transaction(actor=c.owner) as conn:
        rows = conn.execute(
            "SELECT company_id,version FROM business_submission_policies"
        ).fetchall()
        assert [(row["company_id"], row["version"]) for row in rows] == [(c.company, 2)]


def test_returned_direct_facts_are_editable_only_by_applicant(business_case):
    c = business_case
    service, boss, event = direct_event(c)
    returned = service.return_event(
        c.reviewer,
        event["id"],
        ReturnRequest(
            expected_version=event["version"],
            reason="请老板核对金额",
        ),
    )
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute(
                "UPDATE business_events SET payload=jsonb_set(payload::jsonb,'{amount}','\"99.99\"')::text,version=version+1,fact_version=fact_version+1 WHERE id=?",
                (event["id"],),
            )
    assert service.get_event(boss, event["id"])["payload"]["amount"] == event["payload"]["amount"]
    assert service.get_event(boss, event["id"])["version"] == returned["version"]


def test_actual_pg_accounting_list_workspace_and_revoked_receiver(business_case):
    c = business_case
    service, _, event = direct_event(c)
    workspace = WorkspaceService(
        c.db, c.service, service, ReviewService(c.db, c.service), CaseService(c.db, service)
    )
    assert service.list_events(c.reviewer, scope="accounting")["total"] == 1
    tasks = workspace.list(c.reviewer)["tasks"]
    assert any(row["kind"] == "handoff" and row["event_id"] == event["id"] for row in tasks)
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book, c.reviewer.user_id)
        )
    with pytest.raises(AccountingError, match="授权账套"):
        service.list_events(c.reviewer, scope="accounting")
    assert not any(row["event_id"] == event["id"] for row in workspace.list(c.reviewer)["tasks"])


def test_receiver_cannot_switch_resubmitted_event_to_an_old_snapshot(business_case):
    c = business_case
    service, boss, event = direct_event(c)
    old_snapshot = event["submitted_version"]
    returned = service.return_event(
        c.reviewer,
        event["id"],
        ReturnRequest(
            expected_version=event["version"],
            reason="请补充业务金额",
        ),
    )
    edited = service.update(
        boss,
        event["id"],
        BusinessEventUpdate(
            expected_version=returned["version"],
            payload={**event["payload"], "amount": "99.99"},
        ),
    )
    fresh = service.submit(boss, event["id"], edited["version"])
    assert fresh["submitted_version"] != old_snapshot
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute(
                "UPDATE business_events SET submitted_version=?,version=version+1 WHERE id=?",
                (old_snapshot, event["id"]),
            )
    assert service.get_event(boss, event["id"])["submitted_version"] == fresh["submitted_version"]


def attachment(service, actor, event):
    return service.add_attachment(
        actor,
        event["id"],
        event["version"],
        {
            "id": uuid4().hex,
            "storage_key": uuid4().hex,
            "filename": "synthetic.pdf",
            "media_type": "application/pdf",
            "sha256": "c" * 64,
            "size_bytes": 40,
        },
    )


def native_mapping(case, actor, event, book_id, date, snapshot=None):
    marker = uuid4().hex
    case.service.import_sources(
        actor,
        book_id,
        ImportRequest(
            source_name="SQL guard fixture",
            rows=[
                {
                    "external_id": marker,
                    "date": date,
                    "description": "合成业务",
                    "counterparty": "合成往来方",
                    "amount": "0.30",
                    "direction": "expense",
                    "evidence_ref": "合成票据",
                }
            ],
        ),
    )
    case.service.suggestions(actor, book_id)
    data = case.service.get_book(actor, book_id)
    source = next(row for row in data["sources"] if row["external_id"] == marker)
    draft = next(row for row in data["drafts"] if row["source_id"] == source["id"])
    return (
        event["id"],
        actor.firm_id,
        event["company_id"],
        book_id,
        source["id"],
        draft["id"],
        snapshot or event["submitted_version"],
        uuid4().hex,
        fingerprint(
            {
                "event_id": event["id"],
                "book_id": book_id,
                "submitted_version": event["submitted_version"],
            }
        ),
        actor.user_id,
    )


def insert_mapping(conn, values):
    conn.execute(
        "INSERT INTO business_mappings(event_id,firm_id,company_id,book_id,source_id,draft_id,submitted_version,idempotency_key,request_fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        values,
    )


def test_direct_finance_applicant_cannot_create_own_mapping_through_native_sql(business_case):
    c = business_case
    service = BusinessService(c.db)
    service.submission.update(
        c.owner,
        c.company,
        SubmissionPolicyUpdate(mode="direct", expected_version=0, reason="财务集中提交"),
    )
    event = service.create(
        c.finance,
        BusinessEventCreate(company_id=c.company, template_id="reimbursement", payload=payload()),
    )
    event = attachment(service, c.finance, event)
    event = service.submit(c.finance, event["id"], event["version"])
    values = native_mapping(c, c.finance, event, c.book, "2026-10-03")
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            insert_mapping(conn, values)
    with c.db.transaction(actor=c.finance) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM business_mappings WHERE event_id=?", (event["id"],)
            ).fetchone()[0]
            == 0
        )


def test_direct_event_cannot_forge_department_approval_history(business_case):
    c = business_case
    _, _, event = direct_event(c)
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute(
                "INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,decision,reason,created_by,created_at) VALUES(?,?,?,?,?,'department','approve','伪造审批',?,CURRENT_TIMESTAMP)",
                (
                    uuid4().hex,
                    event["id"],
                    c.finance.firm_id,
                    c.company,
                    event["submitted_version"],
                    c.finance.user_id,
                ),
            )


def test_direct_mapping_rejects_wrong_period_and_old_submission_snapshot(business_case):
    c = business_case
    service, boss, event = direct_event(c)
    november = c.service.create_book(
        c.owner, BookCreate(name="同企业十一月", period="2026-11", company_id=c.company)
    )
    DirectoryService(c.db).grant_book_member(
        c.owner, november["id"], c.reviewer.user_id, BookMemberGrant(role="reviewer")
    )
    wrong_month = native_mapping(c, c.reviewer, event, november["id"], "2026-11-03")
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            insert_mapping(conn, wrong_month)
    old_snapshot = event["submitted_version"]
    returned = service.return_event(
        c.reviewer,
        event["id"],
        ReturnRequest(expected_version=event["version"], reason="重新提供资料"),
    )
    fresh = service.submit(boss, event["id"], returned["version"])
    stale = native_mapping(c, c.reviewer, fresh, c.book, "2026-10-03", old_snapshot)
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            insert_mapping(conn, stale)


def test_department_channel_keeps_approval_and_handoff_workflow(business_case):
    c = business_case
    service = BusinessService(c.db)
    event = service.create(
        c.employee,
        BusinessEventCreate(
            company_id=c.company,
            department_id=c.department,
            template_id="reimbursement",
            payload=payload(),
        ),
    )
    event = attachment(service, c.employee, event)
    event = service.submit(c.employee, event["id"], event["version"])
    event = service.approve_department(c.approver, event["id"], event["version"])
    event = service.approve_finance(c.finance, event["id"], event["version"])
    result = service.handoff(
        c.finance,
        event["id"],
        HandoffRequest(
            expected_version=event["version"], book_id=c.book, idempotency_key=uuid4().hex
        ),
    )
    assert result["event"]["status"] == "handed_off"
