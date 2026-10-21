"""Stage 5 real database/worker boundaries with explicit synthetic model responses."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, CompanyCreate, CompanyMemberGrant, ExportRequest
from app.accounting.service import fingerprint
from app.business.models import BusinessEventCreate, BusinessEventUpdate
from app.intelligence.provider import AISettings, ProviderError
from app.intelligence.documents import prepare_document
from app.jobs.requests import BatchItem, BatchRequest
from app.jobs.service import QueueService
from app.jobs.worker import QueueExecution, QueueWorker
from app.main import create_app
from test_intelligence_workflow import (
    FakeProvider, ai_case, create_event, draft, extract, handoff, http_auth,
    http_create, http_handoff, invoice_document, propose,
)


class TestClock:
    __test__ = False

    def __init__(self):
        self.value = datetime(2026, 10, 12, 1, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def queue_case(ai_case):
    clock = TestClock()
    queue = QueueService(ai_case.db, ai_case.intelligence, clock=clock)
    worker = QueueWorker(queue, worker_id="synthetic-worker-one", concurrency=2, lease_seconds=180)
    ai_case.ledger.intelligence = ai_case.intelligence
    ai_case.queue, ai_case.worker, ai_case.clock = queue, worker, clock
    return ai_case


def request(case, events, key="batch-synthetic-test-key", company_id=None):
    return BatchRequest(company_id=company_id or case.company["id"], items=[
        BatchItem(event_id=event["id"], attachment_id=event["attachments"][0]["id"], expected_version=event["version"])
        for event in events
    ], idempotency_key=key)


def enqueue(case, events, actor="employee", key="batch-synthetic-test-key", company_id=None):
    return case.queue.enqueue(case.actors[actor], request(case, events, key, company_id))


def job(case, batch_id, actor="employee"):
    return case.queue.get_batch(case.actors[actor], batch_id)["jobs"][0]


def raw_counts(case):
    with case.db.transaction() as conn:
        return {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("ocr_batches", "ocr_jobs", "ocr_attempts", "ai_runs")}


def create_other_event(case, company=None):
    company = company or case.other_company
    case.intelligence.company_settings(case.manager, company["id"], enabled=True)
    event = case.business.create(case.actors["outsider"], BusinessEventCreate(company_id=company["id"], template_id="procurement_expense", payload={
        "occurred_on": "2026-10-03", "description": "第二企业合成采购", "counterparty": "耗材商店",
        "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office",
    }))
    image = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(image, "PNG")
    attachment = case.storage.put("第二企业合成发票.png", "image/png", image.getvalue())
    return case.business.add_attachment(case.actors["outsider"], event["id"], event["version"], attachment)


def test_enqueue_is_persistent_and_does_not_call_model_or_change_business(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    assert batch["status"] == "queued" and batch["progress"]["pending"] == 1
    assert batch["progress"]["total"] == 1 and batch["progress"]["percent"] == 0
    assert batch["jobs"][0]["attempt_count"] == 0
    assert case.provider.extract_calls == 0
    assert raw_counts(case) == {"ocr_batches": 1, "ocr_jobs": 1, "ocr_attempts": 0, "ai_runs": 0}
    assert case.business.get_event(case.actors["employee"], event["id"])["payload"] == event["payload"]
    restarted = QueueService(case.db, case.intelligence, clock=case.clock)
    assert restarted.get_batch(case.actors["employee"], batch["id"])["jobs"][0]["id"] == batch["jobs"][0]["id"]


def test_enqueue_same_key_and_reordered_inputs_is_one_batch_and_no_extra_calls(queue_case):
    case = queue_case
    events = [create_event(case), create_event(case)]
    original = enqueue(case, events)
    repeat = enqueue(case, list(reversed(events)))
    assert original["id"] == repeat["id"]
    assert {row["id"] for row in original["jobs"]} == {row["id"] for row in repeat["jobs"]}
    assert raw_counts(case)["ocr_jobs"] == 2 and case.provider.extract_calls == 0
    with pytest.raises(AccountingError) as conflict:
        enqueue(case, events[:1])
    assert conflict.value.status == 409 and raw_counts(case)["ocr_batches"] == 1


def test_active_attachment_cannot_be_submitted_again_with_a_different_key(queue_case):
    case = queue_case
    event = create_event(case)
    original = enqueue(case, [event])
    with pytest.raises(AccountingError) as conflict:
        enqueue(case, [event], key="different-batch-key")
    assert conflict.value.status == 409 and raw_counts(case)["ocr_jobs"] == 1
    assert case.queue.get_batch(case.actors["employee"], original["id"])["progress"]["pending"] == 1


@pytest.mark.parametrize("fault", ["peer", "attachment", "version", "duplicate"])
def test_invalid_item_rejects_whole_batch_atomically(queue_case, fault):
    case = queue_case
    own, peer = create_event(case), create_event(case, actor="peer")
    body = request(case, [own])
    if fault == "peer":
        body.items.append(BatchItem(event_id=peer["id"], attachment_id=peer["attachments"][0]["id"], expected_version=peer["version"]))
    elif fault == "attachment":
        body.items.append(BatchItem(event_id=own["id"], attachment_id=peer["attachments"][0]["id"], expected_version=own["version"]))
    elif fault == "version":
        second = create_event(case)
        body.items.append(BatchItem(event_id=second["id"], attachment_id=second["attachments"][0]["id"], expected_version=second["version"] + 1))
    else:
        body.items.append(body.items[0])
    with pytest.raises(AccountingError):
        case.queue.enqueue(case.actors["employee"], body)
    assert raw_counts(case) == {"ocr_batches": 0, "ocr_jobs": 0, "ocr_attempts": 0, "ai_runs": 0}
    assert case.provider.extract_calls == 0


def test_authorized_cross_company_items_still_cannot_share_batch(queue_case):
    case = queue_case
    own, other = create_event(case), create_other_event(case)
    case.directory.grant_company_member(case.manager, case.other_company["id"], CompanyMemberGrant(email="finance@example.test", role="finance"))
    with pytest.raises(AccountingError) as denied:
        enqueue(case, [own, other], actor="finance")
    assert denied.value.status == 409 and raw_counts(case)["ocr_jobs"] == 0
    assert case.provider.extract_calls == 0


def test_disabled_company_and_unconfigured_supplier_enqueue_no_work(queue_case):
    case = queue_case
    event = create_event(case)
    case.intelligence.company_settings(case.manager, case.company["id"], enabled=False)
    with pytest.raises(AccountingError):
        enqueue(case, [event])
    case.intelligence.company_settings(case.manager, case.company["id"], enabled=True)
    case.provider.settings = AISettings()
    with pytest.raises(ProviderError):
        enqueue(case, [event])
    assert raw_counts(case)["ocr_jobs"] == 0 and case.provider.extract_calls == 0


def test_batch_scope_filters_progress_and_never_leaks_peer_count_or_names(queue_case):
    case = queue_case
    own = create_event(case, description="员工本人事项")
    peer = create_event(case, actor="peer", description="绝不能泄露的同事事项")
    full = enqueue(case, [own, peer], actor="finance")
    employee = case.queue.get_batch(case.actors["employee"], full["id"])
    assert employee["progress"]["total"] == 1
    assert [row["event_id"] for row in employee["jobs"]] == [own["id"]]
    assert peer["id"] not in str(employee) and "绝不能泄露" not in str(employee)
    cancelled = case.queue.cancel_job(case.actors["employee"], employee["jobs"][0]["id"], "本人重复提交，人工取消")
    assert cancelled["status"] == "cancelled" and cancelled["progress"]["percent"] == 100
    finance = case.queue.get_batch(case.actors["finance"], full["id"])
    assert finance["progress"]["total"] == 2 and finance["progress"]["percent"] == 50
    listed = case.queue.list_batches(case.actors["employee"])
    assert listed["total"] == 1 and listed["batches"][0]["progress"]["total"] == 1
    assert peer["id"] not in str(listed)
    with pytest.raises(AccountingError) as denied:
        case.queue.get_batch(case.actors["outsider"], full["id"])
    assert denied.value.status == 404
    assert case.queue.list_batches(case.actors["outsider"])["total"] == 0


def test_queue_public_progress_never_exposes_storage_secret_or_lease_tokens(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    claimed = case.queue.claim("secret-internal-worker-name", 180, 2)
    assert claimed and claimed["lease_token"]
    public = case.queue.get_batch(case.actors["employee"], batch["id"])
    assert claimed["lease_token"] not in str(public)
    assert "secret-internal-worker-name" not in str(public)
    assert "test-only-not-a-secret" not in str(public)
    assert "storage_key" not in str(public) and "configuration_fingerprint" not in str(public)
    assert public["jobs"][0]["attempts"][0]["phase"] == "claimed"


def test_worker_success_records_result_and_progress_without_auto_approval(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    assert case.worker.run_once()
    finished = case.queue.get_batch(case.actors["employee"], batch["id"])
    assert finished["status"] == "completed" and finished["progress"]["succeeded"] == 1
    assert finished["progress"]["percent"] == 100
    assert finished["jobs"][0]["attempt_count"] == 1
    assert finished["jobs"][0]["attempts"][0]["status"] == "succeeded"
    assert case.provider.extract_calls == 1 and not case.worker.run_once()
    result = case.intelligence.detail(case.actors["employee"], event["id"])
    assert result["runs"][0]["status"] == "succeeded"
    assert result["runs"][0]["result"]["documents"][0]["gross_amount"] == "0.30"
    assert case.business.get_event(case.actors["employee"], event["id"])["status"] == "draft"


@pytest.mark.parametrize("change", ["revoked", "disabled", "version", "provider"])
def test_worker_rechecks_scope_and_inputs_before_any_external_call(queue_case, change):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    if change == "revoked":
        case.directory.revoke_company_member(case.manager, case.company["id"], "employee")
    elif change == "disabled":
        case.intelligence.company_settings(case.manager, case.company["id"], enabled=False)
    elif change == "version":
        case.business.update(case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload={**event["payload"], "amount": "0.40"}))
    else:
        case.provider.settings = AISettings(base_url="https://another-supplier.example.invalid/v1", api_key="synthetic-another-key", vision_model="synthetic-vision", journal_model="synthetic-journal")
    assert case.worker.run_once()
    finance = case.queue.get_batch(case.actors["finance"], batch["id"])
    assert finance["jobs"][0]["status"] == "stale" and case.provider.extract_calls == 0
    if change == "revoked":
        with pytest.raises(AccountingError) as denied:
            case.queue.get_batch(case.actors["employee"], batch["id"])
        assert denied.value.status == 404


def test_manual_retry_is_idempotent_keeps_history_and_uses_new_model_run(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    case.provider.failure = ProviderError("schema_invalid", "合成字段不合格")
    assert case.worker.run_once()
    failed = job(case, batch["id"])
    assert failed["status"] == "failed" and failed["attempt_count"] == 1
    with case.db.transaction() as conn:
        first_run = conn.execute("SELECT current_run_id FROM ocr_jobs WHERE id=?", (failed["id"],)).fetchone()[0]
    case.provider.failure = None
    retry = case.queue.retry_job(case.actors["employee"], failed["id"], "manual-retry-one-key")
    repeat = case.queue.retry_job(case.actors["employee"], failed["id"], "manual-retry-one-key")
    assert retry["id"] == repeat["id"] and job(case, batch["id"])["attempt_count"] == 1
    assert case.worker.run_once()
    final = job(case, batch["id"])
    assert final["status"] == "succeeded" and final["attempt_count"] == 2
    assert [row["status"] for row in final["attempts"]] == ["failed", "succeeded"]
    with case.db.transaction() as conn:
        last_run = conn.execute("SELECT current_run_id FROM ocr_jobs WHERE id=?", (failed["id"],)).fetchone()[0]
        retries = conn.execute("SELECT COUNT(*) FROM ocr_retries WHERE job_id=?", (failed["id"],)).fetchone()[0]
    assert first_run != last_run and retries == 1 and case.provider.extract_calls == 2
    assert case.queue.retry_job(case.actors["employee"], failed["id"], "manual-retry-one-key")["progress"]["succeeded"] == 1
    assert not case.worker.run_once() and case.provider.extract_calls == 2


def test_crash_before_sending_recovers_without_duplicating_a_supplier_call(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    claimed = case.queue.claim("crashed-before-send-worker", 180, 2)
    assert claimed and case.provider.extract_calls == 0
    case.clock.advance(181)
    restarted = QueueWorker(case.queue, worker_id="restarted-worker", concurrency=2, lease_seconds=180)
    assert restarted.recover() == 1
    assert job(case, batch["id"])["status"] == "retry_wait"
    assert restarted.run_once()
    assert job(case, batch["id"])["status"] == "succeeded"
    assert case.provider.extract_calls == 1 and job(case, batch["id"])["attempt_count"] == 2


def test_two_workers_cannot_claim_same_task_or_parallelize_one_company(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case), create_event(case)])
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda name: case.queue.claim(name, 180, 2), ("worker-a", "worker-b")))
    claimed = [row for row in results if row]
    assert len(claimed) == 1
    assert len({row["id"] for row in claimed}) == 1
    current = case.queue.get_batch(case.actors["employee"], batch["id"])
    assert current["progress"]["running"] == 1 and current["progress"]["pending"] == 1
    assert raw_counts(case)["ocr_attempts"] == 1 and case.provider.extract_calls == 0


def test_two_company_dispatch_round_robin_gives_small_queue_a_turn(queue_case):
    case = queue_case
    first = enqueue(case, [create_event(case), create_event(case), create_event(case)])
    second_event = create_other_event(case)
    second = enqueue(case, [second_event], actor="outsider", key="second-company-batch-key", company_id=case.other_company["id"])
    assert case.worker.run_once()
    assert case.worker.run_once()
    company_one = case.queue.get_batch(case.actors["employee"], first["id"])
    company_two = case.queue.get_batch(case.actors["outsider"], second["id"])
    assert company_one["progress"]["succeeded"] == 1
    assert company_two["progress"]["succeeded"] == 1
    assert company_one["progress"]["pending"] == 2 and case.provider.extract_calls == 2


def test_cancellation_prevents_queued_call_and_preserves_reason_in_audit(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    job_id = batch["jobs"][0]["id"]
    cancelled = case.queue.cancel_job(case.actors["employee"], job_id, "本人确认票据重复上传，请取消")
    assert cancelled["jobs"][0]["status"] == "cancelled"
    assert case.queue.cancel_job(case.actors["employee"], job_id, "重复请求取消")["progress"]["cancelled"] == 1
    assert not case.worker.run_once() and case.provider.extract_calls == 0
    with case.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM audit_events WHERE object_id=?", (job_id,)).fetchall()
    assert "本人确认票据重复上传" in str([dict(row) for row in rows])


@pytest.mark.parametrize("code", ["busy", "network_error"])
def test_proven_unsent_retry_has_bounded_persistent_backoff(queue_case, code):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    case.provider.failure = ProviderError(code, "合成发送前容量或连接拒绝", retryable=True)
    case.provider.failure.llm_attempted = False
    assert case.worker.run_once()
    waiting = job(case, batch["id"])
    assert waiting["status"] == "retry_wait" and waiting["attempt_count"] == 1
    assert not case.worker.run_once()
    case.clock.advance(29)
    assert not case.worker.run_once() and case.provider.extract_calls == 1
    case.clock.advance(1)
    assert case.worker.run_once() and job(case, batch["id"])["attempt_count"] == 2
    case.clock.advance(59)
    assert not case.worker.run_once()
    case.clock.advance(1)
    assert case.worker.run_once()
    exhausted = job(case, batch["id"])
    assert exhausted["status"] == "failed" and exhausted["attempt_count"] == 3
    case.clock.advance(3600)
    assert not case.worker.run_once() and case.provider.extract_calls == 3
    assert len(exhausted["attempts"]) == 3


def test_manual_retry_opens_a_new_bounded_backoff_round(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    case.provider.failure = ProviderError("busy", "合成繁忙")
    case.provider.failure.llm_attempted = False
    for delay in (0, 30, 60):
        case.clock.advance(delay)
        assert case.worker.run_once()
    failed = job(case, batch["id"])
    assert failed["status"] == "failed"
    case.queue.retry_job(case.actors["employee"], failed["id"], "second-backoff-round-key")
    assert case.worker.run_once() and job(case, batch["id"])["attempt_count"] == 4
    case.clock.advance(30)
    assert case.worker.run_once(), "新人工重试轮次应在30秒后允许第二次尝试"
    assert job(case, batch["id"])["attempt_count"] == 5
    case.clock.advance(60)
    assert case.worker.run_once() and job(case, batch["id"])["status"] == "failed"
    assert case.provider.extract_calls == 6 and not case.worker.run_once()


@pytest.mark.parametrize("code", ["timeout", "network_error", "processing_failed"])
def test_uncertain_sent_failure_is_interrupted_and_never_auto_retried(queue_case, code):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    case.provider.failure = ProviderError(code, "合成请求结果不确定")
    assert case.worker.run_once()
    interrupted = job(case, batch["id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["attempts"][0]["status"] == "interrupted"
    assert interrupted["permissions"]["retry"]
    case.clock.advance(3600)
    assert not case.worker.run_once() and case.provider.extract_calls == 1


def bind_claimed_run(case, claimed, sent=False):
    """Install an actual persistent cut point, using production claim/bind checks."""
    actor = case.actors["employee"]
    execution = QueueExecution(case.queue, claimed)
    with case.db.transaction(write=True, actor=actor) as conn:
        event, attachment = case.queue._input(conn, actor, claimed["event_id"], claimed["attachment_id"])
        digest = fingerprint({"attachment_id": attachment["id"], "sha256": attachment["sha256"],
                              "provider": case.provider.settings.base_url, "format": case.provider.settings.response_format,
                              "model": case.provider.settings.vision_model, "prompt": "invoice-extraction-v1"})
        run, execute = case.intelligence._claim(conn, actor, event, "ocr", f"queue:{claimed['id']}:{claimed['attempt_count']}", digest,
                                               case.provider.settings.vision_model, "invoice-extraction-v1", attachment_id=attachment["id"])
        assert execute
        execution.bind(conn, run)
        if sent:
            execution.before_send(conn)
    return execution, run, attachment


def test_crash_after_marking_send_is_interrupted_until_explicit_manual_retry(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    claimed = case.queue.claim("crashed-after-send-worker", 180, 2)
    execution, run, _ = bind_claimed_run(case, claimed, sent=True)
    case.clock.advance(181)
    assert case.worker.recover() == 1
    current = job(case, batch["id"])
    assert current["status"] == "interrupted" and current["error_code"] == "result_uncertain"
    assert not case.worker.run_once() and case.provider.extract_calls == 0
    with case.db.transaction(actor=case.actors["employee"]) as conn:
        with pytest.raises(AccountingError):
            execution.check(conn)
        assert conn.execute("SELECT status,result_json FROM ai_runs WHERE id=?", (run["id"],)).fetchone()["status"] == "stale"
    case.queue.retry_job(case.actors["employee"], current["id"], "confirm-uncertain-retry-key")
    assert case.worker.run_once() and job(case, batch["id"])["status"] == "succeeded"
    assert case.provider.extract_calls == 1


def test_saved_terminal_run_recovers_receipt_without_another_supplier_call(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    claimed = case.queue.claim("receipt-crash-worker", 180, 2)
    _, run, attachment = bind_claimed_run(case, claimed, sent=True)
    pages = prepare_document(case.storage.read(attachment), attachment["media_type"])
    output = case.provider.extract_invoice(pages)
    # Reproduce durable data with a completed run and an incomplete queue
    # receipt. Current normal execution commits both in one transaction.
    with case.db.transaction(write=True, actor=case.actors["employee"]) as conn:
        case.intelligence._finish(conn, case.actors["employee"], run, output)
    assert case.provider.extract_calls == 1
    case.clock.advance(181)
    assert case.worker.recover() == 1
    current = job(case, batch["id"])
    assert current["status"] == "succeeded" and current["attempt_count"] == 1
    assert not case.worker.run_once() and case.provider.extract_calls == 1
    assert case.worker.recover() == 0


def test_late_old_worker_cannot_publish_over_recovered_and_newly_claimed_job(queue_case):
    case = queue_case
    event = create_event(case)
    batch = enqueue(case, [event])
    new_claims = []

    def recover_during_call():
        case.provider.extract_hook = None
        case.clock.advance(181)
        assert case.worker.recover() == 1
        current = job(case, batch["id"])
        assert current["status"] == "interrupted"
        case.queue.retry_job(case.actors["employee"], current["id"], "explicit-new-lease-retry")
        new_claims.append(case.queue.claim("new-lease-worker", 180, 2))

    case.provider.extract_hook = recover_during_call
    assert case.worker.run_once()
    current = job(case, batch["id"])
    assert current["status"] == "running" and current["attempt_count"] == 2
    assert case.provider.extract_calls == 1
    case.worker.process(new_claims[0])
    assert job(case, batch["id"])["status"] == "succeeded" and case.provider.extract_calls == 2
    with case.db.transaction() as conn:
        runs = conn.execute("SELECT status,result_json FROM ai_runs WHERE event_id=? ORDER BY created_at,id", (event["id"],)).fetchall()
    assert sorted(row["status"] for row in runs) == ["stale", "succeeded"]
    assert next(row for row in runs if row["status"] == "stale")["result_json"] is None


def test_revocation_during_running_model_call_discards_result_and_keeps_history(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    case.provider.extract_hook = lambda: case.directory.revoke_company_member(case.manager, case.company["id"], "employee")
    assert case.worker.run_once()
    current = case.queue.get_batch(case.actors["finance"], batch["id"])["jobs"][0]
    assert current["status"] == "stale" and case.provider.extract_calls == 1
    with case.db.transaction() as conn:
        run = conn.execute("SELECT status,result_json FROM ai_runs WHERE id=(SELECT current_run_id FROM ocr_jobs WHERE id=?)", (current["id"],)).fetchone()
    assert run["status"] == "stale" and run["result_json"] is None


def test_three_company_round_robin_prevents_large_batch_monopolizing_first_turns(queue_case):
    case = queue_case
    first = enqueue(case, [create_event(case), create_event(case), create_event(case)])
    second = enqueue(case, [create_other_event(case)], actor="outsider", key="three-company-second-key", company_id=case.other_company["id"])
    third_company = case.directory.create_company(case.manager, CompanyCreate(name="第三合成客户企业"))
    case.directory.grant_company_member(case.manager, third_company["id"], CompanyMemberGrant(email="outsider@example.test", role="employee"))
    third = enqueue(case, [create_other_event(case, third_company)], actor="outsider", key="three-company-third-key", company_id=third_company["id"])
    for _ in range(3):
        assert case.worker.run_once()
    first_progress = case.queue.get_batch(case.actors["employee"], first["id"])["progress"]
    assert first_progress["succeeded"] == 1 and first_progress["pending"] == 2
    assert case.queue.get_batch(case.actors["outsider"], second["id"])["progress"]["succeeded"] == 1
    assert case.queue.get_batch(case.actors["outsider"], third["id"])["progress"]["succeeded"] == 1
    assert case.provider.extract_calls == 3


def test_pending_queue_cannot_be_human_resolved_or_bypass_accounting_review(queue_case):
    case = queue_case
    event = create_event(case)
    extract(case, event, key="previous-verified-ocr")
    batch = enqueue(case, [event])
    with pytest.raises(AccountingError) as duplicate:
        extract(case, event, key="sync-during-active-queue")
    assert duplicate.value.status == 409 and case.provider.extract_calls == 1
    receipt = handoff(case, event)
    detail = case.intelligence.reconcile(case.actors["accountant"], event["id"])
    pending = next(row for row in detail["findings"] if row["code"].startswith("queue_pending_"))
    with pytest.raises(AccountingError):
        case.intelligence.resolve(case.actors["accountant"], pending["id"], "会计试图直接忽略未完成识别")
    with pytest.raises(AccountingError):
        case.ledger.approve(case.actors["accountant"], case.book["id"], receipt["draft_id"], ApprovalRequest(expected_version=1))
    cancelled = case.queue.cancel_job(case.actors["accountant"], batch["jobs"][0]["id"], "取消多余重识别，沿用已核对原件")
    assert cancelled["progress"]["cancelled"] == 1
    for finding in case.intelligence.reconcile(case.actors["accountant"], event["id"])["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            case.intelligence.resolve(
                case.actors["accountant"],
                finding["id"],
                "已独立核对合成原件与业务事实，确认可继续复核",
            )
    reviewed = case.ledger.approve(case.actors["accountant"], case.book["id"], receipt["draft_id"], ApprovalRequest(expected_version=1))
    assert reviewed["status"] == "approved" and case.provider.extract_calls == 1


def test_manual_retry_history_is_bounded_at_thirty_attempts(queue_case):
    case = queue_case
    batch = enqueue(case, [create_event(case)])
    case.provider.failure = ProviderError("schema_invalid", "合成不合规字段，交人工")
    for index in range(1, 31):
        if index > 1:
            case.queue.retry_job(case.actors["employee"], batch["jobs"][0]["id"], f"bounded-manual-key-{index:02d}")
        assert case.worker.run_once()
        assert job(case, batch["id"])["attempt_count"] == index
    final = job(case, batch["id"])
    assert final["status"] == "failed" and not final["permissions"]["retry"]
    assert len(final["attempts"]) == 30 and case.provider.extract_calls == 30
    with pytest.raises(AccountingError) as exhausted:
        case.queue.retry_job(case.actors["employee"], final["id"], "manual-key-after-thirty")
    assert exhausted.value.status == 409
    assert not case.worker.run_once() and case.provider.extract_calls == 30


def test_batch_retry_only_mutates_current_visible_jobs_and_updates_execution_actor(queue_case):
    case = queue_case
    own, peer = create_event(case), create_event(case, actor="peer")
    batch = enqueue(case, [own, peer], actor="finance")
    case.provider.failure = ProviderError("schema_invalid", "合成字段失败")
    assert case.worker.run_once() and case.worker.run_once()
    assert case.queue.get_batch(case.actors["finance"], batch["id"])["progress"]["failed"] == 2
    partial = case.queue.retry_batch(case.actors["employee"], batch["id"], "employee-partial-batch-retry")
    assert partial["progress"]["total"] == 1 and partial["progress"]["pending"] == 1
    finance = case.queue.get_batch(case.actors["finance"], batch["id"])
    assert finance["progress"]["failed"] == 1 and finance["progress"]["pending"] == 1
    with case.db.transaction() as conn:
        own_job = conn.execute("SELECT id,execution_user_id FROM ocr_jobs WHERE event_id=?", (own["id"],)).fetchone()
        peer_job = conn.execute("SELECT id,status,execution_user_id FROM ocr_jobs WHERE event_id=?", (peer["id"],)).fetchone()
        retried = [row[0] for row in conn.execute("SELECT job_id FROM ocr_retries WHERE request_key=?", ("employee-partial-batch-retry",))]
    assert own_job["execution_user_id"] == "employee" and retried == [own_job["id"]]
    assert peer_job["status"] == "failed" and peer_job["execution_user_id"] == "finance"
    case.provider.failure = None
    assert case.worker.run_once()
    assert job(case, batch["id"])["status"] == "succeeded" and not case.worker.run_once()
    assert case.provider.extract_calls == 3


@pytest.fixture
def queue_client(tmp_path):
    provider = FakeProvider()
    app = create_app(tmp_path / "http-queue.db", seed=True, attachment_root=tmp_path / "http-private",
                     intelligence_provider=provider, queue_worker_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 6790)) as client:
        client.provider = provider
        yield client


def test_http_batch_worker_exception_resolution_proposal_review_and_export(queue_client):
    client = queue_client
    employee, department, finance, accountant = [http_auth(client, role) for role in ("employee", "department", "finance", "accountant")]
    event, company = http_create(client, employee)
    client.provider.document = invoice_document(amount="0.30", net_amount="0.30", buyer_name=company["name"], buyer_tax_id=None)
    assert client.put(f"/api/companies/{company['id']}/intelligence/settings", headers=accountant, json={"enabled": True}).status_code == 200
    body = {"company_id": company["id"], "items": [{"event_id": event["id"], "attachment_id": event["attachments"][0]["id"], "expected_version": event["version"]}], "idempotency_key": "synthetic-http-queue-batch"}
    assert client.post("/api/intelligence/batches", json=body).status_code == 401
    created = client.post("/api/intelligence/batches", headers=employee, json=body)
    assert created.status_code == 202, created.text
    batch = created.json()
    assert client.provider.extract_calls == 0 and batch["progress"]["pending"] == 1
    assert client.post("/api/intelligence/batches", headers=employee, json=body).json()["id"] == batch["id"]
    assert client.get(f"/api/intelligence/batches/{batch['id']}", headers=accountant).status_code == 404
    assert QueueWorker(client.app.state.queue, worker_id="http-synthetic-worker").run_once()
    status = client.get(f"/api/intelligence/batches/{batch['id']}", headers=employee)
    assert status.status_code == 200 and status.json()["progress"]["succeeded"] == 1
    detail = client.get(f"/api/business/events/{event['id']}/intelligence", headers=finance)
    assert any(finding["severity"] == "blocking" for finding in detail.json()["findings"])
    for finding in detail.json()["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            assert client.post(f"/api/intelligence/findings/{finding['id']}/resolve", headers=employee, json={"reason": "申请人尝试自审"}).status_code == 403
    receipt, book = http_handoff(client, event, employee, department, finance)
    proposal = client.post(f"/api/books/{book['id']}/drafts/{receipt['draft_id']}/ai-proposals", headers=accountant, json={"expected_version": 1, "idempotency_key": "synthetic-queue-proposal"})
    assert proposal.status_code == 200 and proposal.json()["status"] == "succeeded", proposal.text
    apply_url = f"/api/intelligence/proposals/{proposal.json()['id']}/apply"
    applied = client.post(apply_url, headers=accountant, json={"expected_version": 1})
    if applied.status_code == 409:
        refreshed = client.get(f"/api/business/events/{event['id']}/intelligence", headers=accountant).json()
        for finding in refreshed["findings"]:
            if finding["severity"] == "blocking" and not finding["resolved"]:
                reviewed = client.post(f"/api/intelligence/findings/{finding['id']}/resolve", headers=accountant, json={"reason": "合成测试已核对完整原件与业务，确认差异需人工处理"})
                assert reviewed.status_code == 200, reviewed.text
        applied = client.post(apply_url, headers=accountant, json={"expected_version": 1})
    assert applied.status_code == 200 and applied.json()["status"] == "draft", applied.text
    export_body = {"draft_ids": [receipt["draft_id"]], "idempotency_key": "synthetic-queue-export"}
    for finding in client.get(f"/api/business/events/{event['id']}/intelligence", headers=accountant).json()["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            assert client.post(
                f"/api/intelligence/findings/{finding['id']}/resolve",
                headers=accountant,
                json={"reason": "复核合成原件与业务事实后确认继续"},
            ).status_code == 200
    assert client.post(f"/api/books/{book['id']}/exports", headers=accountant, json=export_body).status_code == 409
    approved = client.post(f"/api/books/{book['id']}/drafts/{receipt['draft_id']}/approve", headers=accountant, json={"expected_version": applied.json()["version"]})
    assert approved.status_code == 200, approved.text
    exported = client.post(f"/api/books/{book['id']}/exports", headers=accountant, json=export_body)
    assert exported.status_code == 200 and "0.30" in exported.json()["csv"], exported.text
    assert client.provider.extract_calls == 1 and client.provider.proposal_calls == 1
    assert client.get(f"/api/intelligence/batches/{batch['id']}", headers=accountant).json()["progress"]["succeeded"] == 1
