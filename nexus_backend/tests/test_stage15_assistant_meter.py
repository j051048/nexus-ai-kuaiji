"""Company budget integration with real persistent turns and synthetic responses."""
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant.meter import AssistantMeter
from app.assistant.models import AssistantReply, ConversationCreate, TurnCreate
from app.assistant.service import AssistantService
from app.assistant.worker import AssistantWorker
from app.intelligence.models import ProviderResult, ProviderUsage
from app.intelligence.provider import AISettings, ProviderError
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from test_model_cost_workflow import ai_case, cost_case, policy, claimed_context, before_send, cost_rows


class SyntheticChat:
    available = True
    def __init__(self):
        self.settings = AISettings(base_url="https://synthetic.example.test/v1", api_key="synthetic-only", chat_model="synthetic-chat")
        self.calls, self.failure, self.hook = 0, None, None
    def configuration_fingerprint(self):
        return "a" * 64
    def reply(self, context, question, history):
        self.calls += 1
        if self.hook:
            self.hook()
        if self.failure:
            raise self.failure
        return ProviderResult(AssistantReply(answer="合成测试回复，仅供验收。", citation_ids=[], proposals=[]),
                              "openai_compatible", "synthetic-chat", "accounting-assistant-v1",
                              ProviderUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7))


@pytest.fixture
def chat_case(cost_case):
    case = cost_case
    case.chat_provider = SyntheticChat()
    case.chat_service = AssistantService(case.db, case.ledger, case.business, provider=case.chat_provider)
    # Use the same company budget and pricing snapshot as OCR, with chat's own tariff.
    case.meter.pricing = Pricing(ocr=case.pricing.ocr, proposal=case.pricing.proposal, chat=Rate("2.000000", "4.000000"))
    case.chat_worker = AssistantWorker(case.chat_service, case.chat_provider, AssistantMeter(case.db, case.meter))
    return case


def question(case, actor=None):
    actor = actor or case.manager
    conversation = case.chat_service.create(actor, ConversationCreate(company_id=case.company["id"], title="合成费用验收",
                                                                     idempotency_key=uuid4().hex))["conversation"]
    turn = case.chat_service.enqueue(actor, conversation["id"], TurnCreate(question="如何准备当前范围的核对资料？", expected_scope_version=1,
                                                                         idempotency_key=uuid4().hex))["turn"]
    return actor, conversation, turn


def test_chat_is_metered_in_existing_company_budget_without_ledger_mutation(chat_case):
    c = chat_case
    policy(c, chat_reserve="0.10")
    actor, conversation, turn = question(c)
    c.chat_worker.run_once()
    assert c.chat_service.turn(actor, conversation["id"], turn["id"])["turn"]["status"] == "succeeded"
    row = cost_rows(c)[0]
    assert row["kind"] == "chat" and row["chat_turn_id"] == turn["id"]
    assert row["run_id"] is None and row["intake_run_id"] is None and row["event_id"] is None
    assert row["state"] == "estimated" and row["estimated_micro"] == 22 and row["reserved_micro"] == 100000
    overview = c.operations.overview(c.manager, c.company["id"])
    assert overview["usage"]["requests"] == 1 and overview["usage"]["exposure"] == "0.000022"
    assert overview["costs"][0]["kind"] == "chat"
    with c.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0


def test_zero_chat_reserve_and_existing_ocr_exposure_block_network(chat_case):
    c = chat_case
    policy(c, chat_reserve="0.00")
    actor, conv, turn = question(c)
    c.chat_worker.run_once()
    assert c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]["error_code"] == "policy_invalid"
    assert c.chat_provider.calls == 0 and not cost_rows(c)
    policy(c, monthly_limit="0.20", chat_reserve="0.15")
    reservation, _ = claimed_context(c)
    before_send(c, reservation)
    actor, conv, turn = question(c)
    c.chat_worker.run_once()
    assert c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]["error_code"] == "quota_exhausted"
    assert c.chat_provider.calls == 0 and len(cost_rows(c)) == 1


def test_unknown_sent_cost_is_held_and_restart_does_not_send_again(chat_case):
    c = chat_case
    policy(c, chat_reserve="0.10")
    c.chat_provider.failure = ProviderError("timeout", "合成供应商超时")
    actor, conv, turn = question(c)
    c.chat_worker.run_once()
    assert c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]["status"] == "interrupted"
    assert cost_rows(c)[0]["state"] == "uncertain"
    restarted = AssistantWorker(c.chat_service, c.chat_provider, AssistantMeter(c.db, c.meter))
    assert restarted.run_once() is None
    assert c.chat_provider.calls == 1 and len(cost_rows(c)) == 1
    assert c.operations.overview(c.manager, c.company["id"])["usage"]["exposure"] == "0.100000"


def test_cost_facts_survive_revocation_but_answer_is_not_published(chat_case):
    c = chat_case
    policy(c, chat_reserve="0.10")
    actor, conv, turn = question(c, c.actors["employee"])
    def revoke():
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (c.company["id"], actor.user_id))
    c.chat_provider.hook = revoke
    c.chat_worker.run_once()
    assert cost_rows(c)[0]["state"] == "estimated"
    with c.db.transaction() as conn:
        assert conn.execute("SELECT status FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()[0] == "stale"
        assert conn.execute("SELECT count(*) FROM assistant_messages WHERE turn_id=? AND role='assistant'", (turn["id"],)).fetchone()[0] == 0
    with pytest.raises(AccountingError):
        c.chat_service.detail(actor, conv["id"])


def test_legacy_policy_clients_preserve_new_chat_reserve(chat_case):
    c = chat_case
    assert policy(c, chat_reserve="0.15")["chat_reserve"] == "0.15"
    # Old clients omit chat_reserve; a normal budget edit must not disable chat.
    assert policy(c, monthly_limit="2.00")["chat_reserve"] == "0.15"
