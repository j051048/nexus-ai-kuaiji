"""Dedicated chat liveness, bounded alerts and private company aggregates."""
from datetime import datetime, timedelta
import json

import pytest

from app.accounting.domain import AccountingError
from app.assistant.monitor import AssistantMonitor
from app.intelligence.provider import ProviderError
from app.jobs.requests import BatchRequest
from app.jobs.service import QueueService
from test_model_cost_workflow import ai_case, cost_case, policy
from test_stage15_assistant_meter import chat_case, question
from test_intelligence_workflow import create_event


def refresh(case):
    with case.db.transaction(write=True, actor=case.manager) as conn:
        case.operations._refresh_alerts(conn, case.manager.firm_id, case.company["id"])
    return case.operations.overview(case.manager, case.company["id"])


def open_codes(overview):
    return {row["code"] for row in overview["alerts"] if row["state"] != "resolved"}


def test_ocr_heartbeat_cannot_hide_chat_worker_unavailable(chat_case):
    c = chat_case
    policy(c, quota_enabled=False, queue_wait_minutes=1, worker_stale_seconds=30)
    actor, conversation, turn = question(c)
    c.clock.value = datetime.fromisoformat(turn["created_at"]) + timedelta(minutes=2)
    with c.db.transaction(write=True) as conn:
        c.operations.heartbeat(conn, "synthetic-ocr-only", 0, "idle")
    current = refresh(c)
    assert current["queue"]["queued"] == current["assistant"]["queue"]["queued"] == 1
    assert current["workers"]["processing_available"]
    assert not current["workers"]["available"] and not current["assistant"]["workers"]["available"]
    assert {"worker_unavailable", "queue_wait"} <= open_codes(current)
    assert turn["question"] not in json.dumps(current, ensure_ascii=False)
    monitor = AssistantMonitor(c.db, "synthetic-chat-only", c.operations)
    monitor.tick()
    current = c.operations.overview(c.manager, c.company["id"])
    assert current["workers"]["available"] and current["assistant"]["workers"]["available"]
    assert "worker_unavailable" not in open_codes(current)
    # OCR remains alive while only the chat worker has gone stale.
    c.clock.advance(31)
    with c.db.transaction(write=True) as conn:
        c.operations.heartbeat(conn, "synthetic-ocr-only", 0, "idle")
    assert "worker_unavailable" in open_codes(refresh(c))
    monitor.tick(state="stopped")
    assert not c.operations.overview(c.manager, c.company["id"])["assistant"]["workers"]["available"]


def test_chat_terminal_changes_dirty_metrics_and_failure_alerts(chat_case):
    c = chat_case
    policy(c, quota_enabled=False, failure_threshold=1)
    actor, conversation, turn = question(c)
    c.clock.value = datetime.fromisoformat(turn["created_at"]) + timedelta(seconds=10)
    monitor = AssistantMonitor(c.db, "synthetic-chat-monitor", c.operations)
    monitor.tick()
    with c.db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM ops_company_dirty WHERE firm_id=? AND company_id=?", (actor.firm_id, c.company["id"])).fetchone()
    c.chat_provider.failure = ProviderError("timeout", "合成超时")
    c.chat_worker.run_once()
    with c.db.transaction() as conn:
        assert conn.execute("SELECT 1 FROM ops_company_dirty WHERE firm_id=? AND company_id=?", (actor.firm_id, c.company["id"])).fetchone()
    monitor.tick()
    current = c.operations.overview(c.manager, c.company["id"])
    assert current["assistant"]["queue"]["interrupted"] == 1
    assert current["queue"]["recent_failures"] == 1 and "task_failures" in open_codes(current)
    with pytest.raises(AccountingError):
        c.operations.overview(c.actors["employee"], c.company["id"])
    with pytest.raises(AccountingError):
        c.operations.overview(c.manager, "unrelated-company")


def test_chat_heartbeat_cannot_hide_processing_worker_unavailable(chat_case):
    c = chat_case
    event = create_event(c)
    QueueService(c.db, c.intelligence).enqueue(c.actors["employee"], BatchRequest(company_id=c.company["id"],
        items=[{"event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"]}], idempotency_key="synthetic-chat-monitor-ocr"))
    AssistantMonitor(c.db, "synthetic-chat-alive", c.operations).tick()
    current = c.operations.overview(c.manager, c.company["id"])
    assert current["workers"]["assistant_available"] and not current["workers"]["processing_available"]
    assert not current["workers"]["available"] and "worker_unavailable" in open_codes(current)


def test_heartbeat_commits_even_when_incremental_refresh_fails(chat_case, monkeypatch):
    c = chat_case
    def failure(*args):
        raise RuntimeError("synthetic refresh failure")
    monkeypatch.setattr(c.operations, "refresh_pending", failure)
    monitor = AssistantMonitor(c.db, "synthetic-heartbeat-survives", c.operations)
    with pytest.raises(RuntimeError):
        monitor.tick(1, "active")
    with c.db.transaction() as conn:
        row = conn.execute("SELECT state,inflight_count FROM assistant_worker_heartbeats WHERE worker_id='synthetic-heartbeat-survives'").fetchone()
        assert row["state"] == "active" and row["inflight_count"] == 1
    for count, state in [(3, "active"), (True, "active"), (1, "idle"), (0, "invalid")]:
        with pytest.raises(ValueError):
            monitor.tick(count, state)
