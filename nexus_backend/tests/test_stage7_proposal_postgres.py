"""Dedicated PostgreSQL worker and current RLS, synthetic provider only."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounting.catalog import CatalogService
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest
from app.intelligence.provider import ProviderError
from app.jobs.requests import BatchRequest
from test_intelligence_workflow import create_event, extract
from test_queue_worker_postgres import (
    ADMIN_URL,
    APP_URL,
    WORKER_URL,
    business_case,
    business_postgres,
    pg_ai,
    pg_queue,
)
from test_stage7_proposal_queue import template, handoff, independent_original

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or not WORKER_URL,
    reason="需要专用 PostgreSQL 管理员、API 和独立 worker 测试连接",
)


@pytest.fixture
def pg_stage7(pg_queue, monkeypatch):
    case = pg_queue
    case.business.proposal_queue = case.queue.proposals
    case.catalog = CatalogService(case.db)
    independent_original(case, monkeypatch)
    # Freeze this PG fixture's randomized ticket/tax identifiers independently
    # from all subsequent mutable model output and fault injection.
    case.original_text = case.provider.document["pages"][0]["raw_text"]
    yield case
    with case.psycopg.connect(ADMIN_URL) as conn:
        cancel_fixture_jobs(conn, case.manager.firm_id)


def cancel_fixture_jobs(conn, firm_id):
    """End this synthetic firm's runnable and resumable work; keep all evidence.

    needs_attention is terminal for one attempt but still eligible for opt-in
    automatic recovery. Leaving it behind contaminates later global worker
    tests, including fair dispatch and the bounded 12-company resume scan.
    """
    conn.execute(
        "UPDATE accounting.proposal_attempts SET phase='finished',status='cancelled',finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status='running'",
        (firm_id,),
    )
    conn.execute(
        "UPDATE accounting.proposal_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('waiting','queued','running','retry_wait','needs_attention')",
        (firm_id,),
    )


def job(case, result):
    return case.queue.proposals.get(case.actors["accountant"], result["automatic_job"]["id"])


def draft(case, result):
    return case.ledger.get_draft(case.actors["accountant"], result["book_id"], result["draft_id"])


def test_real_pg_handoff_missing_ocr_auto_model_adoption_then_independent_review(pg_stage7):
    case = pg_stage7
    result = handoff(case, create_event(case))
    assert job(case, result)["status"] == "waiting"
    assert case.worker.run_once()
    assert case.worker.run_once()
    task, value = job(case, result), draft(case, result)
    assert task["status"] == "succeeded", task
    assert task["current_run_id"] and value["version"] > result["initial_draft_version"] and value["status"] == "draft"
    assert case.provider.extract_calls == case.provider.proposal_calls == 1
    assert not case.worker.run_once()
    with pytest.raises(AccountingError):
        case.ledger.export(
            case.actors["accountant"],
            result["book_id"],
            ExportRequest(draft_ids=[value["id"]], idempotency_key="pg-stage7-before-review"),
        )
    case.ledger.approve(
        case.manager,
        result["book_id"],
        value["id"],
        ApprovalRequest(expected_version=value["version"]),
    )
    assert (
        case.ledger.export(
            case.actors["accountant"],
            result["book_id"],
            ExportRequest(draft_ids=[value["id"]], idempotency_key="pg-stage7-after-review"),
        )["count"]
        == 1
    )


def test_real_pg_current_ocr_is_reused_and_rule_does_not_call_journal_model(pg_stage7):
    case = pg_stage7
    configured = template(case)
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    assert job(case, result)["status"] == "queued"
    assert case.worker.run_once()
    task = job(case, result)
    assert task["status"] == "succeeded", task
    assert task["result_template_id"] == configured["id"] and task["result_draft_version"] == draft(case, result)["version"] > result["initial_draft_version"]
    assert case.provider.proposal_calls == 0 and case.provider.extract_calls == 1
    assert draft(case, result)["status"] == "draft"


@pytest.mark.parametrize("actor", ["employee", "peer", "viewer", "outsider"])
def test_real_pg_proposal_jobs_hidden_from_non_book_edit_principals(pg_stage7, actor):
    case = pg_stage7
    result = handoff(case, create_event(case))
    principal = case.actors[actor]
    assert case.queue.proposals.list(principal)["total"] == 0
    with pytest.raises(AccountingError):
        case.queue.proposals.get(principal, result["automatic_job"]["id"])
    with case.db.transaction(actor=principal) as conn:
        for table in (
            "proposal_jobs",
            "proposal_dependencies",
            "proposal_attempts",
            "proposal_retries",
        ):
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM " + table + " WHERE firm_id=?", (principal.firm_id,)
                ).fetchone()[0]
                == 0
            )


def test_real_pg_api_cannot_claim_finish_or_read_worker_queue_tables(pg_stage7):
    case = pg_stage7
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        assert not conn.execute(
            "SELECT has_function_privilege(current_user,'accounting.claim_processing_job(text,integer,integer)','EXECUTE')"
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_function_privilege(current_user,'accounting.worker_finish_proposal_job(text,text,text,text,text,text,integer)','EXECUTE')"
        ).fetchone()[0]
    with case.worker.queue.worker_transaction() as conn:
        for table in (
            "proposal_jobs",
            "proposal_dependencies",
            "proposal_attempts",
            "proposal_retries",
        ):
            assert not conn.execute(
                "SELECT has_table_privilege(current_user,?,'SELECT,INSERT,UPDATE,DELETE')",
                ("accounting." + table,),
            ).fetchone()[0]


def test_real_pg_status_tampering_and_receipt_provenance_are_rejected(pg_stage7):
    case = pg_stage7
    result = handoff(case, create_event(case))
    task_id = result["automatic_job"]["id"]
    for sql in (
        "UPDATE proposal_jobs SET status='queued' WHERE id=?",
        "UPDATE proposal_jobs SET execution_user_id='invented-actor' WHERE id=?",
    ):
        with pytest.raises(Exception):
            with case.db.transaction(write=True, actor=case.actors["accountant"]) as conn:
                conn.execute(sql, (task_id,))
    assert job(case, result)["status"] == "waiting"


def test_real_pg_revoke_executor_before_send_stops_automated_candidate(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    case.directory.revoke_company_member(
        case.manager, case.company["id"], case.actors["finance"].user_id
    )
    assert case.worker.run_once()
    assert job(case, result)["status"] == "stale" and case.provider.proposal_calls == 0
    assert draft(case, result)["version"] == result["initial_draft_version"]


def test_real_pg_inflight_manual_edit_is_retained_and_candidate_is_fenced(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)

    def edit():
        value = draft(case, result)
        case.ledger.update_draft(
            case.actors["accountant"],
            result["book_id"],
            value["id"],
            DraftUpdate(
                expected_version=value["version"],
                description="真实PG在途期间人工修订",
                lines=[
                    {
                        "account_code": row["account_code"],
                        "debit": row["debit"],
                        "credit": row["credit"],
                    }
                    for row in value["lines"]
                ],
            ),
        )

    case.provider.proposal_hook = edit
    assert case.worker.run_once()
    assert job(case, result)["status"] == "stale"
    assert draft(case, result)["description"] == "真实PG在途期间人工修订"


def test_real_pg_cancel_sent_proposal_ignores_late_response(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    case.provider.proposal_hook = lambda: case.queue.proposals.cancel(
        case.actors["accountant"], result["automatic_job"]["id"], "真实PG会计取消在途建议"
    )
    assert case.worker.run_once()
    assert job(case, result)["status"] == "cancelled" and draft(case, result)["version"] == result["initial_draft_version"]


def test_real_pg_sent_timeout_manual_retry_keeps_immutable_attempts(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    case.provider.failure = ProviderError("timeout", "真实PG合成供应商超时")
    assert case.worker.run_once()
    assert job(case, result)["status"] == "interrupted" and not case.worker.run_once()
    case.provider.failure = None
    retry = case.queue.proposals.retry(
        case.actors["accountant"],
        result["automatic_job"]["id"],
        "pg-stage7-manual-retry",
        "已核对未返回结果后人工重试",
    )
    assert retry["status"] == "queued"
    assert case.worker.run_once()
    finished = job(case, result)
    assert finished["status"] == "succeeded" and len(finished["attempts"]) == 2
    assert finished["attempts"][0]["status"] == "interrupted"


def test_real_pg_mixed_claim_rejects_same_company_parallel_ocr(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    other = create_event(case)
    case.queue.enqueue(
        case.actors["employee"],
        BatchRequest(
            company_id=case.company["id"],
            items=[
                {
                    "event_id": other["id"],
                    "attachment_id": other["attachments"][0]["id"],
                    "expected_version": other["version"],
                }
            ],
            idempotency_key="pg-stage7-mixed-company",
        ),
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        claims = list(
            pool.map(lambda index: case.worker.queue.claim(f"pg-mixed-{index}"), range(3))
        )
    claimed = [item for item in claims if item]
    assert len(claimed) == 1 and claimed[0]["job_kind"] == "proposal"
    case.worker.process(claimed[0])
    assert job(case, result)["status"] == "succeeded"
    assert case.worker.run_once()


def test_real_pg_fact_version_survives_submission_for_ocr_dependency(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    batch = case.queue.enqueue(
        case.actors["employee"],
        BatchRequest(
            company_id=case.company["id"],
            items=[
                {
                    "event_id": event["id"],
                    "attachment_id": event["attachments"][0]["id"],
                    "expected_version": event["version"],
                }
            ],
            idempotency_key="pg-stage7-facts-submitted",
        ),
    )
    submitted = case.business.submit(case.actors["employee"], event["id"], event["version"])
    assert (
        submitted["fact_version"] == event["fact_version"]
        and submitted["version"] != event["version"]
    )
    assert case.worker.run_once()
    assert (
        case.queue.get_batch(case.actors["employee"], batch["id"])["jobs"][0]["status"]
        == "succeeded"
    )
