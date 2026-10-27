"""Actual dedicated worker login runs OCR under the live submitting actor scope."""
from contextlib import closing
import os

import pytest

from app.accounting.database import Database
from app.accounting.models import ApprovalRequest, ExportRequest
from app.intelligence.service import IntelligenceService
from app.jobs.requests import BatchRequest
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from app.auth import AuthSettings
from app.main import create_app
from fastapi.testclient import TestClient
from test_intelligence_workflow_postgres import pg_ai, business_case, business_postgres, ADMIN_URL, APP_URL
from test_intelligence_workflow import create_event, handoff, draft, propose, review_original

WORKER_URL = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要真实PG管理员、API和独立worker测试连接")


@pytest.fixture
def pg_queue(pg_ai):
    case = pg_ai
    case.queue = QueueService(case.db, case.intelligence)
    worker_db = Database(WORKER_URL)
    worker_intelligence = IntelligenceService(worker_db, case.storage, case.provider)
    case.worker = QueueWorker(QueueService(worker_db, worker_intelligence), worker_id="domain-worker")
    yield case
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ocr_attempts SET phase='finished',status='cancelled',finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status='running'", (case.manager.firm_id,))
        conn.execute("UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('queued','running','retry_wait')", (case.manager.firm_id,))


def enqueue(case, event):
    return case.queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[{"event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"]}], idempotency_key="domain-queue-batch"))


def test_actual_pg_worker_runs_complete_scoped_workflow(pg_queue):
    case = pg_queue
    event = create_event(case)
    batch = enqueue(case, event)
    assert batch["progress"]["pending"] == 1 and case.provider.extract_calls == 0
    assert case.worker.run_once()
    completed = case.queue.get_batch(case.actors["employee"], batch["id"])
    assert completed["progress"]["succeeded"] == 1, completed
    assert case.provider.extract_calls == 1
    assert completed["jobs"][0]["attempts"][0]["status"] == "succeeded"
    assert not case.worker.run_once()
    receipt = handoff(case, event)
    proposal = propose(case, receipt["draft_id"])
    applied = case.intelligence.apply(case.actors["accountant"], proposal["id"], proposal["input_version"])
    assert not applied["issues"]
    case.ledger.approve(case.manager, case.book["id"], receipt["draft_id"], ApprovalRequest(expected_version=applied["version"]))
    exported = case.ledger.export(case.actors["accountant"], case.book["id"], ExportRequest(draft_ids=[receipt["draft_id"]], idempotency_key="pg-worker-export"))
    assert exported["count"] == 1 and "0.30" in exported["csv"]


def test_api_login_cannot_construct_global_dispatcher(pg_queue):
    with pytest.raises(RuntimeError, match="独立"):
        QueueWorker(pg_queue.queue)


def test_worker_login_cannot_be_reused_by_api_lifespan(pg_queue, tmp_path):
    app = create_app(WORKER_URL, seed=False, auth_settings=AuthSettings(mode="supabase", supabase_url="https://isolated-auth.example.test", publishable_key="sb_publishable_test_only"), attachment_root=tmp_path / "api-private", queue_worker_enabled=False)
    with pytest.raises(RuntimeError, match="API与后台"):
        with TestClient(app, base_url="http://127.0.0.1"):
            pass


@pytest.mark.parametrize("when", ["before_send", "during_send"])
def test_pg_live_grant_revocation_stops_queue_without_publishing(pg_queue, when):
    case = pg_queue
    event = create_event(case)
    batch = enqueue(case, event)
    revoke = lambda: case.directory.revoke_company_member(case.manager, case.company["id"], case.actors["employee"].user_id)
    if when == "before_send":
        revoke()
    else:
        case.provider.extract_hook = revoke
    assert case.worker.run_once()
    with case.psycopg.connect(ADMIN_URL) as conn:
        job = conn.execute("SELECT status,current_run_id FROM accounting.ocr_jobs WHERE batch_id=%s", (batch["id"],)).fetchone()
        if job[1]:
            run = conn.execute("SELECT status,result_json FROM accounting.ai_runs WHERE id=%s", (job[1],)).fetchone()
            assert run[0] == "stale" and run[1] is None
    assert job[0] == "stale"
    assert case.provider.extract_calls == (0 if when == "before_send" else 1)


def test_pg_cancel_during_model_call_fences_late_result(pg_queue):
    case = pg_queue
    event = create_event(case)
    batch = enqueue(case, event)
    case.provider.extract_hook = lambda: case.queue.cancel_job(case.actors["finance"], batch["jobs"][0]["id"], "已核对，由独立财务取消重复处理")
    assert case.worker.run_once()
    result = case.queue.get_batch(case.actors["employee"], batch["id"])
    assert result["jobs"][0]["status"] == "cancelled", result
    with case.psycopg.connect(ADMIN_URL) as conn:
        run = conn.execute("SELECT status,result_json FROM accounting.ai_runs WHERE event_id=%s", (event["id"],)).fetchone()
    assert run[0] == "stale" and run[1] is None


def test_pending_queue_cannot_be_explained_away_to_approve(pg_queue):
    case = pg_queue
    event = create_event(case)
    receipt = handoff(case, event)
    current = case.business.get_event(case.actors["employee"], event["id"])
    batch = enqueue(case, current)
    issues = draft(case, receipt["draft_id"])["issues"]
    assert any("后台" in issue for issue in issues)
    case.intelligence.reconcile(case.actors["accountant"], event["id"])
    finding = next(item for item in case.intelligence.detail(case.actors["accountant"], event["id"])["findings"] if item["payload"].get("non_overridable"))
    with pytest.raises(Exception, match="不能用说明"):
        case.intelligence.resolve(case.actors["accountant"], finding["id"], "仍在处理但想直接审核")
    assert case.worker.run_once()
    review_original(case, event)
    assert not draft(case, receipt["draft_id"])["issues"]
