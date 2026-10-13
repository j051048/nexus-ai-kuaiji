"""Actual restricted PostgreSQL/worker cost path with synthetic model usage."""
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant.meter import AssistantMeter
from app.intelligence.provider import ProviderError
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from app.operations.requests import PolicyRequest
from app.operations.service import OperationsService
from test_stage15_conversations_postgres import (
    ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres, chat_pg, make_thread, enqueue,
)
from test_model_operations_postgres import run, reserve, sent

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用PG管理员/API/实际worker连接")


@pytest.fixture
def billed_chat(chat_pg):
    c = chat_pg
    tariffs = Pricing(ocr=Rate("2.000000", "4.000000"), proposal=Rate("2.000000", "4.000000"), chat=Rate("2.000000", "4.000000"))
    c.worker.meter = AssistantMeter(c.worker.db, MeterService(c.worker.db, pricing=tariffs))
    c.ops = OperationsService(c.db, MeterService(c.db, pricing=tariffs))
    c.ops.policy(c.source.owner, c.company, PolicyRequest(expected_version=0, quota_enabled=True, monthly_limit="1.00",
                                                         ocr_reserve="0.10", proposal_reserve="0.10", chat_reserve="0.15"))
    return c


def cost(c, turn_id):
    with c.db.transaction(actor=c.source.owner) as conn:
        return dict(conn.execute("SELECT * FROM model_costs WHERE id=?", (turn_id,)).fetchone())


def test_native_chat_budget_real_worker_and_shared_usage(billed_chat):
    c = billed_chat; thread = make_thread(c); turn = enqueue(c, thread)
    assert c.worker.run_once() == turn["id"]
    assert c.service.turn(c.actor, thread["id"], turn["id"])["turn"]["status"] == "succeeded"
    row = cost(c, turn["id"])
    assert row["kind"] == "chat" and row["chat_turn_id"] == turn["id"] and row["run_id"] is None
    assert row["reserved_micro"] == 150000 and row["estimated_micro"] == 16 and row["state"] == "estimated"
    summary = c.ops.overview(c.source.owner, c.company)
    assert summary["usage"]["exposure"] == "0.000016" and summary["costs"][0]["kind"] == "chat"
    assert "question" not in summary["costs"][0]


def test_native_chat_and_ocr_cannot_exceed_shared_company_limit(billed_chat):
    c = billed_chat
    c.ops.policy(c.source.owner, c.company, PolicyRequest(expected_version=1, quota_enabled=True, monthly_limit="0.20",
                                                         ocr_reserve="0.10", proposal_reserve="0.10", chat_reserve="0.15"))
    previous = run(c.source); state, token = reserve(c.source, previous, amount=0)
    assert state == "reserved" and sent(c.source, previous, token)
    thread = make_thread(c); turn = enqueue(c, thread)
    c.worker.run_once()
    result = c.service.turn(c.actor, thread["id"], turn["id"])["turn"]
    assert result["error_code"] == "quota_exhausted" and c.provider.calls == 0
    with c.db.transaction(actor=c.source.owner) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs WHERE id=?", (turn["id"],)).fetchone()[0] == 0


def test_api_cannot_reserve_chat_using_forged_worker_token(billed_chat):
    c = billed_chat; thread = make_thread(c); turn = enqueue(c, thread); claimed = c.worker.claim()
    assert claimed and claimed["id"] == turn["id"]
    tariff = c.worker.meter.shared.pricing.chat.snapshot("openai_compatible", "synthetic-chat")
    from app.business.service import encode
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("SELECT set_config('app.assistant_token',?,true)", (claimed["lease_token"],))
        status = conn.execute("SELECT accounting.reserve_assistant_model_cost(?,?,?,?,?,?)",
                              (turn["id"], uuid4().hex, "openai_compatible", "synthetic-chat", encode(tariff), 0)).fetchone()[0]
        assert status == "authorization_changed"
    assert c.provider.calls == 0


def test_native_unknown_network_cost_is_held_without_replay(billed_chat):
    c = billed_chat; c.provider.error = ProviderError("network_error", "合成网络断连")
    thread = make_thread(c); turn = enqueue(c, thread)
    c.worker.run_once()
    assert c.service.turn(c.actor, thread["id"], turn["id"])["turn"]["status"] == "interrupted"
    assert cost(c, turn["id"])["state"] == "uncertain"
    assert c.worker.run_once() is None and c.provider.calls == 1


def test_native_late_usage_survives_revocation_without_publishing(billed_chat):
    c = billed_chat; thread = make_thread(c); turn = enqueue(c, thread)
    def revoke():
        with c.source.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (c.book, c.actor.user_id))
    c.provider.hook = revoke
    c.worker.run_once()
    assert cost(c, turn["id"])["state"] == "estimated"
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT status FROM accounting.assistant_turns WHERE id=%s", (turn["id"],)).fetchone()[0] == "stale"
        assert conn.execute("SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'", (turn["id"],)).fetchone()[0] == 0
    with pytest.raises(AccountingError):
        c.service.detail(c.actor, thread["id"])
