"""Synthetic monthly service and independently accepted materials, both stores."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventCreate, HandoffRequest
from app.service_plans.models import (
    Generate,
    MaterialReview,
    MaterialSubmit,
    PlanSave,
    TemplateCreate,
)
from app.service_plans.service import ServicePlansService
from test_business import business, payload  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import month_case  # noqa: F401


@pytest.fixture
def plans(month_case):
    c = month_case
    c.plans = ServicePlansService(c.db, c.accounting, c.business)
    return c


def plan(c, **changes):
    return PlanSave(
        **{
            "expected_version": 0,
            "lead_user_id": c.owner.user_id,
            "reviewer_user_id": c.reviewer.user_id,
            "contact_user_id": c.employee.user_id,
            "due_on": "2026-11-10",
            "notes": "本月先核对原件及差异",
            "reason": "明确本月工作分工和交付期限",
            **changes,
        }
    )


def generated(c, assignee=None, count=1):
    template = c.plans.create_template(
        c.owner,
        c.book,
        TemplateCreate(
            name="虚构月度资料要求",
            items=[
                {
                    "key": "bank" + str(i),
                    "title": "本月银行付款资料" + str(i),
                    "description": "提交本人当月真实资料，或说明无发生",
                    "required": True,
                    "due_day": 10,
                    "assignee_user_id": assignee or c.employee.user_id,
                }
                for i in range(count)
            ],
            reason="显式建立本企业月度资料清单",
        ),
    )
    result = c.plans.generate(
        c.owner,
        c.book,
        template["id"],
        Generate(expected_version=1, reason="明确生成本月份资料要求"),
    )
    assert result["created"] == count
    return template, c.plans.book_requests(c.owner, c.book)["rows"][0]


def no_activity(version=1, mode="no_activity"):
    return MaterialSubmit(
        expected_version=version, mode=mode, note="本人核对本月没有此类业务发生", event_ids=[]
    )


def review(version=2, action="accept"):
    return MaterialReview(
        expected_version=version, action=action, reason="独立核对本月资料及无发生说明"
    )


def test_plan_current_candidates_cas_and_no_grants(plans):
    c = plans
    initial = c.plans.overview(c.owner, c.book)
    assert initial["plan"] is None
    candidates = {u["user_id"]: u for u in initial["candidates"]}
    assert (
        candidates[c.employee.user_id]["can_contact"]
        and not candidates[c.employee.user_id]["can_lead"]
    )
    with c.db.transaction(actor=c.owner) as conn:
        grants = [
            tuple(row)
            for row in conn.execute(
                "SELECT user_id,role FROM book_access WHERE book_id=? ORDER BY user_id", (c.book,)
            )
        ]
    saved = c.plans.save_plan(c.owner, c.book, plan(c))
    assert saved["plan"]["version"] == 1 and saved["plan"]["due_on"] == "2026-11-10"
    with pytest.raises(AccountingError) as stale:
        c.plans.save_plan(c.owner, c.book, plan(c))
    assert stale.value.status == 409
    with pytest.raises(AccountingError):
        c.plans.save_plan(
            c.owner, c.book, plan(c, expected_version=1, lead_user_id=c.employee.user_id)
        )
    with pytest.raises(AccountingError):
        c.plans.overview(c.employee, c.book)
    with pytest.raises(AccountingError):
        c.plans.overview(c.reviewer, c.other_book)
    with c.db.transaction(actor=c.owner) as conn:
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT user_id,role FROM book_access WHERE book_id=? ORDER BY user_id", (c.book,)
            )
        ] == grants
        assert (
            conn.execute(
                "SELECT count(*) FROM service_plan_versions WHERE book_id=?", (c.book,)
            ).fetchone()[0]
            == 1
        )


def test_material_no_event_existence_idempotency_pagination_and_independent_confirmation(plans):
    c = plans
    template, _ = generated(c, count=3)
    assert c.plans.generate(
        c.owner,
        c.book,
        template["id"],
        Generate(expected_version=1, reason="重复点击不会重复创建资料"),
    ) == {"created": 0, "reused": 3}
    page = c.plans.my_requests(c.employee, c.company, "2026-10", limit=1, offset=1)
    assert len(page["rows"]) == 1 and page["pagination"]["total"] == 3
    row = page["rows"][0]
    assert row["state"] == "pending" and row["latest_submission"] is None
    submitted = c.plans.submit(c.employee, row["id"], no_activity())
    assert submitted["state"] == "submitted" and submitted["evidence_current"]
    with pytest.raises(AccountingError):
        c.plans.submit(c.employee, row["id"], no_activity())
    accepted = c.plans.review(c.reviewer, row["id"], review())
    assert accepted["state"] == "accepted" and accepted["version"] == 3
    assert not accepted["permissions"]["submit"]
    assert c.plans.my_requests(c.employee, request_id=row["id"])["rows"][0]["id"] == row["id"]
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM business_events WHERE company_id=?", (c.company,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) FROM drafts WHERE book_id=?", (c.book,)).fetchone()[0]
            == 0
        )


def test_return_resubmit_keeps_history_and_scopes(plans):
    c = plans
    _, row = generated(c)
    c.plans.submit(c.employee, row["id"], no_activity())
    returned = c.plans.review(c.reviewer, row["id"], review(action="return"))
    assert returned["state"] == "returned" and returned["version"] == 3
    c.plans.submit(c.employee, row["id"], no_activity(3, "not_applicable"))
    assert c.plans.review(c.reviewer, row["id"], review(4))["version"] == 5
    assert c.plans.my_requests(c.finance)["pagination"]["total"] == 0
    with pytest.raises(AccountingError):
        c.plans.submit(c.finance, row["id"], no_activity(5))
    with pytest.raises(AccountingError):
        c.plans.generate(
            c.owner,
            c.other_book,
            row["id"],
            Generate(expected_version=1, reason="不能跨企业使用资料模板"),
        )
    with c.db.transaction(actor=c.owner) as conn:
        actions = conn.execute(
            "SELECT action FROM material_request_actions WHERE request_id=? ORDER BY version",
            (row["id"],),
        ).fetchall()
        assert [r[0] for r in actions] == ["submit", "return", "submit", "accept"]


def test_native_history_cannot_be_rewritten_and_same_person_review_forbidden(plans):
    c = plans
    # A dual-role firm reviewer still cannot accept their own submission.
    from app.accounting.models import BookMemberGrant
    from app.accounting.directory import DirectoryService

    # Explicitly hold a firm reviewer job as well, so this still tests the
    # stronger "qualified reviewer cannot accept their own submission" rule.
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute("INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'reviewer')", (c.owner.firm_id,c.finance.user_id))
    DirectoryService(c.db).grant_book_member(
        c.owner, c.book, c.finance.user_id, BookMemberGrant(role="reviewer")
    )
    _, row = generated(c, c.finance.user_id)
    c.plans.submit(c.finance, row["id"], no_activity())
    with pytest.raises(AccountingError) as self_review:
        c.plans.review(c.finance, row["id"], review())
    assert self_review.value.status == 403
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute(
                "INSERT INTO material_request_actions(id,request_id,book_id,firm_id,company_id,version,action,state,reason,event_ids_json,evidence_json,created_by,created_at) VALUES(?,?,?,?,?,3,'accept','accepted','直接SQL也不可自行验收','[]','[]',?,'2026-10-16')",
                (uuid4().hex, row["id"], c.book, c.owner.firm_id, c.company, c.finance.user_id),
            )
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "UPDATE material_requests SET title='覆盖原始要求' WHERE id=?", (row["id"],)
            )
    assert c.plans.review(c.reviewer, row["id"], review())["state"] == "accepted"


def test_submit_evidence_respects_fact_month_and_existing_business_visibility(plans):
    c = plans
    _, row = generated(c)
    event = c.business.create(
        c.employee,
        BusinessEventCreate(company_id=c.company, template_id="reimbursement", payload=payload()),
    )
    event = c.business.add_attachment(
        c.employee,
        event["id"],
        event["version"],
        {
            "id": uuid4().hex,
            "storage_key": "synthetic/" + uuid4().hex,
            "filename": "合成原件.pdf",
            "media_type": "application/pdf",
            "sha256": "a" * 64,
            "size_bytes": 20,
        },
    )
    event = c.business.submit(c.employee, event["id"], event["version"])
    submitted = c.plans.submit(
        c.employee,
        row["id"],
        MaterialSubmit(
            expected_version=1,
            mode="provided",
            note="提交本月业务与对应原始附件",
            event_ids=[event["id"]],
        ),
    )
    assert submitted["evidence_current"]
    public = c.plans.book_requests(c.reviewer, c.book, request_id=row["id"])["rows"][0]
    assert not public["evidence_current"] and public["latest_submission"]["evidence"] == []
    with pytest.raises(AccountingError) as before_handoff:
        c.plans.review(c.reviewer, row["id"], review())
    assert before_handoff.value.status == 409
    approver = c.raw.actors["approver"] if hasattr(c.raw, "actors") else c.raw.approver
    event = c.business.approve_department(approver, event["id"], event["version"])
    event = c.business.approve_finance(c.finance, event["id"], event["version"])
    c.business.handoff(
        c.finance,
        event["id"],
        HandoffRequest(
            expected_version=event["version"],
            book_id=c.book,
            idempotency_key="stage25-handoff-" + uuid4().hex,
        ),
    )
    accepted = c.plans.review(c.reviewer, row["id"], review())
    assert accepted["state"] == "accepted" and accepted["evidence_current"]
    assert accepted["latest_submission"]["evidence"][0]["attachments"][0]["sha256"] == "a" * 64
    with c.db.transaction(actor=c.owner) as conn:
        stamps = conn.execute(
            "SELECT evidence_json FROM material_request_actions WHERE request_id=? AND action='submit'",
            (row["id"],),
        ).fetchone()[0]
        assert "合成原件" not in stamps and "description" not in stamps
        assert (
            conn.execute("SELECT status FROM drafts WHERE book_id=?", (c.book,)).fetchone()[0]
            == "draft"
        )


def test_plan_two_writers_preserve_one_version(plans):
    c = plans
    command = plan(c)

    def write(_):
        try:
            c.plans.save_plan(c.owner, c.book, command)
            return "saved"
        except AccountingError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(write, range(2)), key=str) == sorted(["saved", 409], key=str)


@pytest.mark.parametrize(
    "change",
    [
        {"expected_version": True},
        {"lead_user_id": "a", "reviewer_user_id": "a"},
        {"due_on": "2026-02-30"},
    ],
)
def test_plan_strict_contract(change):
    with pytest.raises(ValidationError):
        PlanSave(
            **{"expected_version": 0, "due_on": "2026-11-10", "reason": "明确分工与日期", **change}
        )


def test_material_strict_modes_and_unique_keys():
    for body in (
        {"mode": "provided", "event_ids": []},
        {"mode": "no_activity", "event_ids": ["e"]},
        {"mode": "provided", "event_ids": ["e", "e"]},
    ):
        with pytest.raises(ValidationError):
            MaterialSubmit(expected_version=1, note="明确资料情况", **body)
    with pytest.raises(ValidationError):
        TemplateCreate(
            name="资料清单", items=[{"key": "same", "title": "资料"}] * 2, reason="明确创建依据"
        )


def test_required_material_blocks_close_and_invalidates_passed_snapshot(plans):
    from app.closing.models import CloseRequest, PreflightRequest, SnapshotReview
    from app.closing.service import ClosingService

    c = plans
    closing = ClosingService(c.db, c.accounting)
    old = closing.preflight(c.owner, c.book, PreflightRequest(idempotency_key=uuid4().hex))
    assert old["report"]["passed"]
    closing.review(
        c.owner,
        c.book,
        old["id"],
        SnapshotReview(reason="独立确认当月已知资料及流水完整", materials_confirmed=True),
    )
    _, row = generated(c)
    current = closing.overview(c.owner, c.book)
    assert current["current_report"]["counts"]["required_material"] == 1
    assert closing.get_snapshot(c.owner, c.book, old["id"])["stale"]
    with pytest.raises(AccountingError):
        closing.close(
            c.owner, c.book, CloseRequest(snapshot_id=old["id"], reason="旧快照不可绕过新资料要求")
        )
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute("UPDATE periods SET status='closed' WHERE book_id=?", (c.book,))
    c.plans.submit(c.employee, row["id"], no_activity())
    assert closing.overview(c.owner, c.book)["current_report"]["counts"]["required_material"] == 1
    c.plans.review(c.reviewer, row["id"], review())
    assert closing.overview(c.owner, c.book)["current_report"]["passed"]
    c.plans.review(c.reviewer, row["id"], review(3, "return"))
    assert closing.overview(c.owner, c.book)["current_report"]["counts"]["required_material"] == 1
    c.plans.submit(c.employee, row["id"], no_activity(4))
    c.plans.review(c.reviewer, row["id"], review(5))
    fresh = closing.preflight(c.owner, c.book, PreflightRequest(idempotency_key=uuid4().hex))
    closing.review(
        c.owner,
        c.book,
        fresh["id"],
        SnapshotReview(reason="确认新资料清单已独立验收", materials_confirmed=True),
    )
    closing.close(
        c.owner, c.book, CloseRequest(snapshot_id=fresh["id"], reason="完成资料核对后正式锁定期间")
    )
    with pytest.raises(AccountingError):
        c.plans.save_plan(c.owner, c.book, plan(c))


def test_template_explicit_new_month_reuse_year_rollover_and_contact_fallback(plans):
    from app.ledgers.models import PeriodCreate
    from app.ledgers.service import LedgersService

    c = plans
    template = c.plans.create_template(
        c.owner,
        c.book,
        TemplateCreate(
            name="长期每月银行资料",
            items=[
                {
                    "key": "statement",
                    "title": "月末银行对账单",
                    "assignee_user_id": None,
                    "due_day": 12,
                    "due_month_offset": 1,
                }
            ],
            reason="新月份显式生成并使用本期联系人",
        ),
    )
    with pytest.raises(AccountingError, match="指派"):
        c.plans.generate(
            c.owner,
            c.book,
            template["id"],
            Generate(expected_version=1, reason="缺联系人不能生成无人任务"),
        )
    created = LedgersService(c.db).create_period(
        c.owner,
        c.book,
        PeriodCreate(
            period="2026-12",
            name="同企业十二月",
            source_book_id=c.book,
            inherit_user_ids=[],
            idempotency_key=uuid4().hex,
        ),
    )
    target = created["book_id"]
    assert c.plans.book_requests(c.owner, target)["pagination"]["total"] == 0
    assert c.plans.overview(c.owner, target)["templates"][0]["id"] == template["id"]
    c.plans.save_plan(c.owner, target, plan(c, reviewer_user_id=None, due_on="2027-01-15"))
    assert (
        c.plans.generate(
            c.owner,
            target,
            template["id"],
            Generate(expected_version=1, reason="明确生成十二月所需资料"),
        )["created"]
        == 1
    )
    row = c.plans.my_requests(c.employee, c.company, "2026-12")["rows"][0]
    assert row["period"] == "2026-12" and row["due_on"] == "2027-01-12"
    assert row["assignee_user_id"] == c.employee.user_id
    assert c.plans.book_requests(c.owner, c.book)["pagination"]["total"] == 0


def test_current_membership_and_book_grant_revocation_hides_requests_immediately(plans):
    from app.accounting.directory import DirectoryService

    c = plans
    _, row = generated(c)
    directory = DirectoryService(c.db)
    directory.revoke_book_member(c.owner, c.book, c.reviewer.user_id)
    with pytest.raises(AccountingError):
        c.plans.book_requests(c.reviewer, c.book)
    with pytest.raises(AccountingError):
        c.plans.save_plan(c.owner, c.book, plan(c))
    assert c.plans.my_requests(c.employee)["pagination"]["total"] == 1
    directory.revoke_company_member(c.owner, c.company, c.employee.user_id)
    assert c.plans.my_requests(c.employee)["pagination"]["total"] == 0
    with pytest.raises(AccountingError):
        c.plans.submit(c.employee, row["id"], no_activity())


def test_pg_native_rls_employee_has_no_book_or_other_material_directory(plans):
    c = plans
    if not c.db.is_postgres:
        pytest.skip("原生RLS仅PostgreSQL")
    _, row = generated(c)
    with c.db.transaction(actor=c.employee) as conn:
        assert conn.execute("SELECT count(*) FROM books WHERE id=?", (c.book,)).fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM service_plan_versions WHERE book_id=?", (c.book,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM material_templates WHERE book_id=?", (c.book,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM material_requests WHERE id=?", (row["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM accounting.service_plan_candidates(?)", (c.book,)
            ).fetchone()[0]
            == 0
        )
    with c.db.transaction(actor=c.finance) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM material_requests WHERE id=?", (row["id"],)
            ).fetchone()[0]
            == 1
        )
