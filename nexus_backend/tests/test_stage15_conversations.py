"""Real SQLite transactions and synthetic provider; no supplier connections."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import httpx
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.accounting.models import DraftUpdate
from app.accounting.service import fingerprint, now
from app.assistant.models import AssistantReply, ConversationCreate, TurnCreate, TurnCancel
from app.assistant.service import AssistantService, encode
from app.assistant.worker import AssistantWorker
from app.assistant.provider import AssistantProvider
from app.intelligence.models import ProviderResult, ProviderUsage
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError
from app.operations.meter import BudgetError
from test_business import business, create
from test_stage12_workspace import bank
from test_intelligence_workflow import ai_case, create_event, extract


class SyntheticChatProvider:
    available = True

    def __init__(self):
        self.settings = SimpleNamespace(chat_model="synthetic-chat")
        self.calls, self.contexts = 0, []
        self.hook = None; self.error = None; self.invalid_citation = False; self.config_revision = 1

    def configuration_fingerprint(self):
        return fingerprint({"synthetic": self.config_revision})

    def reply(self, context, question, history):
        self.calls += 1; self.contexts.append((context, question, history))
        if self.hook:
            self.hook()
        if self.error:
            raise self.error
        citations = ["source:unauthorized"] if self.invalid_citation else [context["evidence"][0]["id"]] if context["evidence"] else []
        reply = AssistantReply(answer="合成测试回答：请核对保存的资料。", citation_ids=citations, proposals=[])
        return ProviderResult(reply, "openai_compatible", self.settings.chat_model, "synthetic-test-v1", ProviderUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5))


class SyntheticMeter:
    def __init__(self):
        self.reserves, self.finishes = 0, []
        self.failure = None

    def context(self, actor, turn):
        meter = self
        class Context:
            def before_send(self, conn):
                if meter.failure:
                    raise meter.failure
                meter.reserves += 1
            def finish(self, **kwargs):
                meter.finishes.append(kwargs)
        return Context()


def assistant_case(c, actor=None):
    provider, meter = SyntheticChatProvider(), SyntheticMeter()
    service = AssistantService(c.db, c.ledger, c.service, provider=provider)
    actor = actor or c.actors["accountant"]
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("INSERT INTO ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) VALUES(?,?,1,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (c.manager.firm_id, c.company["id"], c.manager.user_id, now()))
    return SimpleNamespace(source=c, db=c.db, provider=provider, meter=meter, service=service, actor=actor, worker=AssistantWorker(service, provider, meter), company=c.company["id"], book=c.book["id"])


def conversation(c, **changes):
    return c.service.create(c.actor, ConversationCreate(company_id=c.company, book_ids=[c.book], idempotency_key=uuid4().hex, **changes))["conversation"]


def question(c, thread, text="解释当前资料", key=None):
    return c.service.enqueue(c.actor, thread["id"], TurnCreate(question=text, expected_scope_version=1, idempotency_key=key or uuid4().hex))["turn"]


def test_private_conversation_scope_and_idempotency(business):
    c = assistant_case(business)
    command = ConversationCreate(company_id=c.company, book_ids=[c.book], idempotency_key=uuid4().hex)
    first = c.service.create(c.actor, command)["conversation"]
    assert c.service.create(c.actor, command)["conversation"]["id"] == first["id"]
    with pytest.raises(AccountingError) as other:
        c.service.detail(business.manager, first["id"])
    assert other.value.status == 404
    assert c.service.list(c.actor, c.company)["pagination"]["total"] == 1
    assert c.service.list(business.manager, c.company)["pagination"]["total"] == 0
    changed = command.model_copy(update={"title": "不同内容"})
    with pytest.raises(AccountingError, match="不同范围"):
        c.service.create(c.actor, changed)


def test_one_revoked_reference_hides_entire_history_before_count(business):
    c = assistant_case(business); thread = conversation(c)
    question(c, thread); c.worker.run_once()
    with c.db.transaction(write=True, actor=business.manager) as conn:
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book, c.actor.user_id))
    # The owner no longer has company access either: collection is denied.
    for call in (lambda: c.service.detail(c.actor, thread["id"]), lambda: c.service.messages(c.actor, thread["id"]), lambda: c.service.list(c.actor, c.company)):
        with pytest.raises(AccountingError) as error:
            call()
        assert error.value.status == 404


def test_employee_event_scope_never_grants_book_or_peer_data(business):
    own, peer = create(business), create(business, actor="peer")
    c = assistant_case(business, business.actors["employee"])
    thread = c.service.create(c.actor, ConversationCreate(company_id=c.company, event_ids=[own["id"]], idempotency_key=uuid4().hex))["conversation"]
    detail = c.service.detail(c.actor, thread["id"])
    assert detail["context"]["books"] == [] and detail["context"]["events"][0]["amount"] == "0.30"
    assert peer["id"] not in json.dumps(detail)
    for fields in ({"event_ids": [peer["id"]]}, {"book_ids": [c.book]}, {"event_ids": [own["id"], peer["id"]]}):
        with pytest.raises(AccountingError):
            c.service.create(c.actor, ConversationCreate(company_id=c.company, idempotency_key=uuid4().hex, **fields))
    choices = c.service.choices(c.actor, c.company, limit=1)
    assert choices["books"] == [] and choices["pagination"]["total"] == 1


def test_no_refs_sends_no_book_or_event_data(business):
    c = assistant_case(business)
    bank(business)
    thread = c.service.create(c.actor, ConversationCreate(company_id=c.company, idempotency_key=uuid4().hex))["conversation"]
    question(c, thread); c.worker.run_once()
    context, _, _ = c.provider.contexts[0]
    assert context["books"] == context["events"] == context["evidence"] == [] and context["draft"] is None


def test_turn_idempotency_exact_history_and_no_accounting_mutation(business):
    c = assistant_case(business); draft = bank(business); thread = conversation(c, draft_id=draft["id"])
    key = uuid4().hex; first = question(c, thread, key=key)
    assert question(c, thread, key=key)["id"] == first["id"]
    with pytest.raises(AccountingError):
        question(c, thread, "不同问题", key)
    with pytest.raises(AccountingError, match="上一条"):
        question(c, thread, "另一个问题")
    assert c.worker.run_once() == first["id"]
    finished = c.service.turn(c.actor, thread["id"], first["id"])["turn"]
    assert finished["status"] == "succeeded" and finished["mode"] == "model" and finished["reply"]["citation_ids"]
    text = json.dumps(finished)
    assert "lease_token" not in text and "snapshot_json" not in text
    assert c.provider.contexts[0][2] == []
    second = question(c, thread, "继续解释")
    c.worker.run_once()
    assert [r["role"] for r in c.provider.contexts[1][2]] == ["user", "assistant"]
    messages = c.service.messages(c.actor, thread["id"], limit=2)
    assert messages["pagination"]["total"] == 4 and messages["pagination"]["has_more"]
    assert [m["role"] for m in messages["messages"]] == ["user", "assistant"]
    assert business.ledger.get_book(c.actor, c.book)["drafts"][0]["status"] == "draft"
    assert c.provider.calls == 2 and c.meter.reserves == 2


@pytest.mark.parametrize("code", ["timeout", "network", "response_unknown"])
def test_sent_failure_and_restart_never_replays(business, code):
    c = assistant_case(business); thread = conversation(c); turn = question(c, thread)
    c.provider.error = ProviderError(code, "合成网络结果未知")
    c.worker.run_once(); c.worker.run_once()
    assert c.service.turn(c.actor, thread["id"], turn["id"])["turn"]["status"] == "interrupted"
    assert c.provider.calls == 1 and c.service.messages(c.actor, thread["id"])["pagination"]["total"] == 1


@pytest.mark.parametrize("sent", [False, True])
def test_lease_recovery_before_and_after_send(business, sent):
    c = assistant_case(business); thread = conversation(c); turn = question(c, thread)
    claimed = c.worker.claim()
    with c.db.transaction(write=True) as conn:
        conn.execute("UPDATE assistant_turns SET lease_until=?,sent_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", now() if sent else None, turn["id"]))
    c.worker.run_once()
    result = c.service.turn(c.actor, thread["id"], turn["id"])["turn"]
    assert result["status"] == ("interrupted" if sent else "succeeded")
    assert c.provider.calls == (0 if sent else 1)
    assert not c.worker.finish(claimed, "failed", code="late_response")


def test_citation_outside_snapshot_is_rejected(business):
    c = assistant_case(business); thread = conversation(c); turn = question(c, thread)
    c.provider.invalid_citation = True; c.worker.run_once()
    result = c.service.turn(c.actor, thread["id"], turn["id"])["turn"]
    assert result["status"] == "failed" and result["error_code"] == "citation_invalid" and result["reply"] is None
    assert c.meter.finishes[-1]["result"].usage.total_tokens == 5


def test_fact_draft_version_change_prevents_send(business):
    c = assistant_case(business); draft = bank(business); thread = conversation(c, draft_id=draft["id"])
    turn = question(c, thread)
    business.ledger.update_draft(c.actor, c.book, draft["id"], DraftUpdate(expected_version=draft["version"], description="会计修订", lines=draft["lines"]))
    c.worker.run_once()
    assert c.service.turn(c.actor, thread["id"], turn["id"])["turn"]["status"] == "stale" and c.provider.calls == 0


@pytest.mark.parametrize("change", ["revoke", "facts", "configuration", "opt_out"])
def test_changes_during_reply_prevent_publication(business, change):
    c = assistant_case(business); draft = bank(business); thread = conversation(c, draft_id=draft["id"]); turn = question(c, thread)
    def hook():
        if change == "configuration":
            c.provider.config_revision += 1
        elif change == "facts":
            business.ledger.update_draft(c.actor, c.book, draft["id"], DraftUpdate(expected_version=draft["version"], description="回复时修订", lines=draft["lines"]))
        else:
            with c.db.transaction(write=True, actor=business.manager) as conn:
                if change == "revoke":
                    conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book, c.actor.user_id))
                else:
                    conn.execute("UPDATE ai_company_settings SET enabled=0 WHERE firm_id=? AND company_id=?", (c.actor.firm_id, c.company))
    c.provider.hook = hook; c.worker.run_once()
    with c.db.transaction() as conn:
        row = conn.execute("SELECT status,reply_json FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()
        assert row["status"] == "stale" and row["reply_json"] is None
        assert conn.execute("SELECT COUNT(*) FROM assistant_messages WHERE turn_id=? AND role='assistant'", (turn["id"],)).fetchone()[0] == 0
    assert c.meter.finishes[-1]["attempted"]


@pytest.mark.parametrize("code", ["quota_exhausted", "policy_invalid", "pricing_missing"])
def test_quota_refusal_has_fixed_actionable_code_and_no_send(business, code):
    c = assistant_case(business); thread = conversation(c); turn = question(c, thread)
    c.meter.failure = BudgetError(code); c.worker.run_once()
    result = c.service.turn(c.actor, thread["id"], turn["id"])["turn"]
    assert result["status"] == "failed" and result["error_code"] == code and result["sent_at"] is None and c.provider.calls == 0


def test_model_unconfigured_and_company_opt_out_are_explicit(business):
    c = assistant_case(business); thread = conversation(c)
    c.provider.available = False
    assert not c.service.detail(c.actor, thread["id"])["model_available"]
    with pytest.raises(AccountingError) as unavailable:
        question(c, thread)
    assert unavailable.value.status == 409
    c.provider.available = True
    with c.db.transaction(write=True, actor=business.manager) as conn:
        conn.execute("UPDATE ai_company_settings SET enabled=0 WHERE firm_id=? AND company_id=?", (c.actor.firm_id, c.company))
    with pytest.raises(AccountingError, match="管理员已暂停"):
        question(c, thread)
    assert c.provider.calls == 0


def test_cancel_fences_claim_and_preserves_original_question(business):
    c = assistant_case(business); thread = conversation(c); turn = question(c, thread); claimed = c.worker.claim()
    cancelled = c.service.cancel(c.actor, thread["id"], turn["id"], TurnCancel(reason="停止本次问题"))["turn"]
    assert cancelled["status"] == "cancelled"
    assert not c.worker.finish(claimed, "failed") and c.provider.calls == 0
    assert c.service.messages(c.actor, thread["id"])["messages"][0]["content"]["question"] == "解释当前资料"


@pytest.mark.parametrize("body", [{"book_ids": ["x", "x"]}, {"book_ids": [str(i) for i in range(13)]}, {"event_ids": [str(i) for i in range(21)]}, {"draft_id": "x"}, {"book_ids": ["x", "y"], "draft_id": "z"}])
def test_scope_dto_bounds(body):
    with pytest.raises(ValidationError):
        ConversationCreate(company_id="company", idempotency_key="request", **body)


def test_current_ocr_summary_has_tax_amount_but_no_raw_pages_or_tax_identity(ai_case):
    c = ai_case; event = create_event(c); run = extract(c, event)
    provider, meter = SyntheticChatProvider(), SyntheticMeter()
    service = AssistantService(c.db, c.ledger, c.business, provider=provider, intelligence=c.intelligence)
    actor = c.actors["employee"]
    thread = service.create(actor, ConversationCreate(company_id=c.company["id"], event_ids=[event["id"]], idempotency_key=uuid4().hex))["conversation"]
    context = service.detail(actor, thread["id"])["context"]
    document = context["events"][0]["documents"][0]
    assert document["gross_amount"] == "0.30" and document["tax_amount"] == "0.00" and document["extraction_id"] == run["id"]
    assert "pages" not in document and "raw_text" not in json.dumps(context) and "seller_tax_id" not in json.dumps(context)
    assert context["books"] == [] and "原件" in context["evidence"][-1]["detail"]
    turn = service.enqueue(actor, thread["id"], TurnCreate(question="已保存的发票税额是多少？", expected_scope_version=1, idempotency_key=uuid4().hex))["turn"]
    AssistantWorker(service, provider, meter).run_once()
    assert service.turn(actor, thread["id"], turn["id"])["turn"]["status"] == "succeeded"
    assert provider.contexts[0][0]["events"][0]["documents"] == context["events"][0]["documents"]


def test_current_ocr_change_during_reply_invalidates_old_answer(ai_case):
    c = ai_case; event = create_event(c); extract(c, event)
    provider, meter = SyntheticChatProvider(), SyntheticMeter()
    service = AssistantService(c.db, c.ledger, c.business, provider=provider, intelligence=c.intelligence)
    actor = c.actors["employee"]
    thread = service.create(actor, ConversationCreate(company_id=c.company["id"], event_ids=[event["id"]], idempotency_key=uuid4().hex))["conversation"]
    turn = service.enqueue(actor, thread["id"], TurnCreate(question="核对当前票据", expected_scope_version=1, idempotency_key=uuid4().hex))["turn"]
    provider.hook = lambda: extract(c, event, key=uuid4().hex)
    AssistantWorker(service, provider, meter).run_once()
    assert service.turn(actor, thread["id"], turn["id"])["turn"]["status"] == "stale"
    assert service.messages(actor, thread["id"])["pagination"]["total"] == 1


@pytest.mark.parametrize("failure_type", [httpx.ConnectError, httpx.ReadError])
def test_actual_gateway_network_error_is_unknown_and_not_replayed(business, failure_type):
    c = assistant_case(business)
    calls = []
    def handler(request):
        calls.append(request)
        raise failure_type("private synthetic transport detail", request=request)
    provider = AssistantProvider(OpenAICompatibleProvider(AISettings(base_url="https://synthetic.example.invalid/v1", api_key="synthetic-test-key", chat_model="synthetic-chat"), httpx.Client(transport=httpx.MockTransport(handler))))
    c.provider = provider; c.service.provider = provider; c.worker.provider = provider
    thread = conversation(c); turn = question(c, thread)
    c.worker.run_once(); c.worker.run_once()
    result = c.service.turn(c.actor, thread["id"], turn["id"])["turn"]
    assert result["status"] == "interrupted" and result["error_code"] == "network_error"
    assert len(calls) == 1 and "private synthetic" not in result["error_detail"]
