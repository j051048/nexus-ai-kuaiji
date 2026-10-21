"""AK04 uses real SQLite/worker/router with synthetic transport and cost evidence."""

import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import Actor, AccountingError
from app.accounting.models import FirmMemberGrant
from app.assistant.meter import AssistantMeter
from app.assistant.models import ConversationCreate, TurnCreate
from app.assistant.provider import AssistantProvider
from app.assistant.router import build_assistant_router
from app.assistant.service import AssistantService
from app.assistant.worker import AssistantWorker
from app.intelligence.provider import AISettings, OpenAICompatibleProvider
from app.operations.pricing import Pricing, Rate
from test_ak03_assistant_facts import amount_claim, reply
from test_intelligence_workflow import ai_case, create_event
from test_model_cost_workflow import cost_case, cost_rows, policy


@pytest.fixture
def execution_case(cost_case):
    c = cost_case
    c.calls, c.mode, c.hook = [], "success", None
    c.model_reply = {"answer": "供应商私有自由文本", "citation_ids": [], "proposals": [],
                     "fact_status": "missing_facts", "claims": []}

    def respond(request):
        payload = json.loads(request.content)
        c.calls.append(payload)
        if c.hook:
            c.hook()
        if c.mode == "timeout":
            raise httpx.ReadTimeout("private-token synthetic-key endpoint secret", request=request)
        if c.mode == "429":
            return httpx.Response(429, json={"error": "private supplier body synthetic-key"})
        pack = json.loads(payload["messages"][1]["content"])["context_pack"]
        result = c.model_reply(pack) if callable(c.model_reply) else c.model_reply
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        })

    settings = AISettings(base_url="https://supplier.example.test/v1", api_key="synthetic-key", chat_model="synthetic-chat")
    c.chat_provider = AssistantProvider(OpenAICompatibleProvider(
        settings, httpx.Client(transport=httpx.MockTransport(respond), trust_env=False),
    ))
    c.chat_service = AssistantService(c.db, c.ledger, c.business, provider=c.chat_provider)
    c.meter.pricing = Pricing(ocr=c.pricing.ocr, proposal=c.pricing.proposal, chat=Rate("2.000000", "4.000000"))
    c.chat_worker = AssistantWorker(c.chat_service, c.chat_provider, AssistantMeter(c.db, c.meter))
    return c


def enqueue(c, actor=None, **scope):
    actor = actor or c.manager
    conv = c.chat_service.create(actor, ConversationCreate(
        company_id=c.company["id"], idempotency_key=uuid4().hex, **scope,
    ))["conversation"]
    turn = c.chat_service.enqueue(actor, conv["id"], TurnCreate(
        question="解释当前资料", expected_scope_version=1, idempotency_key=uuid4().hex,
    ))["turn"]
    return actor, conv, turn


def execute(c, **scope):
    actor, conv, turn = enqueue(c, **scope)
    assert c.chat_worker.run_once() == turn["id"]
    return actor, conv, c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]


