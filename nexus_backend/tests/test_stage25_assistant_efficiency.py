"""Bounded live progress, snapshot reuse and current-scope human samples."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.seed import LOCAL_ACTOR
from app.assistant.models import TurnCreate
from app.assistant.snapshot_reads import AssistantSnapshotReads
from app.operations.efficiency import EfficiencyService
from app.operations.efficiency_models import SessionCommand, SessionStart
from test_accounting import add_actor, book, service  # noqa: F401
from test_business_api import auth
from test_intelligence_workflow import ai_case, create_event, extract, handoff  # noqa: F401
from test_stage15_conversations import SyntheticChatProvider
from test_stage15_conversations_api import application
from test_stage20_assistant_reads import service_and_thread, statements


def test_progress_is_bounded_private_and_reauthorizes_every_poll(ai_case):
    c = ai_case
    assistant, thread = service_and_thread(c, books=[c.book["id"]])
    actor = c.actors["accountant"]
    with statements(c.db) as sql:
        first = assistant.progress(actor, thread["id"])
    assert len(sql) <= 7
    assert first["active"] is False
    assert len(first["revision"]) == 64
    assert not any(key in str(first) for key in ("question", "reply", "storage_key", "lease_token"))
    assert assistant.progress(actor, thread["id"])["revision"] == first["revision"]
    assistant.provider = SyntheticChatProvider()
    assistant.enqueue(
        actor,
        thread["id"],
        TurnCreate(
            question="合成资料进度测试",
            expected_scope_version=1,
            idempotency_key=uuid4().hex,
        ),
    )
    queued = assistant.progress(actor, thread["id"])
    assert queued["active"] is True and queued["revision"] != first["revision"]
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?", (c.book["id"], actor.user_id)
        )
    with pytest.raises(AccountingError) as denied:
        assistant.progress(actor, thread["id"])
    assert denied.value.status == 404
    with pytest.raises(AccountingError):
        assistant.progress(c.manager, thread["id"])


def test_progress_http_auth_scope_and_bounds(tmp_path):
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
        path = f"/api/assistant/conversations/{thread['id']}/progress"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=employee).status_code == 404
        assert client.get(path, headers=accountant).status_code == 200
        assert (
            client.get(path, headers=accountant, params={"action_offset": 100001}).status_code
            == 422
        )


def test_snapshot_reuse_preserves_all_evidence_and_facts_with_fewer_roundtrips(ai_case):
    c = ai_case
    event = create_event(c)
    extract(c, event)
    handoff(c, event)
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        draft = conn.execute(
            "SELECT draft_id FROM business_mappings WHERE event_id=?", (event["id"],)
        ).fetchone()[0]
    assistant, thread = service_and_thread(
        c, books=[c.book["id"]], events=[event["id"]], draft_id=draft, intelligence=c.intelligence
    )
    actor = c.actors["accountant"]
    with statements(c.db) as baseline:
        with c.db.transaction(actor=actor) as conn:
            conversation = assistant._conversation(conn, actor, thread["id"])
            expected = assistant.snapshot_in_transaction(conn, actor, conversation)
    with statements(c.db) as optimized:
        actual = assistant.detail(actor, thread["id"])["context"]
    assert actual == expected
    assert len(optimized) < len(baseline) - 10
    assert len(optimized) <= 55
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in optimized)
    print("stage25_snapshot_queries", len(baseline), len(optimized))


def test_snapshot_replay_cursor_is_independent_and_native_functions_execute(service):
    with service.db.transaction() as conn:
        with statements(service.db):
            snapshot = AssistantSnapshotReads(conn)
            sql = "SELECT id,name FROM users ORDER BY id"
            first = snapshot.execute(sql)
            expected = conn.execute(sql).fetchall()
            assert first.fetchone() == expected[0]
            second = snapshot.execute(sql)
            assert second.fetchall() == expected
            assert first.fetchall() == expected[1:]
            assert second.fetchall() == []
            assert snapshot.execute(sql).fetchall() == expected
            assert len(snapshot._rows) == 1
            snapshot.execute("SELECT random()").fetchone()
            snapshot.execute("SELECT random()").fetchone()
            assert len(snapshot._rows) == 1
            calls = []

            def synthetic_volatile():
                calls.append(True)
                return len(calls)

            conn.create_function("synthetic_volatile", 0, synthetic_volatile)
            query = "SELECT synthetic_volatile() FROM users LIMIT 1"
            assert snapshot.execute(query).fetchone()[0] == 1
            assert snapshot.execute(query).fetchone()[0] == 2
            assert len(snapshot._rows) == 1
            with pytest.raises(RuntimeError):
                snapshot.execute("UPDATE users SET active=0")


def test_portfolio_pagination_unknown_samples_and_current_review_grants(service, book):
    clock = [datetime(2026, 10, 16, tzinfo=timezone.utc)]
    efficiency = EfficiencyService(service.db, clock=lambda: clock[0])
    first = efficiency.portfolio(LOCAL_ACTOR, "2026-10")
    assert first["pagination"]["total"] == 1
    assert first["items"][0]["credited_seconds"] is None
    assert first["items"][0]["session_count"] == 0
    reviewer = add_actor(
        service, user_id="stage25-reviewer", firm_id=LOCAL_ACTOR.firm_id, book_id=book
    )
    row = efficiency.start(reviewer, SessionStart(book_id=book, idempotency_key=uuid4().hex))
    clock[0] += timedelta(seconds=19)
    efficiency.command(
        reviewer,
        row["id"],
        SessionCommand(
            action="pause",
            expected_version=row["version"],
            idempotency_key=uuid4().hex,
        ),
    )
    second = service.create_book(LOCAL_ACTOR, BookCreate(name="虚构另一家", period="2026-10"))
    service.create_book(LOCAL_ACTOR, BookCreate(name="虚构下月", period="2026-11"))
    page = efficiency.portfolio(LOCAL_ACTOR, "2026-10", limit=1)
    assert page["pagination"]["total"] == 2 and page["pagination"]["has_more"]
    next_page = efficiency.portfolio(LOCAL_ACTOR, "2026-10", limit=1, offset=1)
    assert page["items"][0]["company_id"] != next_page["items"][0]["company_id"]
    visible = efficiency.portfolio(reviewer, "2026-10")
    assert visible["pagination"]["total"] == 1
    assert visible["items"][0]["credited_seconds"] == 19
    assert second["company_id"] not in str(visible)
    with service.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?", (book, reviewer.user_id)
        )
    assert efficiency.portfolio(reviewer, "2026-10")["pagination"]["total"] == 0
    with pytest.raises(AccountingError):
        efficiency.portfolio(reviewer, "2026-10", company_id=second["company_id"])
    assert efficiency.portfolio(LOCAL_ACTOR, "2026-12")["items"] == []
    for bad in ("2026-13", "2026-1", None, "2026-01' OR 1=1"):
        with pytest.raises(AccountingError):
            efficiency.portfolio(LOCAL_ACTOR, bad)


def test_portfolio_http_auth_and_no_timing_inference(tmp_path):
    app = application(tmp_path, SyntheticChatProvider())
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 7799)) as client:
        accountant, employee = auth(client, "accountant"), auth(client, "employee")
        path = "/api/efficiency/portfolio?period=2026-10"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=accountant).status_code == 200
        assert client.get(path, headers=employee).json()["items"] == []
        for query in ("period=2026-13", "period=2026-10&limit=101", "period=2026-10&offset=-1"):
            assert (
                client.get("/api/efficiency/portfolio?" + query, headers=accountant).status_code
                == 422
            )
