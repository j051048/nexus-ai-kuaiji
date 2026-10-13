from period_fixtures import restore_period_state
"""Explicit human adoption is a protected manual decision, not an automatic draft."""
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from test_intelligence_workflow import ai_case, create_event, extract, handoff, draft, propose
from test_queue_worker_postgres import ADMIN_URL, APP_URL, WORKER_URL, pg_queue, pg_ai, business_case, business_postgres
from test_stage7_proposal_postgres import pg_stage7, job as processing_job, draft as loaded_processing_draft


def processing_draft(case, result):
    # Reader metadata is not journal content. Compare every domain field with
    # the apply response while preserving exact amounts, evidence and versions.
    return {key: value for key, value in loaded_processing_draft(case, result).items() if key != "detail_loaded"}


def manual_marker(case, draft_id):
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        return conn.execute("SELECT last_manual_edit_version FROM drafts WHERE id=?", (draft_id,)).fetchone()[0]


@pytest.mark.parametrize("automatic", [False, True])
def test_manual_adoption_marks_decision_and_blocks_future_automatic_candidate(ai_case, automatic):
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    initial = propose(ai_case, receipt["draft_id"])
    with ai_case.db.transaction(write=True, actor=ai_case.actors["accountant"]) as conn:
        ai_case.intelligence._apply_in_transaction(conn, ai_case.actors["accountant"], initial["id"], 1, automatic=automatic)
    before = draft(ai_case, receipt["draft_id"])
    assert manual_marker(ai_case, receipt["draft_id"]) == (0 if automatic else 2)
    candidate = propose(ai_case, receipt["draft_id"], version=2, key="after-adoption-" + uuid4().hex)
    with ai_case.db.transaction() as conn:
        details = json.loads(conn.execute("SELECT details FROM audit_events WHERE action='intelligence.proposal.apply' AND object_id=? ORDER BY id DESC LIMIT 1", (receipt["draft_id"],)).fetchone()[0])
    assert details["automatic"] is automatic
    if automatic:
        with ai_case.db.transaction(write=True, actor=ai_case.actors["accountant"]) as conn:
            after = ai_case.intelligence._apply_in_transaction(conn, ai_case.actors["accountant"], candidate["id"], 2, automatic=True)
        assert after["version"] == 3 and manual_marker(ai_case, receipt["draft_id"]) == 0
    else:
        with pytest.raises(AccountingError, match="人工修改"):
            with ai_case.db.transaction(write=True, actor=ai_case.actors["accountant"]) as conn:
                ai_case.intelligence._apply_in_transaction(conn, ai_case.actors["accountant"], candidate["id"], 2, automatic=True)
        assert draft(ai_case, receipt["draft_id"]) == before
        with ai_case.db.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications WHERE run_id=?", (candidate["id"],)).fetchone()[0] == 0


def automatically_selected(case):
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    candidate = propose(case, receipt["draft_id"])
    with case.db.transaction(write=True, actor=case.actors["accountant"]) as conn:
        case.intelligence._apply_in_transaction(conn, case.actors["accountant"], candidate["id"], 1, automatic=True)
    return receipt, candidate


def test_current_human_confirmation_protects_an_already_automatically_adopted_candidate(ai_case):
    receipt, candidate = automatically_selected(ai_case)
    before = draft(ai_case, receipt["draft_id"])
    # Replaying the old command merely recovers its receipt; it proves no current
    # version was viewed or selected and cannot convert the draft to manual.
    replay = ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], 1)
    assert replay == before and manual_marker(ai_case, receipt["draft_id"]) == 0
    with ai_case.db.transaction(write=True, actor=ai_case.actors["accountant"]) as conn:
        ai_case.intelligence._apply_in_transaction(conn, ai_case.actors["accountant"], candidate["id"], 2, automatic=True)
    assert manual_marker(ai_case, receipt["draft_id"]) == 0
    confirmed = ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], 2)
    assert confirmed == before and manual_marker(ai_case, receipt["draft_id"]) == 2
    assert ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], 2) == confirmed
    with ai_case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE object_id=? AND action='intelligence.proposal.confirm'", (receipt["draft_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications WHERE draft_id=?", (receipt["draft_id"],)).fetchone()[0] == 1
    newer = propose(ai_case, receipt["draft_id"], version=2, key="after-current-confirm-" + uuid4().hex)
    with pytest.raises(AccountingError, match="人工修改"):
        with ai_case.db.transaction(write=True, actor=ai_case.actors["accountant"]) as conn:
            ai_case.intelligence._apply_in_transaction(conn, ai_case.actors["accountant"], newer["id"], 2, automatic=True)
    assert draft(ai_case, receipt["draft_id"]) == confirmed


