"""Explicit work sessions, current ACL, idempotence and audited definitions."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate
from app.accounting.seed import LOCAL_ACTOR
from app.operations.efficiency import EfficiencyService, credited_seconds
from app.operations.efficiency_models import SessionStart, SessionCommand
from app.operations.efficiency_router import build_efficiency_router
from test_accounting import service, book, make_draft, add_actor
from test_business import business, create


def start(book_id, key=None, **changes):
    return SessionStart(book_id=book_id, idempotency_key=key or uuid4().hex, **changes)


def command(row, action, key=None):
    return SessionCommand(action=action, expected_version=row["version"], idempotency_key=key or uuid4().hex)


def test_explicit_time_caps_unattended_interval_and_pause_does_not_count(service, book):
    current = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    efficiency = EfficiencyService(service.db, clock=lambda: current[0])
    row = efficiency.start(LOCAL_ACTOR, start(book))
    assert row["credited_seconds"] == 0
    current[0] += timedelta(seconds=20)
    row = efficiency.command(LOCAL_ACTOR, row["id"], command(row, "heartbeat"))
    assert row["credited_seconds"] == 20
    current[0] += timedelta(hours=5)
    row = efficiency.command(LOCAL_ACTOR, row["id"], command(row, "pause"))
    assert row["credited_seconds"] == 80
    current[0] += timedelta(hours=4)
    row = efficiency.command(LOCAL_ACTOR, row["id"], command(row, "resume"))
    assert row["credited_seconds"] == 80
    current[0] += timedelta(seconds=15)
    row = efficiency.command(LOCAL_ACTOR, row["id"], command(row, "complete"))
    assert row["credited_seconds"] == 95 and row["state"] == "completed"
    assert efficiency.overview(LOCAL_ACTOR, book)["human"]["credited_seconds"] == 95


def test_replay_versions_and_parallel_session_cannot_inflate_time(service, book):
    current = [datetime(2026, 10, 12, tzinfo=timezone.utc)]
    efficiency = EfficiencyService(service.db, clock=lambda: current[0])
    request = start(book, "start-session")
    row = efficiency.start(LOCAL_ACTOR, request)
    assert efficiency.start(LOCAL_ACTOR, request) == row
    with pytest.raises(AccountingError, match="已有计时"):
        efficiency.start(LOCAL_ACTOR, start(book))
    current[0] += timedelta(seconds=20)
    request = command(row, "heartbeat", "heartbeat-replay")
    updated = efficiency.command(LOCAL_ACTOR, row["id"], request)
    current[0] += timedelta(seconds=40)
    assert efficiency.command(LOCAL_ACTOR, row["id"], request) == updated
    with pytest.raises(AccountingError, match="已改变"):
        efficiency.command(LOCAL_ACTOR, row["id"], command(row, "heartbeat"))
    with pytest.raises(AccountingError, match="不同操作"):
        efficiency.command(LOCAL_ACTOR, row["id"], command(updated, "pause", "heartbeat-replay"))
    assert efficiency.own(LOCAL_ACTOR)["sessions"][0]["credited_seconds"] == 20


def test_current_book_scope_and_creator_are_both_required(service, book):
    efficiency = EfficiencyService(service.db)
    # Revoke an explicitly assigned reviewer; owner demotion is now forbidden.
    creator = add_actor(service, user_id="efficiency-creator", firm_id=LOCAL_ACTOR.firm_id, role="reviewer", book_id=book)
    row = efficiency.start(creator, start(book))
    colleague = add_actor(service, user_id="efficiency-colleague", firm_id=LOCAL_ACTOR.firm_id, role="reviewer", book_id=book)
    with pytest.raises(AccountingError):
        efficiency.command(colleague, row["id"], command(row, "pause"))
    assert efficiency.own(colleague)["total"] == 0
    with service.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?", (book, creator.user_id))
    assert efficiency.own(creator)["total"] == 0
    with pytest.raises(AccountingError):
        efficiency.command(creator, row["id"], command(row, "pause"))


def test_employee_records_only_own_business_and_cannot_read_book_metrics(business):
    c = business
    efficiency = EfficiencyService(c.db)
    event = create(c)
    row = efficiency.start(c.actors["employee"], SessionStart(event_id=event["id"], activity="business_entry", idempotency_key=uuid4().hex))
    assert efficiency.own(c.actors["employee"])["total"] == 1
    for role in ("peer", "finance", "accountant"):
        with pytest.raises(AccountingError):
            efficiency.command(c.actors[role], row["id"], command(row, "pause"))
        with pytest.raises(AccountingError):
            efficiency.start(c.actors[role], SessionStart(event_id=event["id"], activity="business_entry", idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError):
        efficiency.overview(c.actors["employee"], c.book["id"])


def test_audited_review_metrics_do_not_claim_unknown_accuracy_or_work_savings(service, book):
    efficiency = EfficiencyService(service.db)
    empty = efficiency.overview(LOCAL_ACTOR, book)
    assert empty["review"]["reviewed_drafts"] == 0 and empty["human"]["credited_seconds"] == 0
    draft = make_draft(service, book)
    edited = service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(expected_version=draft["version"], description="更正摘要", lines=draft["lines"]))
    reviewer = add_actor(service, user_id="efficiency-independent-reviewer", firm_id=LOCAL_ACTOR.firm_id, role="reviewer", book_id=book)
    service.approve(reviewer, book, draft["id"], ApprovalRequest(expected_version=edited["version"]))
    value = efficiency.overview(LOCAL_ACTOR, book)
    assert value["review"] == {"reviewed_drafts": 1, "without_recorded_correction": 0, "with_recorded_correction": 1, "company_rule_covered": 0}
    assert value["human"]["credited_seconds"] == 0
    assert "不代表独立正确率" in value["definitions"]["without_recorded_correction"]


def test_clock_rewind_and_cap_are_deterministic():
    assert credited_seconds("2026-10-12T10:00:00+00:00", "2026-10-12T09:00:00+00:00") == 0
    assert credited_seconds("2026-10-12T10:00:00+00:00", "2026-10-13T10:00:00+00:00") == 60


def test_efficiency_http_auth_and_strict_commands(service, book):
    app = FastAPI()
    app.state.efficiency = EfficiencyService(service.db)
    def authenticate(authorization: str | None = Header(default=None)):
        if authorization != "Bearer efficiency-test":
            raise HTTPException(401)
        return LOCAL_ACTOR
    app.include_router(build_efficiency_router(authenticate))
    with TestClient(app) as client:
        assert client.get("/api/efficiency/sessions").status_code == 401
        headers = {"Authorization": "Bearer efficiency-test"}
        row = client.post("/api/efficiency/sessions", headers=headers, json={"book_id": book, "activity": "review", "idempotency_key": "http-start"}).json()
        assert client.post(f"/api/efficiency/sessions/{row['id']}/commands", headers=headers, json={"action": "heartbeat", "expected_version": "1", "idempotency_key": "bad", "credited_seconds": 100}).status_code == 422
        assert client.get(f"/api/books/{book}/efficiency", headers=headers).json()["human"]["measurement"] == "explicit_sessions"
