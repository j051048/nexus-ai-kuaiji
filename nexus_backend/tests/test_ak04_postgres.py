"""AK04 disposable PG14: real restricted roles, synthetic HTTP, existing cases."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.assistant.router import build_assistant_router
from app.cases.models import CaseCommand
from app.cases.service import CaseService
from app.execution.handoff import HandoffRequest, HandoffService
from app.execution.router import build_execution_router
from test_ak03_postgres import (  # noqa: F401
    ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres, pg_ai,
    chat_pg, billed_chat, facts_chat, isolated_target, ticket_http, pytestmark,
)
from test_intelligence_workflow import create_event, extract, handoff, propose
from test_stage15_assistant_meter_postgres import cost
from test_stage15_conversations_postgres import make_thread, enqueue


def handoff_command(preview, key=None):
    return HandoffRequest(expected_fingerprint=preview["handoff"]["fingerprint"],
                          idempotency_key=key or uuid4().hex, reason="会计逐项核对合成原件与业务事实")


def app_for(c, cases=None, intelligence=None):
    app = FastAPI()
    app.state.assistant, app.state.cases = c.service, cases
    app.state.intelligence, app.state.intake = intelligence, None

    def actor(x_user: str | None = Header(None)):
        if x_user is None:
            raise HTTPException(401)
        return Actor(x_user, c.actor.firm_id)

    @app.exception_handler(AccountingError)
    async def accounting_error(_, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    app.include_router(build_assistant_router(actor))
    app.include_router(build_execution_router(actor))
    return app


def test_pg_runtime_and_worker_are_real_restricted_logins(facts_chat):
    c = facts_chat
    with c.db.transaction(actor=c.actor) as conn:
        row = conn.execute(
            "SELECT session_user,current_user,(SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=session_user)"
        ).fetchone()
        login, role, privileged = row[0], row[1], row[2]
    assert login.startswith("ak04_runtime_") and role == "accounting_app" and privileged is False
    conn = c.worker.db.connect()
    try:
        conn.begin(role="accounting_worker")
        row = conn.execute(
            "SELECT session_user,current_user,(SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=session_user)"
        ).fetchone()
        login, role, privileged = row[0], row[1], row[2]
        assert login.startswith("ak04_worker_") and role == "accounting_worker" and privileged is False
        conn.rollback()
    finally:
        conn.close()


@pytest.mark.parametrize("kind,code,validation", [
    ("valid", "ak04_fact_verified", "verified"),
    ("wrong_value", "ak04_fact_invalid", "invalid"),
    ("unbound", "ak04_fact_accountant", "requires_accountant"),
])
def test_pg_worker_persists_validation_code_and_http_execution_is_private(facts_chat, kind, code, validation):
    c = facts_chat
    event = create_event(c.ai, amount="120.00")
    handoff(c.ai, event)
    c.reply_kind = kind
    conversation = make_thread(c, event_ids=[event["id"]])
    turn = enqueue(c, conversation)
    assert c.worker.run_once() == turn["id"]
    with c.db.transaction(actor=c.actor) as conn:
        stored = conn.execute("SELECT status,error_code,reply_json FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone()
    assert stored["status"] == "succeeded" and stored["error_code"] == code
    assert set(json.loads(stored["reply_json"])) == {"answer", "citation_ids", "proposals"}
    with TestClient(app_for(c)) as client:
        path = f"/api/assistant/conversations/{conversation['id']}/turns/{turn['id']}"
        assert client.get(path + "/execution").status_code == 401
        assert client.get(path + "/execution", headers={"x-user": c.source.owner.user_id}).status_code == 404
        headers = {"x-user": c.actor.user_id}
        response = client.get(path + "/execution", headers=headers)
        assert response.status_code == 200, response.text
        record = response.json()["execution"]
        assert record["execution_id"] == "chat:" + turn["id"]
        assert record["validation"]["code"] == validation
        assert "diagnostics" not in record
        result = client.get(path, headers=headers).json()["turn"]
        assert result["execution"] == record and result["error_code"] == ""
        assert all(value not in json.dumps(record) for value in (
            "synthetic-only", "supplier.example.invalid", "input_tokens", "charge_token", "999999",
        ))
    assert cost(c, turn["id"])["state"] == "estimated"
    assert c.worker.run_once() is None and len(c.requests) == 1


def test_pg_execution_uses_current_book_access_before_worker_send(facts_chat):
    c = facts_chat
    conversation = make_thread(c)
    turn = enqueue(c, conversation)
    assert c.service.execution(c.actor, conversation["id"], turn["id"])["execution"]["status"] == "queued"
    DirectoryService(c.db).revoke_book_member(c.source.owner, c.book, c.actor.user_id)
    with pytest.raises(AccountingError):
        c.service.execution(c.actor, conversation["id"], turn["id"])
    c.worker.run_once()
    assert c.requests == []
    with c.db.transaction(actor=c.source.owner) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs WHERE id=?", (turn["id"],)).fetchone()[0] == 0


def test_pg_admin_diagnostics_and_ticket_execution_keep_real_cost_evidence(ticket_http):
    c = ticket_http
    event = create_event(c)
    run = extract(c, event)
    employee = c.actors["employee"]
    ordinary = c.intelligence.run_execution(employee, event["id"], run["id"])
    assert ordinary["execution_id"] == "ocr:" + run["id"] and "diagnostics" not in ordinary
    assert "document_1_original_unverified" in ordinary["validation"]["finding_codes"]
    handoff(c, event)
    diagnostic = c.intelligence.run_execution(c.manager, event["id"], run["id"])
    assert diagnostic["diagnostics"]["costs"][0]["state"] == "estimated"
    assert diagnostic["diagnostics"]["costs"][0]["input_tokens"] == 3
    assert diagnostic["diagnostics"]["context_pack_hash"] is None
    assert "charge_token" not in json.dumps(diagnostic)
    for other in (c.actors["peer"], c.actors["outsider"]):
        with pytest.raises(AccountingError):
            c.intelligence.run_execution(other, event["id"], run["id"])
    assert len(c.requests) == 1


def test_pg_chat_handoff_uses_safe_runtime_columns_and_replays_one_case(facts_chat):
    c = facts_chat
    event = create_event(c.ai)
    handoff(c.ai, event)
    conversation = make_thread(c, event_ids=[event["id"]])
    turn = enqueue(c, conversation)
    c.worker.run_once()
    cases = CaseService(c.db, c.ai.business)
    with TestClient(app_for(c, cases, c.ai.intelligence)) as client:
        path = "/api/executions/chat/" + turn["id"]
        headers = {"x-user": c.actor.user_id}
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["handoff"]["supported"] is True
        assert preview["handoff"]["can_create"] is True
        request = handoff_command(preview).model_dump()
        first = client.post(path + "/handoff", headers=headers, json=request)
        assert first.status_code == 201, first.text
        again = client.post(path + "/handoff", headers=headers, json=request)
        assert again.status_code == 201 and again.json()["case"]["id"] == first.json()["case"]["id"]
        assert first.json()["case"]["kind"] == "exception" and first.json()["case"]["state"] == "open"
        assert first.json()["automatic_retry"] is False
        assert client.post(path + "/handoff", headers={"x-user": c.source.colleague.user_id}, json=request).status_code == 404
    assert cases.list(c.actor)["total"] == 1
    assert c.worker.run_once() is None and len(c.requests) == 1


def test_pg_ticket_handoff_does_not_replace_original_or_accounting_review(ticket_http):
    c = ticket_http
    event = create_event(c)
    run = extract(c, event)
    mapping = handoff(c, event)
    actor = c.actors["accountant"]
    cases = CaseService(c.db, c.business)
    service = HandoffService(cases, intelligence=c.intelligence)
    preview = service.preview(actor, "ocr", run["id"])
    assert preview["handoff"]["can_create"] is True
    request = handoff_command(preview)
    first = service.create(actor, "ocr", run["id"], request)
    again = service.create(actor, "ocr", run["id"], request)
    assert again["case"]["id"] == first["case"]["id"] and cases.list(actor)["total"] == 1
    with pytest.raises(AccountingError):
        service.create(c.actors["outsider"], "ocr", run["id"], request)
    assert service.preview(c.actors["employee"], "ocr", run["id"])["handoff"]["can_create"] is False
    with pytest.raises(AccountingError):
        service.create(c.actors["employee"], "ocr", run["id"], handoff_command(service.preview(c.actors["employee"], "ocr", run["id"])))
    export = ExportRequest(draft_ids=[mapping["draft_id"]], idempotency_key=uuid4().hex)
    with pytest.raises(AccountingError):
        c.ledger.export(actor, c.book["id"], export)
    case = cases.command(actor, first["case"]["id"], "close", CaseCommand(
        expected_version=first["case"]["version"], idempotency_key=uuid4().hex,
        reason="已完成合成资料人工调查，后续另行核验原件和凭证",
    ))
    assert case["state"] == "closed"
    suggestion = propose(c, mapping["draft_id"])
    assert suggestion["status"] == "succeeded"
    with pytest.raises(AccountingError):
        c.intelligence.apply(actor, suggestion["id"], 1)
    findings = c.intelligence.reconcile(actor, event["id"])["findings"]
    issue = next(row for row in findings if row["code"] == "document_1_original_unverified")
    with pytest.raises(AccountingError):
        c.intelligence.resolve(c.actors["employee"], issue["id"], "申请人自称无需复核")
    c.intelligence.resolve(actor, issue["id"], "会计已逐字段核对合成原件、日期、金额与往来方")
    applied = c.intelligence.apply(actor, suggestion["id"], 1)
    assert applied["status"] == "draft"
    with pytest.raises(AccountingError):
        c.ledger.export(actor, c.book["id"], export)
    c.ledger.approve(actor, c.book["id"], applied["id"], ApprovalRequest(expected_version=applied["version"]))
    assert c.ledger.export(actor, c.book["id"], export)["count"] == 1
    assert len(c.requests) == 2


@pytest.mark.parametrize("moment", ["after_preview", "before_case_write"])
def test_pg_draft_change_rejects_handoff_before_or_inside_case_lock(ticket_http, monkeypatch, moment):
    c = ticket_http
    event = create_event(c)
    run = extract(c, event)
    mapping = handoff(c, event)
    actor = c.actors["accountant"]
    cases = CaseService(c.db, c.business)
    service = HandoffService(cases, intelligence=c.intelligence)
    request = handoff_command(service.preview(actor, "ocr", run["id"]))

    def change_draft():
        # Fault injection uses the real restricted runtime and current reviewer.
        with c.db.transaction(write=True, actor=actor, company_id=c.company["id"], write_scope="company") as conn:
            assert conn.execute("UPDATE drafts SET version=version+1 WHERE id=? AND book_id=?",
                                (mapping["draft_id"], c.book["id"])).rowcount == 1

    if moment == "after_preview":
        change_draft()
    else:
        create_case = cases.create

        def changed_before_company_lock(current, payload, **kwargs):
            change_draft()
            return create_case(current, payload, **kwargs)

        monkeypatch.setattr(cases, "create", changed_before_company_lock)
    with pytest.raises(AccountingError) as changed:
        service.create(actor, "ocr", run["id"], request)
    assert changed.value.status == 409
    assert cases.list(actor)["total"] == 0
    assert len(c.requests) == 1


def test_pg_same_handoff_key_concurrent_connections_create_one_case(ticket_http, monkeypatch):
    c = ticket_http
    event = create_event(c)
    run = extract(c, event)
    handoff(c, event)
    actor = c.actors["accountant"]
    cases = CaseService(c.db, c.business)
    service = HandoffService(cases, intelligence=c.intelligence)
    request = handoff_command(service.preview(actor, "ocr", run["id"]))
    barrier, write_pids = Barrier(2), []
    create_case = cases.create

    def observed_create(current, payload, *, source_guard=None):
        def observed_guard(conn):
            # Observe the real production company write transaction; no SQL or
            # lock is mocked. Both callers retain their own PG connections.
            write_pids.append(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            source_guard(conn)
        return create_case(current, payload, source_guard=observed_guard)

    monkeypatch.setattr(cases, "create", observed_create)
    with c.db.transaction(actor=c.manager) as conn:
        before = [tuple(row[key] for key in ("id", "state", "reserved_micro", "estimated_micro", "confirmed_micro"))
                  for row in conn.execute("SELECT id,state,reserved_micro,estimated_micro,confirmed_micro FROM model_costs WHERE id=?", (run["id"],))]
    calls = len(c.requests)

    def submit():
        barrier.wait(timeout=10)
        return service.create(actor, "ocr", run["id"], request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        results = [future.result(timeout=25) for future in futures]
    assert len(write_pids) == 2 and len(set(write_pids)) == 2
    assert results[0]["case"]["id"] == results[1]["case"]["id"]
    assert cases.list(actor)["total"] == 1 and len(c.requests) == calls == 1
    with c.db.transaction(actor=c.manager) as conn:
        after = [tuple(row[key] for key in ("id", "state", "reserved_micro", "estimated_micro", "confirmed_micro"))
                 for row in conn.execute("SELECT id,state,reserved_micro,estimated_micro,confirmed_micro FROM model_costs WHERE id=?", (run["id"],))]
    assert after == before and len(after) == 1


def test_pg_structured_xml_intake_retains_private_provenance_without_model_fee(pg_ai):
    from app.intake.models import IntakeRecognize
    from app.intelligence.provider import AISettings
    from test_stage12_intake import intake, create_intake, confirmed
    from test_stage16_structured_invoice import xml_bytes

    c = pg_ai
    c.provider.settings = AISettings()
    service = intake(c)
    draft = create_intake(c, service)
    draft = service.add_attachment(c.actors["employee"], draft["id"], draft["version"],
                                   c.storage.put("SYNTHETIC_数电票.xml", "application/xml", xml_bytes()))
    request = IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex)
    run = service.recognize(c.actors["employee"], draft["id"], draft["attachments"][0]["id"], request)
    assert run["status"] == "succeeded" and run.get("provider") is None
    execution = service.run_execution(c.actors["employee"], draft["id"], run["id"])
    assert "diagnostics" not in execution
    with pytest.raises(AccountingError):
        service.run_execution(c.actors["peer"], draft["id"], run["id"])
    event = service.complete(c.actors["employee"], draft["id"], confirmed(draft))["event"]
    transferred = c.intelligence.detail(c.actors["employee"], event["id"])["runs"][0]
    assert transferred["status"] == "succeeded" and transferred["provider"] is None
    assert transferred["extraction_method"] == "structured_xml"
    assert "diagnostics" not in transferred["execution"]
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert conn.execute("SELECT provider FROM intake_runs WHERE id=?", (run["id"],)).fetchone()[0] == "local-structured-parser"
        assert conn.execute("SELECT provider FROM ai_runs WHERE id=?", (transferred["id"],)).fetchone()[0] == "local-structured-parser"
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs WHERE id IN (?,?)", (run["id"], transferred["id"])).fetchone()[0] == 0
    assert c.provider.extract_calls == 0


def test_pg_structured_xml_invalid_tax_sum_still_blocks_and_never_caches(pg_ai):
    from app.intelligence.provider import AISettings
    from test_stage16_structured_invoice import xml_bytes, xml_event

    c = pg_ai
    c.provider.settings = AISettings()
    invalid = xml_bytes().replace(b"<TotalTaxAm>0.00", b"<TotalTaxAm>0.01")
    runs = []
    for _ in range(2):
        event = xml_event(c, invalid)
        run = extract(c, event)
        runs.append(run)
        assert run["status"] == "succeeded" and run["cache_hit"] == 0
        execution = c.intelligence.run_execution(c.actors["employee"], event["id"], run["id"])
        assert execution["validation"]["status"] == "blocked"
        assert "document_1_tax_sum" in execution["validation"]["finding_codes"]
    assert runs[0]["id"] != runs[1]["id"] and c.provider.extract_calls == 0
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs WHERE id IN (?,?)", (runs[0]["id"], runs[1]["id"])).fetchone()[0] == 0
