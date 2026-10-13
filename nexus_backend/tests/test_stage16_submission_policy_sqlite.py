"""SQLite policy guards share PostgreSQL's consecutive-version contract."""

import pytest

from app.business.models import SubmissionPolicyUpdate
from test_business import business, attach, payload
from test_stage16_submission import setup_direct


def test_policy_raw_sql_scope_version_and_valid_upsert(business):
    c = business
    setup_direct(c)
    for statement, arguments in (
        (
            "UPDATE business_submission_policies SET version=version+4 WHERE company_id=?",
            (c.company["id"],),
        ),
        (
            "UPDATE business_submission_policies SET company_id=?,version=version+1 WHERE company_id=?",
            (c.other["id"], c.company["id"]),
        ),
        (
            "INSERT INTO business_submission_policies(firm_id,company_id,mode,version,reason,updated_by,updated_at) VALUES(?,?,'direct',7,'跳版本',?,CURRENT_TIMESTAMP)",
            (c.manager.firm_id, c.other["id"], c.manager.user_id),
        ),
    ):
        with pytest.raises(Exception):
            with c.db.transaction(write=True, actor=c.manager) as conn:
                conn.execute(statement, arguments)
    assert (
        c.service.submission.update(
            c.manager,
            c.company["id"],
            SubmissionPolicyUpdate(
                mode="department",
                expected_version=1,
                reason="只影响新事项",
            ),
        )["version"]
        == 2
    )


def test_direct_with_department_never_enters_department_approval_todo(business):
    from app.accounting.review import ReviewService
    from app.business.models import BusinessEventCreate
    from app.cases.service import CaseService
    from app.workspace.service import WorkspaceService

    c = business
    boss = setup_direct(c)
    event = c.service.create(
        boss,
        BusinessEventCreate(
            company_id=c.company["id"],
            department_id=c.department["id"],
            template_id="reimbursement",
            payload=payload(),
        ),
    )
    event = attach(c, event, "boss")
    event = c.service.submit(boss, event["id"], event["version"])
    workspace = WorkspaceService(
        c.db, c.ledger, c.service, ReviewService(c.db, c.ledger), CaseService(c.db, c.service)
    )
    assert not any(
        row["kind"] == "approval" and row["event_id"] == event["id"]
        for row in workspace.list(c.actors["approver"])["tasks"]
    )
    own = next(
        row for row in workspace.list(boss, bucket="all")["tasks"] if row["event_id"] == event["id"]
    )
    assert own["responsible"] == "授权会计"
