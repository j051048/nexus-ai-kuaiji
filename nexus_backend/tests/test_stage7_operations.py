"""Proposal-only operational risks use real local queue states and safe summaries."""
from dataclasses import replace
import json

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import CompanyMemberGrant
from app.intelligence.provider import ProviderError
from app.operations.requests import AcknowledgeRequest, PolicyRequest
from app.operations.service import OperationsService
from test_intelligence_workflow import ai_case, create_event, extract, handoff
from test_model_cost_workflow import cost_case, policy
from test_stage7_proposal_queue import proposal_cost_case
from test_stage7_proposal_postgres import ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres, pg_ai, pg_queue, pg_stage7


@pytest.fixture
def ops_case(proposal_cost_case):
    case = proposal_cost_case
    policy(case, quota_enabled=False, failure_threshold=1, failure_window_minutes=60,
           backlog_threshold=1, backlog_age_minutes=1, queue_wait_minutes=1, worker_stale_seconds=30)
    return case


def ready(case):
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    assert task(case, result)["status"] == "queued"
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ocr_jobs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM proposal_jobs").fetchone()[0] == 1
    return result


def open_codes(value):
    return {alert["code"] for alert in value["alerts"] if alert["state"] == "open"}


def alert(value, code):
    return next(item for item in value["alerts"] if item["code"] == code)


def alert_count(case, value, code):
    # Public summaries deliberately expose only the message, while persisted
    # alert payloads retain the company-scoped count used by monitoring.
    with case.db.transaction(actor=case.actors["finance"]) as conn:
        row = conn.execute("SELECT payload_json FROM ops_alerts WHERE id=?", (alert(value, code)["id"],)).fetchone()
        return json.loads(row["payload_json"])["count"]


def task(case, result):
    return case.queue.proposals.get(case.actors["accountant"], result["automatic_job"]["id"])


def test_proposal_only_queue_wait_and_missing_worker_trigger_and_cancel_resolves(ops_case):
    case = ops_case
    result = ready(case)
    first = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert first["queue"]["queued"] == 1 and first["queue"]["running"] == 0
    assert "worker_unavailable" in open_codes(first) and "queue_wait" not in open_codes(first)
    assert alert_count(case, first, "worker_unavailable") == 1
    case.clock.advance(61)
    waiting = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert {"queue_wait", "worker_unavailable"} <= open_codes(waiting)
    assert alert_count(case, waiting, "queue_wait") == 1
    case.queue.proposals.cancel(case.actors["accountant"], result["automatic_job"]["id"], "会计确认该后台任务不再需要")
    cleared = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert cleared["queue"]["cancelled"] == 1 and cleared["queue"]["queued"] == 0
    assert alert(cleared, "queue_wait")["state"] == alert(cleared, "worker_unavailable")["state"] == "resolved"


def test_proposal_only_running_task_and_stale_worker_are_visible(ops_case):
    case = ops_case
    ready(case)
    with case.queue.worker_transaction() as conn:
        case.operations.maintenance(conn, "stage7-operation-heartbeat", 0, "idle")
    claimed = case.queue.claim("stage7-operation-heartbeat")
    assert claimed["job_kind"] == "proposal"
    first = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert first["queue"]["running"] == 1 and first["workers"]["available"]
    assert "worker_unavailable" not in open_codes(first)
    case.clock.advance(31)
    stale = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert stale["queue"]["running"] == 1 and not stale["workers"]["available"]
    assert "worker_unavailable" in open_codes(stale)


@pytest.mark.parametrize("code,expected_state", [("invalid_output", "failed"), ("timeout", "interrupted")])
def test_proposal_failure_and_unknown_response_count_in_task_failures(ops_case, code, expected_state):
    case = ops_case
    result = ready(case)
    case.provider.failure = ProviderError(code, "合成模型任务错误")
    assert case.worker.run_once()
    assert task(case, result)["status"] == expected_state
    view = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert view["queue"][expected_state] == view["queue"]["recent_failures"] == 1
    assert "task_failures" in open_codes(view) and alert_count(case, view, "task_failures") == 1
    case.clock.advance(3601)
    aged = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert aged["queue"]["recent_failures"] == 0 and alert(aged, "task_failures")["state"] == "resolved"


def attention(case):
    result = ready(case)
    # Remove the suggestion capability after preparing a current OCR result, then
    # retry the stale configuration through the authorized command surface.
    case.provider.settings = replace(case.provider.settings, journal_model="")
    assert case.worker.run_once() and task(case, result)["status"] == "stale"
    case.queue.proposals.retry(case.actors["accountant"], result["automatic_job"]["id"], "stage7-ops-no-journal", "明确按当前供应商配置重新检查")
    assert case.worker.run_once()
    assert task(case, result)["status"] == "needs_attention"
    assert case.provider.proposal_calls == 0
    return result


def test_proposal_attention_becomes_manual_backlog_without_snapshot_blocking_count(ops_case):
    case = ops_case
    result = attention(case)
    with case.db.transaction() as conn:
        snapshot = conn.execute("SELECT blocking_count FROM ops_exception_snapshots WHERE event_id=?", (result["event"]["id"],)).fetchone()
        assert snapshot["blocking_count"] == 0
    first = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert first["queue"]["needs_attention"] == 1 and first["queue"]["recent_failures"] == 0
    assert first["exceptions"]["events"] == first["exceptions"]["blocking"] == 1
    case.clock.advance(61)
    aged = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert "exception_backlog" in open_codes(aged)
    assert alert_count(case, aged, "exception_backlog") == 1
    assert "task_failures" not in open_codes(aged)


