"""ContextPack at real restricted PG service boundaries, synthetic HTTP only."""

import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from app.accounting.domain import AccountingError
from app.assistant.provider import AssistantProvider
from app.intake.models import IntakeRecognize
from app.intelligence.endpoints import AIEndpointPolicyService, CompanyPolicyUpdate
from app.intelligence.provider import AISettings, OpenAICompatibleProvider
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event, extract, handoff, propose
from test_stage12_intake import intake, create_intake, attach_intake
from test_stage15_conversations_postgres import WORKER_URL, chat_pg, make_thread, enqueue
from test_stage15_assistant_meter_postgres import billed_chat, cost


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK01_ISOLATED_TARGET") != "1"
    or not all((ADMIN_URL, APP_URL, WORKER_URL)),
    reason="requires explicitly opted-in disposable loopback PostgreSQL",
)


@pytest.fixture(autouse=True)
def isolated_target():
    for value in (ADMIN_URL, APP_URL, WORKER_URL):
        parsed = urlsplit(value)
        assert parsed.hostname == "127.0.0.1"
        assert parsed.port == 15438
        assert parsed.path.startswith("/ak01_local_")


def request_pack(request):
    message = json.loads(request.content)["messages"][1]["content"]
    if isinstance(message, list):
        for part in message:
            if part.get("type") == "text":
                try:
                    value = json.loads(part["text"])
                except ValueError:
                    continue
                if "context_pack" in value:
                    return value["context_pack"]
        pytest.fail("OCR request omitted ContextPack")
    return json.loads(message.split("\n", 1)[-1])["context_pack"]


def provider(settings, calls, document=None, hook=None):
    def respond(request):
        calls.append(request)
        if hook:
            hook()
        payload = json.loads(request.content)
        if payload["model"] == "synthetic-chat":
            output = {"answer": "请独立复核当前授权资料。", "citation_ids": [], "proposals": []}
        elif payload["model"] == "synthetic-vision":
            output = {"documents": [document]}
        else:
            supplied = json.loads(payload["messages"][1]["content"].split("\n", 1)[-1])
            # Return the same synthetic model output to the old request too;
            # the assertion below, not the fixture, must expose the missing pack.
            facts = ({row["key"]: row["value"] for row in supplied["context_pack"]["facts"]}
                     if "context_pack" in supplied else supplied)
            source = facts["documents"][0]
            amount = facts["business"]["amount"]
            output = {
                "description": "合成办公耗材", "reasoning": "金额来自当前业务资料。",
                "lines": [{"account_code": "6602.02", "debit": amount, "credit": "0.00"},
                          {"account_code": "2241", "debit": "0.00", "credit": amount}],
                "evidence_refs": [{"attachment_id": source["attachment_id"],
                                   "extraction_id": source["extraction_id"],
                                   "field": "gross_amount", "page": 1}], "warnings": [],
            }
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        })

    client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    return OpenAICompatibleProvider(settings, client=client), client


@pytest.fixture
def ticket_http(pg_ai):
    c = pg_ai
    c.requests = []
    c.provider, client = provider(c.provider.settings, c.requests, c.provider.document)
    c.intelligence.provider = c.provider
    c.intelligence.meter = MeterService(c.db, pricing=Pricing(
        ocr=Rate("1.000000", "2.000000"), proposal=Rate("1.000000", "2.000000"),
    ))
    try:
        yield c
    finally:
        client.close()


@pytest.mark.parametrize("entry", ["ocr", "journal", "intake"])
def test_pg_ticket_entries_send_bound_pack_and_meter_once(ticket_http, entry):
    c = ticket_http
    if entry == "intake":
        service = intake(c)
        original = attach_intake(c, service, create_intake(c, service))
        request = IntakeRecognize(expected_version=original["version"], idempotency_key=uuid4().hex)
        execute = lambda: service.recognize(c.actors["employee"], original["id"], original["attachments"][0]["id"], request)
    else:
        original = create_event(c)
        if entry == "journal":
            assert extract(c, original)["status"] == "succeeded"
            receipt = handoff(c, original)
            c.requests.clear()
            execute = lambda: propose(c, receipt["draft_id"])
        else:
            execute = lambda: extract(c, original)
    result = execute()
    assert result["status"] == "succeeded", result
    assert execute()["id"] == result["id"]
    assert len(c.requests) == 1
    pack = request_pack(c.requests[0])
    assert pack["scope"]["firm_id"] == c.manager.firm_id
    assert pack["scope"]["company_id"] == c.company["id"]
    assert pack["task"] == ("journal_suggest" if entry == "journal" else "invoice_extract")
    assert pack["coverage"]["complete_required_facts"]
    assert c.other_company["id"] not in json.dumps(pack)
    assert c.other_book["id"] not in json.dumps(pack)
    assert len(pack["fingerprint"]) == 64
    assert pack["prompt_version"] and pack["schema_version"] and pack["policy_version"]
    assert not any(row["source"] == "approved_fact" for row in pack["facts"])
    if entry == "intake":
        assert pack["scope"]["book_id"] is None and pack["scope"]["period"] is None
        attachment = next(row["value"] for row in pack["facts"] if row["key"] == "attachment")
        assert attachment["owner_id"] == original["id"] and attachment["owner_kind"] == "intake"
        assert attachment["attachment_id"] == original["attachments"][0]["id"]
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute("SELECT state,input_tokens,output_tokens,estimated_micro FROM model_costs WHERE id=?", (result["id"],)).fetchone()
    assert row["state"] == "estimated"
    assert (row["input_tokens"], row["output_tokens"], row["estimated_micro"]) == (3, 4, 11)