def api(c):
    app = FastAPI()
    app.state.assistant = c.chat_service

    def current(x_user: str | None = Header(None)):
        if x_user is None:
            raise HTTPException(401)
        return Actor(x_user, c.manager.firm_id)

    @app.exception_handler(AccountingError)
    async def accounting_error(_, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    app.include_router(build_assistant_router(current))
    return TestClient(app, base_url="http://127.0.0.1")


@pytest.mark.parametrize("status,code", [
    ("missing_facts", "ak04_fact_missing"),
    ("conflicting_evidence", "ak04_fact_conflict"),
    ("unsupported_case", "ak04_fact_unsupported"),
    ("requires_accountant", "ak04_fact_accountant"),
])
def test_real_worker_persists_machine_abstention_and_preserves_reply_schema(execution_case, status, code):
    c = execution_case
    policy(c, chat_reserve="0.10")
    c.model_reply = {**c.model_reply, "fact_status": status}
    actor, conv, turn = execute(c)
    with c.db.transaction() as conn:
        raw = conn.execute("SELECT status,error_code FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()
    assert raw["error_code"] == code
    assert raw["status"] == turn["status"] == "succeeded" and turn["error_code"] == ""
    assert set(turn["reply"]) == {"answer", "citation_ids", "proposals"}
    assert turn["execution"]["validation"] is not None
    assert turn["execution"]["handoff"]["required"] is True
    assert cost_rows(c)[0]["state"] == "estimated" and len(c.calls) == 1
    record = c.chat_service.execution(actor, conv["id"], turn["id"])["execution"]
    assert record["execution_id"] == "chat:" + turn["id"] and record["kind"] == "chat"
    assert record["status"] == "succeeded"
    assert record["schema"] == "ak04-execution-v1"
    assert record["validation"]["code"] == {
        "missing_facts": "missing_facts", "conflicting_evidence": "conflicting_evidence",
        "unsupported_case": "unsupported_case", "requires_accountant": "requires_accountant",
    }[status]


@pytest.mark.parametrize("invalid", [False, True])
def test_bound_fact_validation_is_recorded_from_server_checks(execution_case, invalid):
    c = execution_case
    # An employee owns the event, so the claim is independently authorized.
    event = create_event(c)

    def supplier(pack):
        claim = amount_claim(pack)
        if invalid:
            claim["value"] = "999999.00"
        return reply([claim])

    c.model_reply = supplier
    actor, conv, turn = execute(c, actor=c.actors["employee"], event_ids=[event["id"]])
    with c.db.transaction() as conn:
        code = conn.execute("SELECT error_code FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()[0]
    assert code == ("ak04_fact_invalid" if invalid else "ak04_fact_verified")
    assert turn["execution"]["handoff"]["required"] is invalid
    assert turn["execution"]["validation"]["code"] == ("invalid" if invalid else "verified")
    assert "999999" not in turn["reply"]["answer"] and turn["error_code"] == ""


@pytest.mark.parametrize("mode", ["timeout", "429"])
def test_unknown_or_http_rejection_preserves_real_cost_and_never_replays(execution_case, mode):
    c = execution_case
    policy(c, chat_reserve="0.10")
    c.mode = mode
    actor, conv, turn = execute(c)
    record = c.chat_service.execution(actor, conv["id"], turn["id"])["execution"]
    assert record["failure"] is not None
    assert record["handoff"]["required"] is True
    assert record["handoff"]["automatic_retry"] is False
    assert cost_rows(c)[0]["state"] == "uncertain"
    assert c.chat_worker.run_once() is None and len(c.calls) == 1
    assert turn["status"] == ("interrupted" if mode == "timeout" else "failed")
    if mode == "timeout":
        assert record["failure"]["outcome_unknown"] is True
        assert record["failure"]["automatic_retry"] is False
    assert all(secret not in json.dumps(record) for secret in ("synthetic-key", "private supplier", "supplier.example.test"))


def test_execution_http_matches_turn_with_current_privacy_and_no_cost_read_for_employee(execution_case, monkeypatch):
    c = execution_case
    actor, conv, turn = execute(c, actor=c.actors["employee"])
    seen = []
    connect = c.db.connect

    def tracked_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(c.db, "connect", tracked_connect)
    with api(c) as client:
        path = f"/api/assistant/conversations/{conv['id']}/turns/{turn['id']}"
        assert client.get(path + "/execution").status_code == 401
        for other in (c.manager, c.actors["peer"], c.actors["finance"], c.actors["outsider"]):
            assert client.get(path + "/execution", headers={"x-user": other.user_id}).status_code == 404
        headers = {"x-user": actor.user_id}
        response = client.get(path + "/execution", headers=headers)
        assert response.status_code == 200
        record = response.json()["execution"]
        assert client.get(path, headers=headers).json()["turn"]["execution"] == record
        assert "diagnostics" not in record
        assert all(secret not in json.dumps(record) for secret in ("synthetic-key", "supplier.example.test", "charge_token", "input_tokens", "output_tokens"))
        assert not any("SELECT" in sql.upper() and "MODEL_COSTS" in sql.upper() for sql in seen)
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (c.company["id"], actor.user_id))
        assert client.get(path + "/execution", headers=headers).status_code == 404


def test_current_admin_diagnostics_does_not_invent_unsaved_context_hash(execution_case):
    c = execution_case
    policy(c, chat_reserve="0.10")
    actor, conv, turn = execute(c)
    record = c.chat_service.execution(actor, conv["id"], turn["id"])["execution"]
    assert "diagnostics" in record
    serialized = json.dumps(record)
    assert "estimated" in serialized
    assert all(value not in serialized for value in ("synthetic-key", "charge_token", "supplier.example.test", "供应商私有自由文本"))
    assert record["diagnostics"]["context_pack_hash"] is None


def test_diagnostic_role_is_rechecked_and_other_conversation_turn_is_hidden(execution_case):
    c = execution_case
    c.directory.grant_firm_member(c.manager, FirmMemberGrant(email="employee@example.test", role="admin"))
    actor, conv, turn = execute(c, actor=c.actors["employee"])
    assert "diagnostics" in c.chat_service.execution(actor, conv["id"], turn["id"])["execution"]
    other = c.chat_service.create(actor, ConversationCreate(
        company_id=c.other_company["id"], idempotency_key=uuid4().hex,
    ))["conversation"]
    with pytest.raises(AccountingError) as wrong_parent:
        c.chat_service.execution(actor, other["id"], turn["id"])
    assert wrong_parent.value.status == 404
    # Keep ordinary access while removing the current firm's diagnostic role.
    c.directory.grant_firm_member(c.manager, FirmMemberGrant(email="employee@example.test", role="reviewer"))
    assert "diagnostics" not in c.chat_service.execution(actor, conv["id"], turn["id"])["execution"]


def test_untrusted_persisted_error_text_never_reaches_turn_or_execution(execution_case):
    c = execution_case
    c.mode = "429"
    actor, conv, turn = execute(c, actor=c.actors["employee"])
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE assistant_turns SET error_code=?,error_detail=? WHERE id=?", (
            "provider-private-key-value", "synthetic-key https://supplier.example.test private traceback", turn["id"],
        ))
    result = c.chat_service.turn(actor, conv["id"], turn["id"])["turn"]
    assert result["error_code"] == "worker_failure"
    assert result["execution"]["failure"]["code"] == "worker_failure"
    assert all(value not in json.dumps(result) for value in (
        "synthetic-key", "supplier.example.test", "private traceback", "provider-private-key-value",
    ))


def test_send_time_revocation_hides_execution_and_keeps_cost_evidence(execution_case):
    c = execution_case
    policy(c, chat_reserve="0.10")
    actor, conv, turn = enqueue(c, c.actors["employee"])

    def revoke():
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?", (c.company["id"], actor.user_id))

    c.hook = revoke
    c.chat_worker.run_once()
    with pytest.raises(AccountingError) as denied:
        c.chat_service.execution(actor, conv["id"], turn["id"])
    assert denied.value.status == 404
    assert cost_rows(c)[0]["state"] == "estimated" and len(c.calls) == 1
    with c.db.transaction() as conn:
        assert conn.execute("SELECT status,reply_json FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()["status"] == "stale"
