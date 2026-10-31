"""Read-only delivery diagnosis from current business services, not model prose."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant.models import ConversationCreate, TurnCreate
from app.assistant.fact_validation import render_assistant_reply
from app.assistant.context_pack import build_assistant_context_pack
from app.assistant.provider import PROMPT_VERSION
from app.intelligence.provider import ExecutionLimits
from app.service_plans.service import ServicePlansService
from app.service_plans.models import PlanSave
from test_ak04_assistant_execution import execution_case
from test_intelligence_workflow import ai_case, create_event, handoff
from test_model_cost_workflow import cost_case, cost_rows, policy
from test_business import business
from test_p0_close_dependencies import close_case
from test_monthly_delivery_package import ready_case, freeze
from test_intelligence_workflow import extract

QUESTION = "为什么这家企业本月还不能交付？"


def enqueue(c, actor=None, **scope):
    actor = actor or c.manager
    conversation = c.chat_service.create(
        actor,
        ConversationCreate(
            company_id=c.company["id"],
            idempotency_key=uuid4().hex,
            **scope,
        ),
    )["conversation"]
    turn = c.chat_service.enqueue(
        actor,
        conversation["id"],
        TurnCreate(
            question=QUESTION,
            expected_scope_version=1,
            idempotency_key=uuid4().hex,
        ),
    )["turn"]
    return actor, conversation, turn


def test_task_reuses_delivery_blockers_and_plan_without_model_authority(execution_case):
    c = execution_case
    policy(c, chat_reserve="0.10")
    event = create_event(c)
    receipt = handoff(c, event)
    plans = ServicePlansService(c.db, c.ledger, c.business)
    plans.save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=0,
            due_on="2026-11-10",
            reason="虚构服务责任",
            lead_user_id="finance",
            reviewer_user_id="accountant",
            notes="PRIVATE_PLAN_NOTE",
        ),
    )
    actor, conv, turn = enqueue(
        c,
        c.actors["accountant"],
        book_ids=[c.book["id"]],
        event_ids=[event["id"]],
        draft_id=receipt["draft_id"],
    )
    c.chat_worker.run_once()
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    answer = result["reply"]["answer"]
    assert "2026-10" in answer and "待会计复核" in answer
    assert "会计复核员" in answer and "复核凭证" in answer
    assert "2026-11-10" in answer and "客户联系人" in answer
    assert result["reply"]["citation_ids"] and result["reply"]["proposals"] == []
    raw = json.dumps(c.calls, ensure_ascii=False)
    assert "PRIVATE_PLAN_NOTE" not in raw
    pack = json.loads(c.calls[0]["messages"][1]["content"])["context_pack"]
    fact = next(row for row in pack["facts"] if row["key"] == "delivery_task")
    assert fact["value"]["coverage"] == "selected_book"
    assert fact["value"]["can_deliver"] is None  # no accounting approval decision
    assert c.other_book["id"] not in raw


def test_plan_change_after_send_invalidates_old_reply_but_retains_usage(execution_case):
    c = execution_case
    policy(c, chat_reserve="0.10")
    plans = ServicePlansService(c.db, c.ledger, c.business)
    actor, conv, turn = enqueue(c, book_ids=[c.book["id"]])

    def change():
        plans.save_plan(
            c.manager,
            c.book["id"],
            PlanSave(
                expected_version=0,
                due_on="2026-11-20",
                reason="虚构变更期限",
            ),
        )

    c.hook = change
    c.chat_worker.run_once()
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert result["status"] == "stale" and result["reply"] is None
    assert len(c.calls) == 1 and cost_rows(c)[0]["state"] == "estimated"


def test_foreign_or_multiple_periods_are_not_guessed(execution_case):
    c = execution_case
    actor, conv, turn = enqueue(c)
    c.chat_worker.run_once()
    answer = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]["reply"]["answer"]
    assert "选择一个账套和月份" in answer
    assert c.other_book["id"] not in json.dumps(c.calls)


def project(c, actor=None):
    from app.assistant.delivery_context import project_delivery

    actor = actor or c.manager
    with c.db.transaction(actor=actor) as conn:
        book = c.ledger.access.book(conn, actor, c.book["id"])
        snapshot = {
            "company_id": c.company["id"],
            "books": [{"id": book["id"], "period": book["period"]}],
            "events": [],
            "draft": None,
            "evidence": [{"id": "book:" + book["id"], "kind": "book", "book_id": book["id"]}],
        }
        return project_delivery(c.db, conn, actor, snapshot)


def test_preflight_fingerprint_and_checks_are_reused(close_case):
    c = close_case
    projection = project(c)
    source = next(row for row in projection["sources"] if row["kind"] == "close")
    assert source["id"] == c.run["close"]["id"]
    assert source["inputs_fingerprint"] == c.run["close"]["inputs_fingerprint"]
    assert source["current"] is True
    assert "published_report_missing" in {row["code"] for row in projection["issues"]}


def test_customer_does_not_receive_internal_plan_or_preflight(ready_case):
    c = ready_case
    projection = project(c, c.actors["finance"])
    assert projection["coverage"] == "selected_items"
    assert not projection["sources"]
    assert "contact_missing" not in {row["code"] for row in projection["issues"]}


def test_frozen_package_is_not_claimed_received_or_posted(ready_case):
    c = ready_case
    package = freeze(c)
    projection = project(c)
    source = next(row for row in projection["sources"] if row["kind"] == "package")
    assert source["id"] == package["id"] and source["version"] == package["version"]
    assert source["manifest_sha256"] == package["manifest_sha256"]
    assert "awaiting_customer_ack" in {row["code"] for row in projection["issues"]}
    assert projection["can_deliver"] is None


def test_projection_is_read_only(execution_case, monkeypatch):
    c = execution_case
    from app.assistant.delivery_context import project_delivery

    with c.db.transaction(actor=c.manager) as conn:

        class ReadOnly:
            def execute(self, sql, args=()):
                assert sql.lstrip().upper().startswith("SELECT "), sql
                return conn.execute(sql, args)

        project_delivery(
            c.db,
            ReadOnly(),
            c.manager,
            {
                "company_id": c.company["id"],
                "books": [{"id": c.book["id"], "period": "2026-10"}],
                "events": [],
                "draft": None,
                "evidence": [],
            },
        )


def test_no_monthly_inspection_for_ordinary_questions(execution_case, monkeypatch):
    from app.assistant import delivery_context

    c = execution_case

    def forbidden(*args):
        raise AssertionError("普通问题不得触发完整月度诊断")

    monkeypatch.setattr(delivery_context, "project_delivery", forbidden)
    actor = c.manager
    conv = c.chat_service.create(
        actor,
        ConversationCreate(
            company_id=c.company["id"],
            book_ids=[c.book["id"]],
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]
    turn = c.chat_service.enqueue(
        actor,
        conv["id"],
        TurnCreate(
            question="解释当前金额",
            expected_scope_version=1,
            idempotency_key=uuid4().hex,
        ),
    )["turn"]
    c.chat_worker.run_once()
    assert c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]["status"] == "succeeded"


def test_plan_change_at_atomic_finish_not_just_after_response(execution_case, monkeypatch):
    c = execution_case
    actor, conv, turn = enqueue(c, book_ids=[c.book["id"]])
    original = c.chat_worker.finish

    def finish(*args, **kwargs):
        ServicePlansService(c.db, c.ledger, c.business).save_plan(
            c.manager,
            c.book["id"],
            PlanSave(expected_version=0, due_on="2026-11-21", reason="提交边界变化"),
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(c.chat_worker, "finish", finish)
    c.chat_worker.run_once()
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert result["status"] == "stale" and result["reply"] is None
    assert len(c.calls) == 1 and cost_rows(c)[0]["state"] == "estimated"


def test_stale_preflight_is_not_replayed_as_current(close_case):
    from app.subledger.models import BankAdjustment

    c = close_case
    c.sub.add_bank_adjustment(
        c.manager,
        BankAdjustment(
            book_id=c.book["id"],
            bank_account_id=c.bank_account["id"],
            period="2026-10",
            kind="error",
            amount="1.00",
            explanation="虚构新增差异",
        ),
    )
    projection = project(c)
    assert next(row for row in projection["sources"] if row["kind"] == "close")["current"] is False
    assert "preflight_stale" in {row["code"] for row in projection["issues"]}


@pytest.mark.parametrize(
    "change", ["company_id", "book_id", "period", "evidence_ids", "can_deliver"]
)
def test_rehashed_wrong_projection_still_fails_scope_contract(execution_case, change):
    from app.assistant.delivery_context import validate_projection
    from app.accounting.service import fingerprint

    c = execution_case
    value = project(c)
    if change == "evidence_ids":
        value["issues"][0]["evidence_ids"] = ["book:" + c.other_book["id"]]
    else:
        value[change] = True if change == "can_deliver" else "foreign"
    value["fingerprint"] = fingerprint(
        {key: item for key, item in value.items() if key != "fingerprint"}
    )
    with pytest.raises(ValueError):
        validate_projection(
            value,
            c.company["id"],
            [{"book_id": c.book["id"], "period": "2026-10"}],
            ["book:" + c.book["id"]],
        )


def test_bounded_summary_exposes_omissions_instead_of_claiming_complete():
    from app.assistant.delivery_context import render_delivery
    from app.accounting.service import fingerprint

    value = {
        "schema": "assistant-delivery-v1",
        "company_id": "company-a",
        "book_id": "book-a",
        "period": "2026-10",
        "coverage": "selected_book",
        "can_deliver": None,
        "sources": [],
        "issues": [
            {
                "code": str(i),
                "reason": "待核验" * 80,
                "owner_role": "企业财务",
                "next_action": "核验原件" * 80,
                "evidence_ids": ["book:book-a"],
            }
            for i in range(80)
        ],
    }
    value["fingerprint"] = fingerprint(value)
    pack = SimpleNamespace(
        scope={"company_id": "company-a", "books": [{"book_id": "book-a", "period": "2026-10"}]},
        facts=[
            {"key": "delivery_task", "value": value},
            {"key": "citation_ids", "value": ["book:book-a"]},
        ],
    )
    answer, refs = render_delivery(pack)
    assert len(answer) <= 7000 and "未展示" in answer
    assert refs == ["book:book-a"]


@pytest.mark.parametrize("case", ["truncated", "multiple", "null", "missing", "invalid", "bool"])
def test_incomplete_ocr_never_produces_a_precise_difference(execution_case, case):
    from app.assistant.delivery_context import project_delivery

    c = execution_case
    actor = c.actors["finance"]
    event = create_event(c, actor="finance", amount="0.30")
    c.provider.document.update(gross_amount="0.40", net_amount="0.40")
    extract(c, event, actor="finance")
    c.chat_service.intelligence = c.intelligence
    conversation = c.chat_service.create(
        actor,
        ConversationCreate(
            company_id=c.company["id"],
            book_ids=[c.book["id"]],
            event_ids=[event["id"]],
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]
    with c.db.transaction(actor=actor) as conn:
        stored = c.chat_service._conversation(conn, actor, conversation["id"])
        snapshot = c.chat_service.snapshot_in_transaction(conn, actor, stored)
        current = snapshot["events"][0]
        assert len(current["documents"]) == 1
        if case == "truncated":
            current["documents_truncated"] = True
        elif case == "multiple":
            current["documents"].append(copy.deepcopy(current["documents"][0]))
        elif case == "missing":
            current["documents"][0].pop("gross_amount")
        else:
            current["documents"][0]["gross_amount"] = {
                "null": None,
                "invalid": "NaN",
                "bool": True,
            }[case]
        projection = project_delivery(c.db, conn, actor, snapshot)
    issue = next(row for row in projection["issues"] if row["code"] == "amount_conflict")
    assert "差额" not in issue["reason"]
    assert "核对原件" in issue["next_action"] and issue["evidence_ids"]