@pytest.mark.parametrize("entry", ["ocr", "intake"])
def test_pg_other_company_cannot_send_ticket_context(ticket_http, entry):
    c = ticket_http
    with pytest.raises(AccountingError):
        if entry == "ocr":
            extract(c, create_event(c), actor="outsider")
        else:
            service = intake(c)
            original = attach_intake(c, service, create_intake(c, service))
            service.recognize(c.actors["outsider"], original["id"], original["attachments"][0]["id"],
                              IntakeRecognize(expected_version=original["version"], idempotency_key=uuid4().hex))
    assert c.requests == []
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs WHERE company_id=?", (c.company["id"],)).fetchone()[0] == 0


@pytest.fixture
def chat_http(billed_chat):
    c = billed_chat
    c.requests = []
    c.response_hook = None
    settings = AISettings(base_url="https://supplier.example.invalid/v1", api_key="synthetic-only", chat_model="synthetic-chat")
    raw, client = provider(settings, c.requests, hook=lambda: c.response_hook() if c.response_hook else None)
    c.provider = AssistantProvider(raw)
    c.service.provider = c.worker.service.provider = c.worker.provider = c.provider
    try:
        yield c
    finally:
        client.close()


def test_pg_assistant_worker_sends_authorized_pack_and_keeps_cost(chat_http):
    c = chat_http
    conversation = make_thread(c)
    turn = enqueue(c, conversation)
    assert c.worker.run_once() == turn["id"]
    result = c.service.turn(c.actor, conversation["id"], turn["id"])["turn"]
    assert result["status"] == "succeeded", result
    assert len(c.requests) == 1
    pack = request_pack(c.requests[0])
    assert pack["scope"]["firm_id"] == c.actor.firm_id
    assert pack["scope"]["company_id"] == c.company
    assert pack["scope"]["book_id"] is None and pack["scope"]["period"] is None
    assert {row["book_id"] for row in pack["scope"]["books"]} == {c.book}
    assert c.source.other_company not in json.dumps(pack)
    assert c.source.other_book not in json.dumps(pack)
    assert pack["task"] == "assistant_reply"
    assert pack["coverage"]["complete_required_facts"]
    assert any(rule["required"] for rule in pack["rules"])
    row = cost(c, turn["id"])
    assert (row["state"], row["input_tokens"], row["output_tokens"], row["estimated_micro"]) == ("estimated", 3, 4, 22)
    assert c.worker.run_once() is None
    assert len(c.requests) == 1


def test_pg_revoked_assistant_scope_does_not_compile_send_or_charge(chat_http):
    c = chat_http
    conversation = make_thread(c)
    turn = enqueue(c, conversation)
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (c.book, c.actor.user_id))
    c.worker.run_once()
    assert c.requests == []
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        status, sent = conn.execute("SELECT status,sent_at FROM accounting.assistant_turns WHERE id=%s", (turn["id"],)).fetchone()
        assert conn.execute("SELECT count(*) FROM accounting.model_costs WHERE id=%s", (turn["id"],)).fetchone()[0] == 0
    assert status == "stale" and sent is None


def test_pg_policy_revoked_after_send_keeps_usage_but_does_not_publish(chat_http):
    c = chat_http
    conversation = make_thread(c)
    turn = enqueue(c, conversation)

    def deny_chat():
        AIEndpointPolicyService(c.db).update_policy(c.source.owner, c.company, CompanyPolicyUpdate(
            allowed_scenarios=["ocr"], allowed_data_categories=["document"],
            allowed_endpoint_ids=[], reason="合成调用在途时撤销对话授权",
        ))

    c.response_hook = deny_chat
    assert c.worker.run_once() == turn["id"]
    result = c.service.turn(c.actor, conversation["id"], turn["id"])["turn"]
    assert result["status"] == "stale", result
    assert len(c.requests) == 1
    row = cost(c, turn["id"])
    assert (row["state"], row["input_tokens"], row["output_tokens"], row["estimated_micro"]) == ("estimated", 3, 4, 22)
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'", (turn["id"],)).fetchone()[0] == 0
    assert c.worker.run_once() is None
    assert len(c.requests) == 1
