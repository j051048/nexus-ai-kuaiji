"""PLT08-C：高频财务有界动作卡（高风险复核清单、缺件催办草稿）。

两张卡都走既有护栏：不可变预览 + 64 位指纹 + 显式确认 + 幂等键 + 30 分钟过期，
并且只产出清单/草稿文本，不改单据状态、不对外发送、不授予任何权限。
"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ImportRequest, SourceRow
from app.assistant.actions import ActionConfirm, ActionPrepare, AssistantActionService
from app.assistant.models import ConversationCreate
from app.assistant.service import AssistantService
from app.jobs.service import QueueService
from test_intelligence_workflow import ai_case  # noqa: F401
from test_queue_workflow import TestClock


@pytest.fixture
def cards(ai_case):
    case = ai_case
    case.clock = TestClock()
    case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
    case.assistant = AssistantService(case.db, case.ledger, case.business)
    case.actions = AssistantActionService(case.db, case.assistant, case.queue, case.ledger)
    return case


def _conversation(case, books):
    return case.assistant.create(
        case.actors["accountant"],
        ConversationCreate(
            company_id=case.company["id"],
            book_ids=list(books),
            event_ids=[],
            title="合成财务待办对话",
            idempotency_key=uuid4().hex,
        ),
    )["conversation"]


def _prepare(case, conv, kind):
    return case.actions.prepare(
        case.actors["accountant"],
        conv["id"],
        ActionPrepare(
            kind=kind, reason="用户核对当前明确范围内的待办", idempotency_key=uuid4().hex
        ),
    )


def _confirm(case, conv, plan, key=None, fingerprint=None):
    return case.actions.confirm(
        case.actors["accountant"],
        conv["id"],
        plan["id"],
        ActionConfirm(
            fingerprint=fingerprint or plan["fingerprint"],
            confirmed=True,
            idempotency_key=key or uuid4().hex,
        ),
    )


def _drafts(case):
    with case.db.transaction() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,status,version FROM drafts WHERE book_id=?", (case.book["id"],)
            )
        ]


def _import_bank(case, number=1, evidence="原始银行回单已保留"):
    case.ledger.import_sources(
        case.actors["accountant"],
        case.book["id"],
        ImportRequest(
            source_name="合成银行流水",
            rows=[
                SourceRow(
                    external_id="synthetic-" + str(index) + "-" + uuid4().hex,
                    date="2026-10-03",
                    description="办公耗材付款" + str(index),
                    counterparty="合成供应商",
                    amount="0.30",
                    direction="expense",
                    evidence_ref=evidence,
                )
                for index in range(number)
            ],
        ),
    )


def _make_drafts(case):
    _import_bank(case, number=1)
    conv = _conversation(case, [case.book["id"]])
    plan = _prepare(case, conv, "bank_drafts")
    assert plan["count"] == 1
    _confirm(case, conv, plan)
    return conv


def test_review_queue_card_lists_high_risk_drafts_without_mutation(cards):
    case = cards
    for source in _drafts(case):
        assert source["status"] == "draft"
    _make_drafts(case)
    before = _drafts(case)
    conv = _conversation(case, [case.book["id"]])
    plan = _prepare(case, conv, "review_queue_pull")
    assert plan["state"] == "ready"
    assert plan["count"] == len(before)
    assert plan["requires_confirmation"] is True
    assert len(plan["fingerprint"]) == 64
    titles = [item["title"] for item in plan["items"]]
    assert all(title.startswith("高风险草稿：") for title in titles)
    # 预览不改任何草稿：状态与版本保持原样。
    assert _drafts(case) == before
    receipt = _confirm(case, conv, plan)
    assert receipt["count"] == len(before)
    assert receipt["created"] == 0
    # 确认只落回执，不审批、不改单据状态。
    assert _drafts(case) == before


def test_review_queue_card_requires_scope_and_exact_fingerprint(cards):
    case = cards
    _make_drafts(case)
    conv = _conversation(case, [case.book["id"]])
    plan = _prepare(case, conv, "review_queue_pull")
    with pytest.raises(AccountingError) as failure:
        _confirm(case, conv, plan, fingerprint="0" * 64)
    assert "指纹" in str(failure.value)
    # 没有范围的对话允许存在，但卡片必须明确拒绝并说明缺少范围。
    empty = _prepare(case, _conversation(case, []), "review_queue_pull")
    assert empty["state"] == "empty"
    assert {row["code"] for row in empty["blocked"]} >= {"scope_required"}


def test_missing_evidence_card_generates_drafts_only(cards):
    case = cards
    # 一张没有原始凭据的流水：应当出现在催办草稿里。
    _import_bank(case, number=1, evidence="")
    conv = _conversation(case, [case.book["id"]])
    plan = _prepare(case, conv, "missing_evidence_draft")
    assert plan["state"] == "ready"
    assert plan["count"] == 1
    assert plan["items"][0]["title"].startswith("缺件催办：办公耗材付款0")
    assert "缺原始凭据" in plan["items"][0]["title"]
    key = uuid4().hex
    receipt = _confirm(case, conv, plan, key=key)
    assert receipt["created"] == 0 and receipt["count"] == 1
    # 幂等：同一请求键重复提交只返回原回执，不会重复落单。
    again = _confirm(case, conv, plan, key=key)
    assert again["count"] == 1 and again["id"] == receipt["id"]


def test_cards_never_touch_business_tables(cards):
    case = cards
    _make_drafts(case)

    def counts():
        with case.db.transaction() as conn:
            return {
                table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("drafts", "sources", "vouchers", "exports", "handoff_tasks")
            }

    before = counts()
    for kind in ("review_queue_pull", "missing_evidence_draft"):
        conv = _conversation(case, [case.book["id"]])
        plan = _prepare(case, conv, kind)
        if plan["count"]:
            _confirm(case, conv, plan)
    assert counts() == before
