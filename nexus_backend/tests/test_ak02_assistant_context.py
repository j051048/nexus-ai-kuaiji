"""AK02 assistant uses authorized ContextPack; synthetic HTTP, never a real model."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.assistant.models import ConversationCreate
from app.assistant.provider import AssistantProvider
from app.assistant.worker import AssistantWorker
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError
from test_business import business, create
from test_stage15_conversations import assistant_case, question
from test_intelligence_workflow import ai_case, create_event, extract


def runtime(case, actor=None):
    c = assistant_case(case, actor or case.manager)
    c.requests = []

    def respond(request):
        c.requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "answer": "请会计复核当前资料。", "citation_ids": [], "proposals": [],
            })}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    settings = AISettings(base_url="https://supplier.example.test/v1", api_key="synthetic-key", chat_model="synthetic-chat")
    c.provider = AssistantProvider(OpenAICompatibleProvider(settings, httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)))
    c.service.provider = c.provider
    c.worker = AssistantWorker(c.service, c.provider, c.meter)
    return c


def thread(c, **scope):
    return c.service.create(c.actor, ConversationCreate(company_id=c.company, idempotency_key=uuid4().hex, **scope))["conversation"]


def run(c, conversation, text="解释当前资料"):
    turn = question(c, conversation, text)
    c.worker.run_once()
    return c.service.turn(c.actor, conversation["id"], turn["id"])["turn"]


def transmitted_pack(c):
    supplied = json.loads(c.requests[-1]["messages"][1]["content"])
    assert set(supplied) == {"context_pack"}, "runtime must consume pack, not duplicate raw snapshot/history"
    return supplied["context_pack"]


@pytest.mark.parametrize("book_count", [0, 2])
def test_runtime_compiles_explicit_company_scope_without_invented_single_book(business, book_count):
    c = runtime(business)
    ids = []
    if book_count:
        second = business.ledger.create_book(business.manager, BookCreate(name="同企业下一期", period="2026-11", company_id=c.company))
        ids = [c.book, second["id"]]
    conv = thread(c, book_ids=ids)
    assert run(c, conv)["status"] == "succeeded"
    pack = transmitted_pack(c)
    assert pack["task"] == "assistant_reply"
    assert pack["scope"]["firm_id"] == c.actor.firm_id
    assert pack["scope"]["company_id"] == c.company
    assert pack["scope"]["book_id"] is None and pack["scope"]["period"] is None
    assert {row["book_id"] for row in pack["scope"]["books"]} == set(ids)
    assert business.other["id"] not in json.dumps(pack)
    assert business.other_book["id"] not in json.dumps(pack)
    assert any(rule["required"] for rule in pack["rules"])
    assert c.meter.reserves == 1 and c.meter.finishes[-1]["result"].usage.total_tokens == 5


def test_employee_current_fact_is_saved_not_approved_and_old_answer_not_reused(business):
    own = create(business, amount="120.00")
    peer = create(business, actor="peer", amount="999.00")
    c = runtime(business, business.actors["employee"])
    conv = thread(c, event_ids=[own["id"]])
    assert run(c, conv)["status"] == "succeeded"
    assert run(c, conv, "请按当前资料再说明")["status"] == "succeeded"
    pack = transmitted_pack(c)
    fact = next(row for row in pack["facts"] if row["key"] == "event:" + own["id"])
    assert fact["source"] == "saved_fact" and fact["value"]["amount"] == "120.00"
    assert peer["id"] not in json.dumps(pack)
    assert pack["history"] and all(row["stale"] and row["answer"] == "" for row in pack["history"])
    assert "请会计复核当前资料。" not in json.dumps(pack)
    assert all("page" not in row for row in pack["evidence"]), "summary cannot invent OCR page evidence"


def test_cross_company_reference_and_revocation_never_send(business):
    c = runtime(business, business.actors["accountant"])
    with pytest.raises(AccountingError):
        thread(c, book_ids=[business.other_book["id"]])
    conv = thread(c, book_ids=[c.book])
    turn = question(c, conv)
    with c.db.transaction(write=True, actor=business.manager) as conn:
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book, c.actor.user_id))
    c.worker.run_once()
    with c.db.transaction() as conn:
        row = conn.execute("SELECT status,sent_at FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()
    assert row["status"] == "stale" and row["sent_at"] is None
    assert c.requests == [] and c.meter.reserves == 0


def test_pack_tamper_or_wrong_current_snapshot_is_rejected_before_transport(business):
    c = runtime(business)
    conv = thread(c, book_ids=[c.book])
    assert run(c, conv)["status"] == "succeeded"
    pack = transmitted_pack(c)
    snapshot = c.service.detail(c.actor, conv["id"])["context"]
    c.requests.clear()
    changed = copy.deepcopy(pack)
    changed["scope"]["company_id"] = business.other["id"]
    for candidate, current in [(changed, snapshot), (pack, {**snapshot, "company_id": business.other["id"]}), (pack, {**snapshot, "input_fingerprint": "0" * 64})]:
        with pytest.raises(ProviderError) as caught:
            c.provider.reply(current, "解释当前资料", [], context_pack=candidate)
        assert caught.value.llm_attempted is False
    assert c.requests == []


def test_context_compile_failure_precedes_reserve_and_sent_marker(business, monkeypatch):
    from app.assistant import context_pack as adapter
    from app.intelligence.context_pack import BudgetExceeded

    c = runtime(business)
    conv = thread(c, book_ids=[c.book])
    monkeypatch.setattr(adapter, "compile_context_pack", lambda **kwargs: (_ for _ in ()).throw(BudgetExceeded("context_budget_exceeded", "synthetic budget")))
    result = run(c, conv)
    assert result["status"] == "failed"
    assert result["error_code"] == "context_too_large"
    assert c.requests == [] and c.meter.reserves == 0
    with c.db.transaction() as conn:
        row = conn.execute("SELECT sent_at FROM assistant_turns WHERE id=?", (result["id"],)).fetchone()
    assert row["sent_at"] is None
    assert c.meter.finishes[-1]["error"].llm_attempted is False


def minimal_snapshot():
    return {
        "company_id": "company-a", "company_name": "合成企业", "input_fingerprint": "a" * 64,
        "books": [], "events": [], "draft": None, "evidence": [], "notice": "合成资料",
    }


def compile_pack(snapshot, **changes):
    from app.assistant.context_pack import build_assistant_context_pack
    from app.assistant.provider import PROMPT_VERSION
    from app.intelligence.provider import ExecutionLimits

    arguments = dict(firm_id="firm-a", company_id="company-a", limits=ExecutionLimits(), policy_version=1, prompt_version=PROMPT_VERSION)
    arguments.update(changes)
    return build_assistant_context_pack(snapshot, "解释资料", [], **arguments)


@pytest.mark.parametrize("defect", ["foreign_company", "unknown_book", "event_version", "duplicate", "unbound_ocr"])
def test_summary_reference_binding_fails_closed(defect):
    snapshot = minimal_snapshot()
    snapshot["events"] = [{"id": "event-a", "fact_version": 2, "amount": "120.00"}]
    snapshot["evidence"] = [{"id": "event:event-a:v2", "kind": "event", "event_id": "event-a"}]
    if defect == "foreign_company":
        snapshot["evidence"][0]["company_id"] = "company-b"
    elif defect == "unknown_book":
        snapshot["evidence"].append({"id": "book:foreign", "kind": "book", "book_id": "foreign"})
    elif defect == "event_version":
        snapshot["evidence"][0]["id"] = "event:event-a:v1"
    elif defect == "duplicate":
        snapshot["evidence"].append(dict(snapshot["evidence"][0]))
    else:
        snapshot["events"][0]["documents"] = [{"attachment_id": "foreign", "extraction_id": "run-a", "document_index": 0}]
    with pytest.raises(ProviderError) as caught:
        compile_pack(snapshot)
    assert caught.value.llm_attempted is False


def test_rehashed_amount_tamper_rejected_and_ocr_summary_not_invented_page_evidence():
    from app.assistant.context_pack import validate_assistant_context_pack
    from app.assistant.provider import PROMPT_VERSION
    from app.intelligence.context_pack import fingerprint
    from app.intelligence.provider import ExecutionLimits

    snapshot = minimal_snapshot()
    snapshot["events"] = [{"id": "event-a", "fact_version": 2, "amount": "120.00", "documents": [
        {"attachment_id": "attachment-a", "extraction_id": "run-a", "document_index": 0, "gross_amount": "100.00"},
        {"attachment_id": "attachment-a", "extraction_id": "run-a", "document_index": 1, "gross_amount": "20.00"},
    ]}]
    snapshot["evidence"] = [
        {"id": "event:event-a:v2", "kind": "event", "event_id": "event-a"},
        {"id": "attachment:attachment-a", "kind": "attachment", "event_id": "event-a", "attachment_id": "attachment-a"},
    ]
    pack = compile_pack(snapshot).as_dict()
    ocr_facts = [row for row in pack["facts"] if row["key"].startswith("ocr:")]
    assert len(ocr_facts) == 2 and len({row["evidence_id"] for row in ocr_facts}) == 2
    assert all(row["source"] == "source_unreviewed" for row in ocr_facts)
    event = next(row for row in pack["facts"] if row["key"] == "event:event-a")
    assert event["value"]["amount"] == "120.00" and "documents" not in event["value"]
    assert all("page" not in row for row in pack["evidence"])
    event["value"]["amount"] = "100.00"
    pack["fingerprint"] = fingerprint({key: value for key, value in pack.items() if key != "fingerprint"})
    with pytest.raises(ProviderError) as caught:
        validate_assistant_context_pack(pack, snapshot, "解释资料", [], limits=ExecutionLimits(), prompt_version=PROMPT_VERSION)
    assert caught.value.llm_attempted is False


def test_real_budget_compile_refuses_required_facts_instead_of_dropping_them():
    from app.intelligence.provider import ExecutionLimits

    with pytest.raises(ProviderError) as caught:
        compile_pack(minimal_snapshot(), limits=ExecutionLimits(context_tokens=10, output_tokens=4))
    assert caught.value.code == "context_too_large" and caught.value.llm_attempted is False


def test_persisted_ocr_summary_enters_real_assistant_request_as_unreviewed(ai_case):
    event = create_event(ai_case)
    extracted = extract(ai_case, event)
    fixture = SimpleNamespace(**vars(ai_case), service=ai_case.business, other=ai_case.other_company)
    c = runtime(fixture, ai_case.actors["employee"])
    c.service.intelligence = ai_case.intelligence
    conv = thread(c, event_ids=[event["id"]])
    assert run(c, conv)["status"] == "succeeded"
    pack = transmitted_pack(c)
    documents = [row for row in pack["facts"] if row["source"] == "source_unreviewed"]
    assert len(documents) == 1
    assert documents[0]["value"]["extraction_id"] == extracted["id"]
    assert documents[0]["value"]["gross_amount"] == "0.30"
    assert "raw_text" not in json.dumps(pack) and "seller_tax_id" not in json.dumps(pack)
    assert all("page" not in row for row in pack["evidence"])