@pytest.mark.parametrize("frozen", ["approved", "closed"])
def test_receipt_replay_never_confirms_a_frozen_draft(ai_case, frozen):
    receipt, candidate = automatically_selected(ai_case)
    if frozen == "approved":
        ai_case.ledger.approve(ai_case.actors["accountant"], ai_case.book["id"], receipt["draft_id"], ApprovalRequest(expected_version=2))
    else:
        restore_period_state(ai_case.ledger.db, ai_case.book["id"])
    before = draft(ai_case, receipt["draft_id"])
    ai_case.intelligence.apply(ai_case.actors["accountant"], candidate["id"], before["version"])
    assert draft(ai_case, receipt["draft_id"]) == before and manual_marker(ai_case, receipt["draft_id"]) == 0
    with ai_case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE object_id=? AND action='intelligence.proposal.confirm'", (receipt["draft_id"],)).fetchone()[0] == 0


@pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PG 管理员、API 和独立 worker 测试连接")
def test_pg_manual_adoption_fences_old_job_and_refuses_automatic_retry(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    task_id = result["automatic_job"]["id"]
    assert processing_job(case, result)["status"] == "queued"
    candidate = propose(case, result["draft_id"])
    selected = case.intelligence.apply(case.actors["accountant"], candidate["id"], 1)
    assert selected["version"] == 2 and manual_marker(case, result["draft_id"]) == 2
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.actors["accountant"]) as conn:
            conn.execute("UPDATE drafts SET last_manual_edit_version=0 WHERE id=?", (result["draft_id"],))
    assert case.worker.run_once()
    assert processing_job(case, result)["status"] == "stale"
    assert case.provider.proposal_calls == 1
    with pytest.raises(AccountingError, match="人工修改") as refused:
        case.queue.proposals.retry(case.actors["accountant"], task_id, "manual-selected-retry-" + uuid4().hex, "试图让旧任务覆盖已选候选")
    assert refused.value.status == 409
    assert processing_job(case, result)["status"] == "stale" and not case.worker.run_once()
    assert processing_draft(case, result) == selected
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposal_retries WHERE job_id=?", (task_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications WHERE draft_id=?", (result["draft_id"],)).fetchone()[0] == 1


@pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PG 管理员、API 和独立 worker 测试连接")
def test_pg_current_human_confirmation_protects_worker_adopted_candidate(pg_stage7):
    case = pg_stage7
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    assert case.worker.run_once()
    task = processing_job(case, result)
    assert task["status"] == "succeeded" and task["current_run_id"]
    before = processing_draft(case, result)
    assert before["version"] == 2 and manual_marker(case, result["draft_id"]) == 0
    # A response lost by the original caller cannot itself express a new manual
    # decision. Selecting the current version can, without replacing its lines.
    assert case.intelligence.apply(case.actors["accountant"], task["current_run_id"], 1) == before
    assert manual_marker(case, result["draft_id"]) == 0
    assert case.intelligence.apply(case.actors["accountant"], task["current_run_id"], 2) == before
    assert manual_marker(case, result["draft_id"]) == 2
    newer = propose(case, result["draft_id"], version=2, key="pg-after-confirm-" + uuid4().hex)
    with pytest.raises(AccountingError, match="人工修改"):
        with case.db.transaction(write=True, actor=case.actors["accountant"]) as conn:
            case.intelligence._apply_in_transaction(conn, case.actors["accountant"], newer["id"], 2, automatic=True)
    assert processing_draft(case, result) == before
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE object_id=? AND action='intelligence.proposal.confirm'", (result["draft_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications WHERE draft_id=?", (result["draft_id"],)).fetchone()[0] == 1
