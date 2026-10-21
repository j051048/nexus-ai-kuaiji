"""AK03 fact publication and manual review in disposable restricted PostgreSQL."""

import json
from uuid import uuid4

import httpx
import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.assistant.models import ConversationCreate
from app.assistant.provider import AssistantProvider
from app.intelligence.provider import AISettings, OpenAICompatibleProvider
from test_ak02_postgres import (  # noqa: F401
    ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres,
    pg_ai, chat_pg, billed_chat, isolated_target, ticket_http, pytestmark,
)
from test_intelligence_workflow import create_event, extract, handoff, propose
from test_stage15_conversations_postgres import make_thread, enqueue
from test_stage15_assistant_meter_postgres import cost


def model_reply(pack, kind):
    output = {"answer": "利润已确认999999元，凭证已审核并导出。", "citation_ids": [],
              "proposals": [{"kind": "ocr_batch", "reason": "利润已确认999999元"}]}
    if kind == "unknown_citation":
        output["citation_ids"] = ["event:other-company:v1"]
    elif kind in {"valid", "wrong_value"}:
        fact = next(row for row in pack["facts"] if row["key"].startswith("event:"))
        output.update(fact_status="verified", claims=[{
            "fact_key": fact["key"], "path": "/amount", "field": "amount",
            "value": fact["value"]["amount"] if kind == "valid" else "100.00",
            "company_id": pack["scope"]["company_id"], "book_id": None,
            "evidence_version": fact["version"], "evidence_id": fact["evidence_id"],
            "source": fact["source"], "unit": "元", "currency": "CNY",
        }])
    return output


@pytest.fixture
def facts_chat(billed_chat, pg_ai):
    c = billed_chat
    c.ai = pg_ai
    c.requests = []
    c.reply_kind = "unbound"

    def respond(request):
        c.requests.append(request)
        payload = json.loads(request.content)
        pack = json.loads(payload["messages"][1]["content"])["context_pack"]
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(model_reply(pack, c.reply_kind)),
            }}], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        })

    settings = AISettings(base_url="https://supplier.example.invalid/v1", api_key="synthetic-only", chat_model="synthetic-chat")
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        c.provider = AssistantProvider(OpenAICompatibleProvider(settings, client=client))
        c.service.provider = c.worker.service.provider = c.worker.provider = c.provider
        yield c


@pytest.mark.parametrize("kind", ["valid", "wrong_value", "unbound"])
def test_pg_internal_claims_never_escape_public_reply_or_publish_invented_facts(facts_chat, kind):
    c = facts_chat
    event = create_event(c.ai, amount="120.00")
    handoff(c.ai, event)
    c.reply_kind = kind
    conversation = make_thread(c, event_ids=[event["id"]])
    turn = enqueue(c, conversation)
    assert c.worker.run_once() == turn["id"]
    result = c.service.turn(c.actor, conversation["id"], turn["id"])["turn"]
    assert result["status"] == "succeeded", result
    public = result["reply"]
    assert set(public) == {"answer", "citation_ids", "proposals"}
    assert "999999" not in public["answer"] and "已审核并导出" not in public["answer"]
    assert public["proposals"] == []
    if kind == "valid":
        assert "120.00" in public["answer"] and "已保存" in public["answer"]
        assert len(public["citation_ids"]) == 1 and public["citation_ids"][0].startswith("event:" + event["id"])
    else:
        assert "120.00" not in public["answer"] and "人工" in public["answer"]
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        stored = conn.execute("SELECT reply_json FROM accounting.assistant_turns WHERE id=%s", (turn["id"],)).fetchone()[0]
        message = conn.execute("SELECT content_json FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'", (turn["id"],)).fetchone()[0]
    assert json.loads(stored) == json.loads(message) == public
    row = cost(c, turn["id"])
    assert (row["state"], row["input_tokens"], row["output_tokens"], row["estimated_micro"]) == ("estimated", 3, 4, 22)
    assert c.worker.run_once() is None and len(c.requests) == 1


