"""Read-only durable milestones; mock provider only, no new model calls."""

import json
import socket
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.assistant.models import TurnCancel
from app.assistant.service import AssistantService
from app.assistant.worker import AssistantWorker
from app.intelligence.provider import ProviderError
from test_business import business
from test_stage15_conversations import assistant_case, conversation, question
from test_ak04_assistant_execution import api


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original = socket.socket.connect

    def local_only(sock, address):
        # Windows asyncio creates a loopback socket pair for TestClient.
        assert isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


def setup(business):
    c = assistant_case(business)
    conv = conversation(c)
    turn = question(c, conv)
    return c, conv, turn


def read(c, conv, turn, **kw):
    return c.service.events(c.actor, conv["id"], turn["id"], **kw)


def test_queue_send_finish_resume_and_stable_replay(business):
    c, conv, turn = setup(business)
    queued = read(c, conv, turn)
    assert [e["seq"] for e in queued["events"]] == [1]
    assert queued["terminal"] is False and queued["has_more"] is False
    assert read(c, conv, turn, after=queued["cursor"])["events"] == []
    while_sending = []
    c.provider.hook = lambda: while_sending.append(read(c, conv, turn, after=queued["cursor"]))
    c.worker.run_once()
    sent = while_sending[0]
    assert [e["kind"] for e in sent["events"]] == ["send_marked"]
    assert "尚未确认" in sent["events"][0]["message"]
    completed = read(c, conv, turn, after=sent["cursor"])
    assert [e["seq"] for e in completed["events"]] == [3] and completed["terminal"]
    assert completed["events"][0]["accounting_approved"] is False
    assert read(c, conv, turn, after=completed["cursor"])["events"] == []
    old_page = read(c, conv, turn, limit=1)
    assert old_page["events"] == queued["events"] and old_page["cursor"] == queued["cursor"]
    assert old_page["has_more"] is True
    # A new service process can replay from the cursor without replaying work.
    restarted = AssistantService(c.db, c.source.ledger, c.source.service, provider=c.provider)
    assert restarted.events(c.actor, conv["id"], turn["id"], after=sent["cursor"]) == completed
    assert c.provider.calls == 1 and c.meter.reserves == 1


@pytest.mark.parametrize("sent", [False, True])
def test_cancel_and_unknown_never_claim_received_or_accounting_approval(business, sent):
    c, conv, turn = setup(business)
    if sent:
        c.provider.error = ProviderError("timeout", "PRIVATE_TOKEN_AND_MODEL_PROSE")
        c.worker.run_once()
    else:
        c.service.cancel(c.actor, conv["id"], turn["id"], TurnCancel(reason="PRIVATE_REASON"))
    feed = read(c, conv, turn)
    assert [e["seq"] for e in feed["events"]] == ([1, 2, 3] if sent else [1, 3])
    assert feed["events"][-1]["outcome_unknown"] is sent
    assert feed["events"][-1]["automatic_retry"] is False
    assert "PRIVATE_" not in json.dumps(feed)
    assert "snapshot_json" not in json.dumps(feed) and "diagnostics" not in json.dumps(feed)
    assert AssistantWorker(c.service, c.provider, c.meter).run_once() is None
    assert c.provider.calls == (1 if sent else 0)


def test_cursor_cannot_cross_turn_and_revocation_precedes_cursor_parsing(business):
    c, conv, turn = setup(business)
    cursor = read(c, conv, turn)["cursor"]
    c.worker.run_once()
    other = question(c, conv)
    with pytest.raises(AccountingError) as mismatch:
        read(c, conv, other, after=cursor)
    assert mismatch.value.status == 409
    for actor in (business.manager, business.actors["employee"]):
        with pytest.raises(AccountingError) as denied:
            c.service.events(actor, conv["id"], turn["id"], after=cursor)
        assert denied.value.status == 404
    with c.db.transaction(write=True, actor=business.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book, c.actor.user_id)
        )
    with pytest.raises(AccountingError) as denied:
        read(c, conv, turn, after="malformed")
    assert denied.value.status == 404


@pytest.mark.parametrize("cursor", ["malformed", "e30", "x" * 2049, True, 1])
def test_bad_cursor_rejected_without_resending(business, cursor):
    c, conv, turn = setup(business)
    with pytest.raises(AccountingError) as bad:
        read(c, conv, turn, after=cursor)
    assert bad.value.status == 409 and c.provider.calls == 0


def test_events_http_contract_and_permission(business):
    c, conv, turn = setup(business)
    client = api(SimpleNamespace(chat_service=c.service, manager=business.manager))
    path = f"/api/assistant/conversations/{conv['id']}/turns/{turn['id']}/events"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-User": business.manager.user_id}).status_code == 404
    response = client.get(path, headers={"X-User": c.actor.user_id})
    assert response.status_code == 200
    assert response.json()["schema"] == "assistant-turn-events-v1"
    assert response.headers.get("cache-control") == "no-store"
    for query in ("limit=0", "limit=4", "after=" + "x" * 2049):
        assert (
            client.get(path + "?" + query, headers={"X-User": c.actor.user_id}).status_code == 422
        )


def test_cursor_prefix_changed_or_ahead_rejected(business):
    import base64

    c, conv, turn = setup(business)
    cursor = read(c, conv, turn)["cursor"]
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    for change in ({"seq": 3}, {"seq": True}, {"prefix": "0" * 64}, {"binding": "0" * 64}):
        changed = (
            base64.urlsafe_b64encode(json.dumps(decoded | change).encode()).decode().rstrip("=")
        )
        with pytest.raises(AccountingError) as failure:
            read(c, conv, turn, after=changed)
        assert failure.value.status == 409
    assert c.provider.calls == 0


def test_terminal_pagination_and_cancel_after_send_are_conservative(business):
    c, conv, turn = setup(business)
    c.provider.hook = lambda: c.service.cancel(
        c.actor, conv["id"], turn["id"], TurnCancel(reason="cancel while waiting")
    )
    c.worker.run_once()
    first = read(c, conv, turn, limit=1)
    second = read(c, conv, turn, after=first["cursor"], limit=1)
    final = read(c, conv, turn, after=second["cursor"], limit=1)
    assert first["terminal"] and first["has_more"] and second["has_more"]
    assert final["events"][0]["outcome_unknown"] is True and not final["has_more"]
    assert c.provider.calls == 1 and c.worker.run_once() is None
