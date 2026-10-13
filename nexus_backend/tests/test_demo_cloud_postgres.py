"""Dedicated PostgreSQL API/worker roles with independent remote blob clients.

Storage and model responses are explicitly synthetic. Actual PostgreSQL RLS,
queue claims, source versions, draft adoption, review and export remain active.
The test does not connect to a cloud account or create a bucket.
"""
from uuid import uuid4
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.auth import AuthSettings, Identity
from app.intelligence.service import IntelligenceService
from app.jobs.requests import BatchRequest
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from app.main import create_app
from test_intelligence_workflow import create_event, handoff
from test_queue_worker_postgres import (
    ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres,
    pg_ai, pg_queue,
)
from test_shared_original_storage import SyntheticStorage

needs_pg = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or not WORKER_URL,
    reason="需要专用 PostgreSQL 管理员、受限 API 与独立 worker 测试连接",
)


@needs_pg
def test_pg_all_tables_force_rls_and_internal_schedule_has_no_direct_runtime_access(pg_queue):
    case = pg_queue
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("""SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='accounting' AND c.relkind IN ('r','p')
            AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)""").fetchone()[0] == 0
        assert not conn.execute("SELECT has_table_privilege(current_user,'accounting.assistant_company_schedule','SELECT,INSERT,UPDATE,DELETE')").fetchone()[0]
    with case.worker.queue.worker_transaction() as conn:
        assert not conn.execute("SELECT has_table_privilege(current_user,'accounting.assistant_company_schedule','SELECT,INSERT,UPDATE,DELETE')").fetchone()[0]
        assert conn.execute("SELECT accounting.assistant_recover()").fetchone()[0] >= 0