def test_pg_illegal_citation_fails_with_known_cost_without_message_or_resend(facts_chat):
    c = facts_chat
    c.reply_kind = "unknown_citation"
    conversation = make_thread(c)
    turn = enqueue(c, conversation)
    c.worker.run_once()
    result = c.service.turn(c.actor, conversation["id"], turn["id"])["turn"]
    assert result["status"] == "failed" and result["error_code"] == "citation_invalid"
    assert result["reply"] is None
    assert cost(c, turn["id"])["estimated_micro"] == 22
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'", (turn["id"],)).fetchone()[0] == 0
    assert c.worker.run_once() is None and len(c.requests) == 1


def test_pg_cross_company_conversation_cannot_create_fact_context_or_charge(facts_chat):
    c = facts_chat
    with pytest.raises(AccountingError):
        c.service.create(c.actor, ConversationCreate(company_id=c.source.other_company,
                         book_ids=[c.source.other_book], idempotency_key=uuid4().hex))
    assert c.requests == []
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT count(*) FROM accounting.model_costs WHERE firm_id=%s", (c.actor.firm_id,)).fetchone()[0] == 0


def test_pg_pure_vision_requires_independent_original_review_before_apply_and_export(ticket_http):
    c = ticket_http
    event = create_event(c)
    assert extract(c, event)["status"] == "succeeded"
    receipt = handoff(c, event)
    suggestion = propose(c, receipt["draft_id"])
    assert suggestion["status"] == "succeeded", suggestion
    with pytest.raises(AccountingError):
        c.intelligence.apply(c.actors["accountant"], suggestion["id"], 1)
    export = ExportRequest(draft_ids=[receipt["draft_id"]], idempotency_key=uuid4().hex)
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors["accountant"], c.book["id"], export)
    findings = c.intelligence.reconcile(c.actors["accountant"], event["id"])["findings"]
    issue = next(row for row in findings if row["code"] == "document_1_original_unverified")
    assert issue["severity"] == "blocking" and not issue["resolved"]
    with pytest.raises(AccountingError):
        c.intelligence.resolve(c.actors["employee"], issue["id"], "申请人自称无误不能替代独立复核")
    c.intelligence.resolve(c.actors["accountant"], issue["id"], "会计已逐字段核对合成原件，金额日期和往来方一致")
    applied = c.intelligence.apply(c.actors["accountant"], suggestion["id"], 1)
    assert applied["status"] == "draft"
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors["accountant"], c.book["id"], export)
    c.ledger.approve(c.actors["accountant"], c.book["id"], applied["id"], ApprovalRequest(expected_version=applied["version"]))
    assert c.ledger.export(c.actors["accountant"], c.book["id"], export)["count"] == 1
    assert len(c.requests) == 2


def test_pg_balanced_unproved_split_is_not_published_but_known_cost_is_kept(ticket_http):
    c = ticket_http
    event = create_event(c)
    assert extract(c, event)["status"] == "succeeded"
    receipt = handoff(c, event)
    calls = []

    def respond(request):
        calls.append(request)
        from test_ak02_postgres import request_pack

        facts = {row["key"]: row["value"] for row in request_pack(request)["facts"]}
        doc = facts["documents"][0]
        result = {
            "description": "伪造拆分", "reasoning": "只靠借贷平衡无法证明分摊", "warnings": [],
            "lines": [{"account_code": "6602.02", "debit": "0.15", "credit": "0.00"},
                      {"account_code": "6602.02", "debit": "0.15", "credit": "0.00"},
                      {"account_code": "2241", "debit": "0.00", "credit": "0.30"}],
            "evidence_refs": [{"attachment_id": doc["attachment_id"], "extraction_id": doc["extraction_id"],
                               "field": "gross_amount", "page": 1}],
        }
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                                        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}})

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        original_client = c.provider._client
        c.provider._client = client
        try:
            result = propose(c, receipt["draft_id"])
            assert result["status"] == "failed", result
            assert propose(c, receipt["draft_id"])["id"] == result["id"]
            assert len(calls) == 1
        finally:
            c.provider._client = original_client
    with c.db.transaction(actor=c.manager) as conn:
        charge = conn.execute("SELECT state,estimated_micro FROM model_costs WHERE id=?", (result["id"],)).fetchone()
        applied = conn.execute("SELECT count(*) FROM ai_proposal_applications WHERE run_id=?", (result["id"],)).fetchone()[0]
    assert charge["state"] == "estimated" and charge["estimated_micro"] == 11
    assert applied == 0
