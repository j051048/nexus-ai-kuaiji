"""A human handoff creates an existing case, never approves or resends AI work."""
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.cases.service import CaseService
from test_intelligence_workflow import ai_case, create_event, extract, handoff


def setup(c):
    from app.execution.handoff import HandoffService
    event = create_event(c)
    run = extract(c, event)
    mapping = handoff(c, event, verify_original=False)
    cases = CaseService(c.db, c.business)
    service = HandoffService(cases, intelligence=c.intelligence)
    return service, cases, event, run, mapping


def command(preview, key=None, **changes):
    from app.execution.handoff import HandoffRequest
    return HandoffRequest(expected_fingerprint=preview["handoff"]["fingerprint"],
                          idempotency_key=key or uuid4().hex, reason="请会计核对原始票据与业务用途", **changes)


def test_handoff_case_is_idempotent_and_keeps_accounting_gate(ai_case):
    service, cases, event, run, mapping = setup(ai_case)
    actor = ai_case.actors["accountant"]
    preview = service.preview(actor, "ocr", run["id"])
    assert preview["handoff"]["supported"] is True
    assert preview["handoff"]["can_create"] is True
    request = command(preview)
    first = service.create(actor, "ocr", run["id"], request)
    again = service.create(actor, "ocr", run["id"], request)
    assert first["case"]["id"] == again["case"]["id"]
    assert first["case"]["state"] == "open" and first["case"]["kind"] == "exception"
    assert first["case"]["event_id"] == event["id"]
    assert cases.list(actor)["total"] == 1
    assert ai_case.provider.extract_calls == 1
    detail = ai_case.ledger.get_book(actor, ai_case.book["id"])
    assert all(d["status"] == "draft" for d in detail["drafts"])


def test_employee_cannot_publish_private_details_as_internal_case(ai_case):
    service, cases, event, run, _ = setup(ai_case)
    employee = ai_case.actors["employee"]
    preview = service.preview(employee, "ocr", run["id"])
    assert preview["handoff"]["can_create"] is False
    with pytest.raises(AccountingError) as caught:
        service.create(employee, "ocr", run["id"], command(preview))
    assert caught.value.status in {403, 404}
    assert cases.list(ai_case.actors["accountant"])["total"] == 0


@pytest.mark.parametrize("role", ["peer", "outsider"])
def test_handoff_read_and_write_use_current_resource_authorization(ai_case, role):
    service, _, _, run, _ = setup(ai_case)
    with pytest.raises(AccountingError):
        service.preview(ai_case.actors[role], "ocr", run["id"])


def test_changed_source_after_preview_rejects_case_creation(ai_case):
    service, cases, _, run, _ = setup(ai_case)
    actor = ai_case.actors["accountant"]
    preview = service.preview(actor, "ocr", run["id"])
    request = command(preview)
    request = request.model_copy(update={"expected_fingerprint": "0" * 64})
    with pytest.raises(AccountingError) as caught:
        service.create(actor, "ocr", run["id"], request)
    assert caught.value.status == 409 and cases.list(actor)["total"] == 0


def test_guard_rechecks_source_under_case_write_transaction(ai_case, monkeypatch):
    service, cases, event, run, _ = setup(ai_case)
    actor = ai_case.actors["accountant"]
    preview = service.preview(actor, "ocr", run["id"])
    original = cases.create

    def changed_before_write(current, payload, **kwargs):
        with ai_case.db.transaction(write=True) as conn:
            conn.execute("UPDATE business_events SET fact_version=fact_version+1 WHERE id=?", (event["id"],))
        return original(current, payload, **kwargs)

    monkeypatch.setattr(cases, "create", changed_before_write)
    with pytest.raises(AccountingError) as caught:
        service.create(actor, "ocr", run["id"], command(preview))
    assert caught.value.status == 409 and cases.list(actor)["total"] == 0


def test_replayed_handoff_after_revocation_cannot_restore_access(ai_case):
    service, _, _, run, _ = setup(ai_case)
    actor = ai_case.actors["accountant"]
    request = command(service.preview(actor, "ocr", run["id"]))
    service.create(actor, "ocr", run["id"], request)
    with ai_case.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM book_access WHERE user_id=?", (actor.user_id,))
    with pytest.raises(AccountingError):
        service.create(actor, "ocr", run["id"], request)


def test_same_key_different_human_reason_is_a_conflict(ai_case):
    service, cases, _, run, _ = setup(ai_case)
    actor = ai_case.actors["accountant"]
    request = command(service.preview(actor, "ocr", run["id"]))
    service.create(actor, "ocr", run["id"], request)
    with pytest.raises(AccountingError) as caught:
        service.create(actor, "ocr", run["id"], request.model_copy(update={"reason": "另一项请求"}))
    assert caught.value.status == 409 and cases.list(actor)["total"] == 1


