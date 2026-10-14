"""Organization suspension stops new work and rejects an in-flight OCR result."""

from app.accounting.admin_models import AdminCompanyStatus
from app.accounting.admin_service import AdminService
from test_queue_workflow import (
    ai_case, queue_case, create_event, create_other_event, enqueue,
)


def suspend(case, status="suspended", version=1):
    return AdminService(case.db).change_status(
        case.manager, case.company["id"],
        AdminCompanyStatus(status=status, expected_version=version, reason="试点调度状态验证"),
    )


def test_suspended_company_does_not_take_slots_and_can_resume(queue_case):
    case = queue_case
    first = enqueue(case, [create_event(case)], key="admin-dispatch-first")
    second = enqueue(case, [create_other_event(case)], actor="outsider", key="admin-dispatch-second", company_id=case.other_company["id"])
    suspend(case)
    claimed = case.queue.claim("admin-dispatch-worker")
    assert claimed["company_id"] == case.other_company["id"]
    assert claimed["id"] == second["jobs"][0]["id"]
    assert case.queue.claim("admin-dispatch-worker") is None
    with case.db.transaction() as conn:
        row = conn.execute("SELECT status,attempt_count FROM ocr_jobs WHERE id=?", (first["jobs"][0]["id"],)).fetchone()
        assert row["status"] == "queued" and row["attempt_count"] == 0
    suspend(case, "active", 2)
    resumed = case.queue.claim("admin-dispatch-worker")
    assert resumed["id"] == first["jobs"][0]["id"]


def test_suspension_after_claim_prevents_provider_call(queue_case):
    case = queue_case
    enqueue(case, [create_event(case)], key="admin-stop-before-send")
    claimed = case.queue.claim("admin-dispatch-worker")
    suspend(case)
    case.worker.process(claimed)
    assert case.provider.extract_calls == 0
    with case.db.transaction() as conn:
        assert conn.execute("SELECT status FROM ocr_jobs WHERE id=?", (claimed["id"],)).fetchone()[0] != "succeeded"


def test_suspension_during_model_request_cannot_publish_success(queue_case):
    case = queue_case
    enqueue(case, [create_event(case)], key="admin-stop-in-flight")
    case.provider.extract_hook = lambda: suspend(case)
    assert case.worker.run_once()
    assert case.provider.extract_calls == 1
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ocr_jobs WHERE status='succeeded'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ai_runs WHERE status='succeeded'").fetchone()[0] == 0
