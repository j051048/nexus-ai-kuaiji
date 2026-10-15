"""Assistant workspace reads share live authorization and one read snapshot."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.assistant.actions import ActionPrepare, AssistantActionService
from app.assistant.models import TurnCreate
from app.assistant.worker import AssistantWorker
from app.jobs.service import QueueService
from test_business_api import auth
from test_intelligence_workflow import ai_case, create_event, extract, handoff  # noqa: F401
from test_stage15_assistant_actions import action_case, confirm, conversation, prepare  # noqa: F401
from test_stage15_conversations import SyntheticChatProvider, SyntheticMeter
from test_stage15_conversations_api import application
from test_stage20_assistant_reads import service_and_thread, statements


@contextmanager
def transactions(db):
    original, calls = db.transaction, []

    @contextmanager
    def tracked(*args, **kwargs):
        calls.append(kwargs)
        with original(*args, **kwargs) as conn:
            yield conn

    db.transaction = tracked
    try:
        yield calls
    finally:
        db.transaction = original


def workspace_parity(case):
    c = case
    event = create_event(c)
    extract(c, event)
    handoff(c, event)
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        mapping = conn.execute(
            "SELECT draft_id FROM business_mappings WHERE event_id=?", (event["id"],)
        ).fetchone()
    service, thread = service_and_thread(
        c,
        events=[event["id"]],
        books=[c.book["id"]],
        draft_id=mapping["draft_id"],
        intelligence=c.intelligence,
    )
    actions = AssistantActionService(c.db, service, QueueService(c.db, c.intelligence), c.ledger)
    actor, identifier = c.actors["accountant"], thread["id"]
    for _ in range(2):
        actions.prepare(
            actor,
            identifier,
            ActionPrepare(
                kind="bank_drafts",
                reason="明确范围的合成读取检查",
                idempotency_key=uuid4().hex,
            ),
        )
    with transactions(c.db) as legacy_transactions, statements(c.db) as legacy_sql:
        expected = {
            "detail": service.detail(actor, identifier),
            "messages": service.messages(actor, identifier, 100, 0),
            "actions": actions.list(actor, identifier, 20, 0),
        }
    with transactions(c.db) as grouped_transactions, statements(c.db) as grouped_sql:
        actual = service.workspace(actor, identifier, actions)
    assert actual == expected
    assert len(legacy_transactions) == 3
    assert len(grouped_transactions) == 1 and not grouped_transactions[0].get("write")
    assert len(grouped_sql) == len(legacy_sql) - 4
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in grouped_sql)
    assert len(actual["detail"]["context"]["evidence"]) == 4
    assert actual["detail"]["context"]["draft"]["status"] == "draft"
    assert len(actual["actions"]["plans"]) == 2
    print("assistant_workspace_queries", len(legacy_sql), len(grouped_sql))
    return service, actions, thread


def test_workspace_matches_three_reads_in_one_read_transaction(ai_case):
    workspace_parity(ai_case)


def test_workspace_preserves_saved_messages_citations_receipts_and_pagination(action_case):
    c = action_case
    event = create_event(c)
    thread = conversation(c, events=[event["id"]])
    provider = SyntheticChatProvider()
    c.assistant.provider = provider
    worker = AssistantWorker(c.assistant, provider, SyntheticMeter())
    actor = c.actors["employee"]
    for _ in range(2):
        turn = c.assistant.enqueue(
            actor,
            thread["id"],
            TurnCreate(
                question="解释当前保存的事项",
                expected_scope_version=1,
                idempotency_key=uuid4().hex,
            ),
        )["turn"]
        assert worker.run_once() == turn["id"]
    plan = prepare(c, thread)
    receipt = confirm(c, thread, plan)
    assert receipt["progress"]["total"] == 1
    calls_before = provider.calls
    for message_offset, action_offset in ((0, 0), (2, 1), (100, 100)):
        actual = c.assistant.workspace(
            actor,
            thread["id"],
            c.actions,
            message_limit=2,
            message_offset=message_offset,
            action_limit=1,
            action_offset=action_offset,
        )
        assert actual == {
            "detail": c.assistant.detail(actor, thread["id"]),
            "messages": c.assistant.messages(actor, thread["id"], 2, message_offset),
            "actions": c.actions.list(actor, thread["id"], 1, action_offset),
        }
        assert "storage_key" not in str(actual) and "lease_token" not in str(actual)
        if message_offset == 0:
            assert actual["messages"]["messages"][-1]["citations"]
            assert actual["actions"]["plans"][0]["receipt"]["progress"]["total"] == 1
    assert provider.calls == calls_before


@pytest.mark.parametrize("change", ["book", "company", "membership", "user"])
def test_warm_workspace_does_not_survive_revoke_or_other_user(ai_case, change):
    c = ai_case
    service, thread = service_and_thread(c, books=[c.book["id"]])
    actions = AssistantActionService(c.db, service, QueueService(c.db, c.intelligence), c.ledger)
    actor = c.actors["accountant"]
    assert service.workspace(actor, thread["id"], actions)["detail"]["context"]["books"]
    with pytest.raises(AccountingError) as other_user:
        service.workspace(c.manager, thread["id"], actions)
    assert other_user.value.status == 404
    sql, args = {
        "book": (
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (c.book["id"], actor.user_id),
        ),
        "company": (
            "UPDATE firm_clients SET status='suspended' WHERE firm_id=? AND company_id=?",
            (actor.firm_id, c.company["id"]),
        ),
        "membership": (
            "DELETE FROM memberships WHERE firm_id=? AND user_id=?",
            (actor.firm_id, actor.user_id),
        ),
        "user": ("UPDATE users SET active=0 WHERE id=?", (actor.user_id,)),
    }[change]
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(sql, args)
    with pytest.raises(AccountingError) as denied:
        service.workspace(actor, thread["id"], actions)
    assert denied.value.status in (403, 404)


def test_workspace_http_authentication_bounds_and_response_parity(tmp_path):
    app = application(tmp_path, SyntheticChatProvider())
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 7799)) as client:
        accountant, employee = auth(client, "accountant"), auth(client, "employee")
        book = client.get("/api/workbench", headers=accountant).json()["books"][0]
        thread = client.post(
            "/api/assistant/conversations",
            headers=accountant,
            json={
                "company_id": book["company_id"],
                "book_ids": [book["id"]],
                "idempotency_key": uuid4().hex,
            },
        ).json()["conversation"]
        path = f"/api/assistant/conversations/{thread['id']}"
        assert client.get(path + "/workspace").status_code == 401
        assert client.get(path + "/workspace", headers=employee).status_code == 404
        result = client.get(path + "/workspace", headers=accountant)
        assert result.status_code == 200, result.text
        assert result.json() == {
            "detail": client.get(path, headers=accountant).json(),
            "messages": client.get(path + "/messages?limit=100", headers=accountant).json(),
            "actions": client.get(path + "/actions", headers=accountant).json(),
        }
        for params in (
            {"message_limit": 101},
            {"message_offset": -1},
            {"action_limit": 21},
            {"action_offset": 100001},
        ):
            assert (
                client.get(path + "/workspace", params=params, headers=accountant).status_code
                == 422
            )