def test_draft_change_after_preview_invalidates_handoff(ai_case):
    service, cases, _, run, mapping = setup(ai_case)
    actor = ai_case.actors["accountant"]
    preview = service.preview(actor, "ocr", run["id"])
    # Other actors can edit the still-unreviewed draft without changing the
    # business event version. The handoff fence must include that draft.
    with ai_case.db.transaction(write=True) as conn:
        conn.execute("UPDATE drafts SET version=version+1 WHERE id=?", (mapping["draft_id"],))
    with pytest.raises(AccountingError) as caught:
        service.create(actor, "ocr", run["id"], command(preview))
    assert caught.value.status == 409 and cases.list(actor)["total"] == 0


def test_chat_handoff_preview_and_replay_exclude_the_created_case(ai_case):
    from types import SimpleNamespace
    from app.execution.handoff import HandoffService
    from test_ak02_assistant_context import runtime, thread, run as run_chat
    service, cases, event, _, _ = setup(ai_case)
    c = runtime(SimpleNamespace(**vars(ai_case), service=ai_case.business), ai_case.actors["accountant"])
    c.service.intelligence = ai_case.intelligence
    conversation = thread(c, event_ids=[event["id"]], book_ids=[ai_case.book["id"]])
    turn = run_chat(c, conversation)
    service = HandoffService(cases, assistant=c.service, intelligence=ai_case.intelligence)
    preview = service.preview(c.actor, "chat", turn["id"])
    request = command(preview)
    first = service.create(c.actor, "chat", turn["id"], request)
    again = service.create(c.actor, "chat", turn["id"], request)
    assert first["case"]["id"] == again["case"]["id"]


def test_same_key_for_another_execution_is_a_conflict(ai_case):
    service, cases, event, run, _ = setup(ai_case)
    actor = ai_case.actors["accountant"]
    first = command(service.preview(actor, "ocr", run["id"]))
    service.create(actor, "ocr", run["id"], first)
    latest = ai_case.business.get_event(actor, event["id"])
    other = extract(ai_case, latest, actor="accountant", key=uuid4().hex)
    second = command(service.preview(actor, "ocr", other["id"]), key=first.idempotency_key)
    with pytest.raises(AccountingError) as caught:
        service.create(actor, "ocr", other["id"], second)
    assert caught.value.status == 409 and cases.list(actor)["total"] == 1


def test_handoff_preserves_unknown_cost_and_no_supplier_replay(ai_case):
    from app.execution.handoff import HandoffService
    from app.intelligence.provider import ProviderError
    ai_case.provider.failure = ProviderError("timeout", "private response")
    event = create_event(ai_case)
    run = extract(ai_case, event)
    handoff(ai_case, event, verify_original=False)
    cases = CaseService(ai_case.db, ai_case.business)
    service = HandoffService(cases, intelligence=ai_case.intelligence)
    actor = ai_case.actors["accountant"]
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        before = dict(conn.execute("SELECT * FROM model_costs WHERE id=?", (run["id"],)).fetchone())
    preview = service.preview(actor, "ocr", run["id"])
    assert preview["failure"]["outcome_unknown"] is True
    request = command(preview)
    service.create(actor, "ocr", run["id"], request)
    service.create(actor, "ocr", run["id"], request)
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        after = dict(conn.execute("SELECT * FROM model_costs WHERE id=?", (run["id"],)).fetchone())
    assert before == after and after["state"] == "uncertain"
    assert ai_case.provider.extract_calls == 1


def test_unified_http_requires_auth_and_rejects_forged_target(ai_case):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from app.accounting.domain import Actor
    from app.execution.router import build_execution_router
    service, cases, _, run, _ = setup(ai_case)
    app = FastAPI()
    app.state.cases, app.state.intelligence = cases, ai_case.intelligence
    app.state.assistant = app.state.intake = None

    def current(x_user: str | None = Header(None)):
        if x_user is None:
            raise HTTPException(401)
        return Actor(x_user, ai_case.manager.firm_id)

    @app.exception_handler(AccountingError)
    async def error_handler(_, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    app.include_router(build_execution_router(current))
    path = "/api/executions/ocr/" + run["id"]
    headers = {"x-user": ai_case.actors["accountant"].user_id}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"x-user": ai_case.actors["outsider"].user_id}).status_code == 404
        preview = client.get(path, headers=headers)
        assert preview.status_code == 200
        payload = command(preview.json()).model_dump()
        assert client.post(path + "/handoff", headers=headers, json={**payload, "event_id": "foreign"}).status_code == 422
        result = client.post(path + "/handoff", headers=headers, json=payload)
        again = client.post(path + "/handoff", headers=headers, json=payload)
        assert result.status_code == again.status_code == 201
        assert result.json()["case"]["id"] == again.json()["case"]["id"]