def test_proposal_attention_and_existing_snapshot_are_not_double_counted(ops_case):
    case = ops_case
    result = attention(case)
    with case.db.transaction(write=True) as conn:
        conn.execute("UPDATE ops_exception_snapshots SET blocking_count=3,warning_count=2,oldest_opened_at=?,context_fingerprint='stage7-existing-blockers' WHERE event_id=?", (case.queue.timestamp(-120), result["event"]["id"]))
    view = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert view["exceptions"]["events"] == 1
    assert view["exceptions"]["blocking"] == 3 and view["exceptions"]["warning"] == 2
    assert alert_count(case, view, "exception_backlog") == 1


def test_proposal_alert_refresh_retains_acknowledgement_version_when_risk_unchanged(ops_case):
    case = ops_case
    ready(case)
    case.clock.advance(61)
    view = case.operations.refresh(case.actors["finance"], case.company["id"])
    old = alert(view, "queue_wait")
    acknowledged = case.operations.acknowledge(case.actors["finance"], old["id"], AcknowledgeRequest(expected_version=old["version"], reason="会计已知晓，等待后台处理"))
    case.clock.advance(2)
    same = alert(case.operations.refresh(case.actors["finance"], case.company["id"]), "queue_wait")
    assert same["state"] == "acknowledged" and same["version"] == acknowledged["version"]


def test_proposal_operational_alerts_remain_company_scoped_and_sanitized(ops_case):
    case = ops_case
    result = ready(case)
    case.clock.advance(61)
    own = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert "worker_unavailable" in open_codes(own)
    case.directory.grant_company_member(case.manager, case.other_company["id"], CompanyMemberGrant(email="outsider@example.test", role="finance"))
    other = case.operations.refresh(case.actors["outsider"], case.other_company["id"])
    assert not other["alerts"] and other["queue"]["queued"] == other["exceptions"]["events"] == 0
    assert result["automatic_job"]["id"] not in str(other) and case.company["id"] not in str(other)
    with pytest.raises(AccountingError):
        case.operations.overview(case.actors["outsider"], case.company["id"])
    with pytest.raises(AccountingError):
        case.operations.refresh(case.actors["finance"], case.other_company["id"])
    for item in own["alerts"]:
        assert "message" in item
        assert "event_id" not in item and "draft_id" not in item and "attachment_id" not in item
    for actor in ("employee", "peer", "accountant"):
        with pytest.raises(AccountingError):
            case.operations.overview(case.actors[actor], case.company["id"])


requires_pg = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PostgreSQL 管理员、API 和独立 worker 测试连接")


@pytest.fixture
def pg_ops(pg_stage7):
    case = pg_stage7
    case.operations = OperationsService(case.db, case.intelligence._meter(), intelligence=case.intelligence)
    before = case.operations.policy(case.manager, case.company["id"])
    case.operations.policy(case.manager, case.company["id"], PolicyRequest(expected_version=before["version"], quota_enabled=False,
                          failure_threshold=1, failure_window_minutes=60, backlog_threshold=1, backlog_age_minutes=1,
                          queue_wait_minutes=1, worker_stale_seconds=30))
    return case


@requires_pg
def test_pg_proposal_only_pending_participates_in_queue_wait(pg_ops, monkeypatch):
    case = pg_ops
    event = create_event(case)
    extract(case, event)
    with case.db.transaction(actor=case.manager) as conn:
        old = conn.execute("SELECT to_char((clock_timestamp()-interval '120 seconds') AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')").fetchone()[0]
    # Model an already waiting task at creation, without modifying immutable
    # provenance after insertion or relying on sleeps across the two OS clocks.
    monkeypatch.setattr(case.queue, "database_timestamp", lambda _: old)
    result = handoff(case, event)
    view = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert view["queue"]["queued"] == 1 and "queue_wait" in open_codes(view)
    case.queue.proposals.cancel(case.actors["accountant"], result["automatic_job"]["id"], "真实PG已核对，取消等待任务")
    assert alert(case.operations.refresh(case.actors["finance"], case.company["id"]), "queue_wait")["state"] == "resolved"


@requires_pg
@pytest.mark.parametrize("code,status", [("invalid_output", "failed"), ("timeout", "interrupted")])
def test_pg_proposal_failure_and_uncertain_result_feed_monitor(pg_ops, code, status):
    case = pg_ops
    ready(case)
    case.provider.failure = ProviderError(code, "真实PG合成任务错误")
    assert case.worker.run_once()
    view = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert view["queue"][status] == 1 and view["queue"]["recent_failures"] == 1
    assert "task_failures" in open_codes(view)
    assert case.operations.overview(case.manager, case.other_company["id"])["queue"]["recent_failures"] == 0


@requires_pg
def test_pg_attention_manual_backlog_deduplicates_existing_snapshot(pg_ops):
    case = pg_ops
    event = create_event(case)
    extract(case, event)
    case.provider.settings = replace(case.provider.settings, journal_model="")
    result = handoff(case, event)
    assert case.worker.run_once() and task(case, result)["status"] == "needs_attention"
    first = case.operations.overview(case.actors["finance"], case.company["id"])
    assert first["queue"]["needs_attention"] == 1
    assert first["exceptions"]["events"] == first["exceptions"]["blocking"] == 1
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ops_exception_snapshots SET blocking_count=3,warning_count=2,oldest_opened_at=to_char((clock_timestamp()-interval '120 seconds') AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"'),context_fingerprint='stage7-pg-existing-risk' WHERE event_id=%s", (event["id"],))
    deduplicated = case.operations.refresh(case.actors["finance"], case.company["id"])
    assert deduplicated["exceptions"]["events"] == 1 and deduplicated["exceptions"]["blocking"] == 3 and deduplicated["exceptions"]["warning"] == 2
    assert "exception_backlog" in open_codes(deduplicated)
    with pytest.raises(AccountingError):
        case.operations.overview(case.actors["outsider"], case.company["id"])