@needs_pg
def test_pg_api_lifespan_remote_factory_private_download_and_public_bucket_startup_block(cloud_pg, monkeypatch):
    case = cloud_pg
    event = create_event(case)
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    monkeypatch.setenv("ACCOUNTING_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("ACCOUNTING_STORAGE_SUPABASE_URL", "https://synthetic-storage.example")
    monkeypatch.setenv("ACCOUNTING_STORAGE_BUCKET", "accounting-originals")
    monkeypatch.setenv("ACCOUNTING_STORAGE_SERVER_KEY", "sb_secret_synthetic_test_only")
    monkeypatch.delenv("ACCOUNTING_ATTACHMENT_ROOT", raising=False)
    from app.storage.factory import create_attachment_store
    monkeypatch.setattr("app.main.create_attachment_store", lambda local_root, settings: create_attachment_store(
        settings=settings, client=httpx.Client(transport=httpx.MockTransport(case.cloud.handle))))
    identities = {role: Identity(actor.user_id, None, "合成云端验证人员") for role, actor in case.actors.items()}
    app = create_app(APP_URL, seed=False, auth_settings=AuthSettings(mode="supabase",
                     supabase_url="https://isolated-auth.example.test", publishable_key="sb_publishable_test_only"),
                     authenticator=SimpleNamespace(authenticate=lambda token: identities[token]),
                     intelligence_provider=case.provider, queue_worker_enabled=False)
    path = "/api/business/attachments/" + event["attachments"][0]["id"] + "/content"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert app.state.attachment_store.root is None
        assert client.get("/api/health").json() == {"status": "ok", "storage": "postgres", "auth": "supabase"}
        result = client.get(path, headers={"Authorization": "Bearer employee", "X-Firm-Id": case.manager.firm_id})
        assert result.status_code == 200 and "private" in result.headers["cache-control"]
        calls = len(case.cloud.requests)
        denied = client.get(path, headers={"Authorization": "Bearer outsider", "X-Firm-Id": case.manager.firm_id})
        assert denied.status_code in {403, 404} and len(case.cloud.requests) == calls
        assert "sb_secret_" not in str(client.get("/api/auth/config").json())
    case.cloud.public = True
    with pytest.raises(AccountingError, match="私有"):
        with TestClient(app, base_url="http://127.0.0.1"):
            pass


@pytest.fixture
def cloud_pg(pg_queue):
    case, cloud = pg_queue, SyntheticStorage()
    api_store, worker_store = cloud.store(), cloud.store()
    worker_db = case.worker.queue.db
    case.storage = api_store
    case.intelligence = IntelligenceService(case.db, api_store, case.provider)
    case.queue = QueueService(case.db, case.intelligence)
    case.business.intelligence = case.intelligence
    case.business.proposal_queue = case.queue.proposals
    case.ledger.intelligence = case.intelligence
    worker_ai = IntelligenceService(worker_db, worker_store, case.provider)
    case.worker = QueueWorker(QueueService(worker_db, worker_ai), worker_id="shared-storage-" + uuid4().hex)
    case.cloud, case.worker_store = cloud, worker_store
    assert api_store.root is worker_store.root is None
    assert api_store.backend is not worker_store.backend
    assert api_store.backend._client is not worker_store.backend._client
    try:
        yield case
    finally:
        # Preserve immutable test history, and cancel only this synthetic firm.
        # OCR cleanup belongs to the inherited pg_queue fixture.
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.proposal_attempts SET phase='finished',status='cancelled',finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status='running'", (case.manager.firm_id,))
            conn.execute("UPDATE accounting.proposal_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN('waiting','queued','running','retry_wait')", (case.manager.firm_id,))


def batch(case, event):
    return BatchRequest(company_id=case.company["id"], items=[{
        "event_id": event["id"], "attachment_id": event["attachments"][0]["id"],
        "expected_version": event["version"],
    }], idempotency_key=uuid4().hex)


@needs_pg
def test_pg_remote_original_api_worker_auto_draft_requires_review_before_export(cloud_pg):
    case = cloud_pg
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        api_login = conn.execute("SELECT session_user").fetchone()[0]
    with case.worker.queue.worker_transaction() as conn:
        worker_login = conn.execute("SELECT session_user").fetchone()[0]
        assert conn.execute("SELECT current_user").fetchone()[0] == "accounting_worker"
    assert api_login != worker_login
    # create_event uses the actual API-role domain checks and upload metadata
    # transaction. Only bytes are stored in the isolated synthetic cloud.
    event = create_event(case)
    assert len(case.cloud.blobs) == 1 and case.provider.extract_calls == 0
    receipt = handoff(case, event)
    automatic = receipt["automatic_job"]
    task = case.queue.proposals.get(case.actors["accountant"], automatic["id"])
    assert task["status"] == "waiting"
    assert case.worker.run_once()  # Remote original -> OCR under submitter scope.
    assert case.worker.run_once()  # Validated evidence -> adopted draft.
    task = case.queue.proposals.get(case.actors["accountant"], automatic["id"])
    value = case.ledger.get_draft(case.actors["accountant"], receipt["book_id"], receipt["draft_id"])
    assert task["status"] == "succeeded", task
    assert task["current_run_id"] and value["version"] == 2 and value["status"] == "draft"
    assert not value["issues"]
    assert case.provider.extract_calls == case.provider.proposal_calls == 1
    assert any(request.method == "GET" and "/object/" in request.url.path for request in case.cloud.requests)
    assert not case.worker.run_once()
    with pytest.raises(AccountingError):
        case.ledger.export(case.actors["accountant"], receipt["book_id"], ExportRequest(draft_ids=[value["id"]], idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError):
        case.ledger.approve(case.actors["finance"], receipt["book_id"], value["id"], ApprovalRequest(expected_version=2))
    case.ledger.approve(case.actors["accountant"], receipt["book_id"], value["id"], ApprovalRequest(expected_version=2))
    exported = case.ledger.export(case.actors["accountant"], receipt["book_id"], ExportRequest(draft_ids=[value["id"]], idempotency_key=uuid4().hex))
    assert exported["count"] == 1 and "0.30" in exported["csv"]
    assert not any(request.method in {"DELETE", "PUT"} or "/public/" in request.url.path for request in case.cloud.requests)


@needs_pg
@pytest.mark.parametrize("principal", ["outsider", "peer", "revoked"])
def test_pg_remote_original_scope_rejected_before_storage_call(cloud_pg, principal):
    case = cloud_pg
    event = create_event(case)
    actor = case.actors["employee" if principal == "revoked" else principal]
    if principal == "revoked":
        case.directory.revoke_company_member(case.manager, case.company["id"], actor.user_id)
    before = len(case.cloud.requests)
    with pytest.raises(AccountingError):
        # Exact API download sequence: owned metadata first, blob second.
        case.storage.read(case.business.download_metadata(actor, event["attachments"][0]["id"]))
    with pytest.raises(AccountingError):
        case.queue.enqueue(actor, batch(case, event))
    assert len(case.cloud.requests) == before
    assert case.provider.extract_calls == case.provider.proposal_calls == 0


@needs_pg
def test_pg_revoked_queued_executor_cannot_send_remote_original(cloud_pg):
    case = cloud_pg
    event = create_event(case)
    queued = case.queue.enqueue(case.actors["employee"], batch(case, event))
    case.directory.revoke_company_member(case.manager, case.company["id"], case.actors["employee"].user_id)
    before = len(case.cloud.requests)
    assert case.worker.run_once()
    assert len(case.cloud.requests) == before and case.provider.extract_calls == 0
    with case.psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute("SELECT status,current_run_id FROM accounting.ocr_jobs WHERE batch_id=%s", (queued["id"],)).fetchone()
    assert row[0] == "stale"


def test_production_configuration_accepts_shared_storage_without_attachment_root(monkeypatch):
    # Constructor checks run before lifespan. This does not claim a successful
    # external connection, bucket probe, migration or production startup.
    for key in ("ACCOUNTING_ATTACHMENT_ROOT", "APP_ENV", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(key, raising=False)
    values = {
        "ACCOUNTING_ENV": "production",
        "ACCOUNTING_ALLOWED_HOSTS": "synthetic-api.example",
        "ACCOUNTING_ALLOWED_ORIGINS": "https://synthetic-app.example",
        "ACCOUNTING_STORAGE_BACKEND": "supabase",
        "ACCOUNTING_STORAGE_SUPABASE_URL": "https://synthetic-project.supabase.co",
        "ACCOUNTING_STORAGE_BUCKET": "accounting-originals",
        "ACCOUNTING_STORAGE_SERVER_KEY": "sb_secret_synthetic_fixture_only",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    app = create_app("postgresql://synthetic_runtime@db.example/accounting?sslmode=require", seed=False,
                     auth_settings=AuthSettings(mode="supabase", supabase_url="https://synthetic-project.supabase.co", publishable_key="sb_publishable_synthetic_fixture_only"),
                     queue_worker_enabled=False)
    assert app is not None
