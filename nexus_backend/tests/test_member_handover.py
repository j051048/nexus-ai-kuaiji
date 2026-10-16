"""受审计的工作交接转派：账套职责、服务单分工与待处理任务的原子化移交。"""

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.handover import HandoverRequest, HandoverService
from app.accounting.models import BookCreate, BookMemberGrant, FirmMemberGrant
from app.cases.models import CaseAssign
from app.cases.service import CaseService
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from test_business import business
from test_stage8_cases import request as case_request
from test_stage8_cases import setup as case_setup


@pytest.fixture
def handover_case(business):
    c = business
    identity = SimpleNamespace(
        user_id="handover-receiver",
        display_name="接替会计",
        email="handover-receiver@example.test",
    )
    c.directory.register_identity(identity)
    c.directory.grant_firm_member(
        c.manager, FirmMemberGrant(email=identity.email, role="reviewer")
    )
    c.receiver = Actor(identity.user_id, c.manager.firm_id)
    c.cases = CaseService(c.db, c.service)
    c.plans = ServicePlansService(c.db, c.ledger, c.service)
    c.handover = HandoverService(c.db, c.ledger, c.plans, c.cases)
    return c


def test_handover_moves_book_role_plan_field_and_case_assignee(handover_case):
    c = handover_case
    accountant = c.actors["accountant"]
    c.plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            lead_user_id=accountant.user_id,
            reviewer_user_id=c.manager.user_id,
            contact_user_id="finance",
            due_on=date(2026, 11, 10),
            reason="转派前的服务单分工",
        ),
    )
    cases, event, _mapping, task = case_setup(c, "exception")
    task = cases.command(
        accountant,
        task["id"],
        "assign",
        case_request(
            c, task, CaseAssign, assignee_id=accountant.user_id, due_on="2026-10-20", priority="normal"
        ),
    )
    assert task["assignee_id"] == accountant.user_id

    preview = c.handover.preview(c.manager, accountant.user_id, c.receiver.user_id)
    assert preview["executed"] is False
    assert preview["counts"] == {"books": 1, "plans": 1, "cases": 1, "skipped": 0}
    assert preview["books"][0]["role"] == "reviewer" and preview["books"][0]["action"] == "transfer"
    assert preview["plans"][0]["changes"] == {"lead_user_id": c.receiver.user_id}

    result = c.handover.execute(
        c.manager,
        accountant.user_id,
        HandoverRequest(to_user_id=c.receiver.user_id, reason="会计离职：全量工作交接"),
    )
    assert result["executed"] is True and result["counts"]["skipped"] == 0
    with c.db.transaction() as conn:
        access = {
            row["user_id"]: row["role"]
            for row in conn.execute(
                "SELECT user_id,role FROM book_access WHERE book_id=?", (c.book["id"],)
            )
        }
        assert accountant.user_id not in access and access[c.receiver.user_id] == "reviewer"
        plan = conn.execute(
            "SELECT * FROM service_plan_versions WHERE book_id=? ORDER BY version DESC",
            (c.book["id"],),
        ).fetchone()
        assert plan["version"] == 2 and plan["lead_user_id"] == c.receiver.user_id
        assert plan["reviewer_user_id"] == c.manager.user_id and plan["contact_user_id"] == "finance"
        assert "人员交接转派" in plan["reason"]
        case_row = conn.execute(
            "SELECT assignee_id FROM case_tasks WHERE id=?", (task["id"],)
        ).fetchone()
        assert case_row["assignee_id"] == c.receiver.user_id
        audits = [
            dict(row)
            for row in conn.execute("SELECT * FROM audit_events WHERE action='member.handover'")
        ]
        assert len(audits) == 1
        details = json.loads(audits[0]["details"])
        assert details["to_user_id"] == c.receiver.user_id
        assert details["books"][0]["book_id"] == c.book["id"]
        assert details["cases"] == [task["id"]]
        assert details["reason"] == "会计离职：全量工作交接"
    assert c.cases.list(c.receiver)["total"] == 1
    after = c.handover.preview(c.manager, accountant.user_id, c.receiver.user_id)
    assert after["counts"] == {"books": 0, "plans": 0, "cases": 0, "skipped": 0}


def test_handover_reports_role_conflicts_and_independence_violations(handover_case):
    c = handover_case
    accountant = c.actors["accountant"]
    book = c.ledger.create_book(
        c.manager, BookCreate(name="冲突账套", period="2026-10", company_id=c.company["id"])
    )
    c.directory.grant_book_member(
        c.manager, book["id"], accountant.user_id, BookMemberGrant(role="editor")
    )
    c.directory.grant_book_member(
        c.manager, book["id"], c.receiver.user_id, BookMemberGrant(role="reviewer")
    )
    c.plans.save_plan(
        c.manager,
        book["id"],
        PlanSave(
            expected_version=0,
            lead_user_id=accountant.user_id,
            reviewer_user_id=c.receiver.user_id,
            due_on=date(2026, 11, 10),
            reason="冲突验证分工",
        ),
    )
    preview = c.handover.preview(c.manager, accountant.user_id, c.receiver.user_id)
    # 仅第一客户账套可转派；冲突账套的账套职责与分工都进入跳过清单。
    assert preview["counts"]["books"] == 1 and preview["counts"]["plans"] == 0
    assert [item["book_id"] for item in preview["books"]] == [c.book["id"]]
    reasons = "|".join(item["reason"] for item in preview["skipped"])
    assert "主办与复核不得同人" in reasons
    assert "需另行指定他人" in reasons
    with c.db.transaction() as conn:
        access = {
            row["user_id"]: row["role"]
            for row in conn.execute(
                "SELECT user_id,role FROM book_access WHERE book_id=?", (book["id"],)
            )
        }
        assert access[accountant.user_id] == "editor" and access[c.receiver.user_id] == "reviewer"


def test_handover_requires_manager_and_valid_receiver(handover_case):
    c = handover_case
    accountant = c.actors["accountant"]
    with pytest.raises(AccountingError) as denied:
        c.handover.preview(accountant, accountant.user_id, c.receiver.user_id)
    assert denied.value.status == 403
    with pytest.raises(AccountingError) as same:
        c.handover.preview(c.manager, accountant.user_id, accountant.user_id)
    assert same.value.status == 409
    with pytest.raises(AccountingError) as ineligible:
        c.handover.preview(c.manager, accountant.user_id, c.actors["employee"].user_id)
    assert ineligible.value.status == 409
    with pytest.raises(AccountingError) as missing:
        c.handover.preview(c.manager, accountant.user_id, "not-a-member")
    assert missing.value.status == 404
