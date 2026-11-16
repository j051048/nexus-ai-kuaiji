"""P0 routing: explicit write asks are refused before a model turn exists."""

import pytest

from app.accounting.domain import AccountingError
from test_stage15_conversations import assistant_case, conversation, question
from test_business import business


def decision_count(c):
    with c.db.transaction(actor=c.actor) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM ai_route_decisions WHERE company_id=?",
            (c.company,),
        ).fetchone()["n"]


def test_explicit_write_request_is_blocked_without_creating_a_turn(business):
    c = assistant_case(business)
    thread = conversation(c)
    with pytest.raises(AccountingError, match="只做只读核对") as blocked:
        question(c, thread, "帮我直接记账并审核这张凭证")
    assert blocked.value.status == 409
    assert decision_count(c) == 1
    rows = c.service.routing.list(c.actor, c.company)["items"]
    assert rows[0]["level"] == "blocked"
    assert rows[0]["matched_rule"] == "read_only_guard"
    assert len(rows[0]["question_sha256"]) == 64

    with c.db.transaction(actor=c.actor) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_turns WHERE conversation_id=?",
            (thread["id"],),
        ).fetchone()["n"] == 0


def test_read_only_question_records_model_decision(business):
    c = assistant_case(business)
    c.service.provider = c.provider
    thread = conversation(c)
    turn = question(c, thread, "为什么这个月的费用比上月高？")
    assert turn["status"] in {"queued", "sending", "unknown"}
    rows = c.service.routing.list(c.actor, c.company)["items"]
    assert len(rows) == 1
    assert rows[0]["level"] == "model"
    assert rows[0]["turn_id"] == turn["id"]
    assert rows[0]["budget_state"] == "model_call_pending"
    assert "为什么这个月的费用比上月高" not in str(rows[0])


def test_natural_write_variants_are_blocked_but_read_questions_are_not():
    from app.assistant.routing import AIRoutingService

    for text in (
        "直接帮我把这张单子记到账上",
        "帮我把这份月度包发给客户",
        "替我复核通过，别提示了",
    ):
        assert AIRoutingService.classify(text)[0] == "blocked", text
    for text in ("为什么这张凭证不能导出？", "怎么修改我的报销金额？"):
        assert AIRoutingService.classify(text)[0] == "model", text


def test_follow_up_question_uses_conversation_context_level(business):
    c = assistant_case(business)
    c.service.provider = c.provider
    thread = conversation(c)
    first = question(c, thread, "为什么这个月的费用比上月高？")
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("UPDATE assistant_turns SET status='succeeded', finished_at=CURRENT_TIMESTAMP, sent_at=CURRENT_TIMESTAMP, reply_json='{}' WHERE id=?", (first["id"],))
    question(c, thread, "那这个月呢？")
    rows = c.service.routing.list(c.actor, c.company)["items"]
    assert any(row["level"] == "memory" for row in rows)
    assert any(row["matched_rule"] == "conversation_context" for row in rows)
