"""Permanent real PostgreSQL regression suite for opt-in scheduling and RLS."""
from uuid import uuid4
import pytest
from app.accounting.models import DraftUpdate
from app.jobs.requests import AutomationPolicyRequest
from app.cases.service import CaseService
from app.cases.models import CaseCreate, CaseRespond, CaseReviewResponse, CaseCommand
from test_stage7_proposal_postgres import pg_stage7, job, draft
from test_queue_worker_postgres import ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres, pg_ai, pg_queue
from test_intelligence_workflow import create_event, handoff

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PG 管理员、受限 API 和 worker 登录")


def blocked(c):
    c.intelligence.company_settings(c.manager, c.company["id"], False)
    event = create_event(c)
    return {**handoff(c, event), "event_id": event["id"]}


def enable(c):
    return c.queue.automation.update_policy(c.manager, c.company["id"], AutomationPolicyRequest(expected_version=0, enabled=True, reason="真实 PG 合成企业条件恢复测试"))


def test_pg_disabled_configuration_resume_frozen_receipt_and_metric_pipeline(pg_stage7):
    c = pg_stage7; result = blocked(c)
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.worker.queue.automation.resume_pending() == 0
    enable(c)
    assert c.worker.queue.automation.resume_pending() == 1
    assert c.worker.queue.automation.resume_pending() == 0
    events = c.queue.automation.events(c.actors["accountant"], c.book["id"])["events"]
    assert len(events) == 1 and events[0]["generation"] == 2
    assert c.worker.run_once() and c.worker.run_once()
    assert job(c, result)["status"] == "succeeded" and draft(c, result)["status"] == "draft"
    stages = {row["stage"] for row in c.queue.automation.metrics(c.manager, c.company["id"])["stages"]}
    assert {"dependency_wait", "eligible_queue_wait", "claim_lock", "model_call", "validation_persistence"} <= stages


@pytest.mark.parametrize("change", ["revoke", "manual", "cancel"])
def test_pg_original_principal_manual_work_and_cancellation_block_resume(pg_stage7, change):
    c = pg_stage7; result = blocked(c); enable(c)
    if change == "revoke":
        c.directory.revoke_company_member(c.manager, c.company["id"], c.actors["finance"].user_id)
    elif change == "manual":
        c.ledger.update_draft(c.actors["accountant"], c.book["id"], result["draft_id"], DraftUpdate(expected_version=1, description="保持会计人工修改", lines=[{"account_code":"6602.02","debit":"0.30","credit":"0.00"},{"account_code":"2241","debit":"0.00","credit":"0.30"}]))
    else:
        c.queue.proposals.cancel(c.actors["accountant"], result["automatic_job"]["id"], "会计明确取消自动处理")
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.worker.queue.automation.resume_pending() == 0
    assert c.provider.proposal_calls == c.provider.extract_calls == 0


def test_pg_independent_supplement_accepted_then_closed_rebinds_only_verified_version(pg_stage7):
    c = pg_stage7; result = blocked(c); enable(c)
    cases = CaseService(c.db, c.business)
    task = cases.create(c.actors["accountant"], CaseCreate(event_id=result["event_id"], kind="supplement", title="说明采购用途", reason="补充独立核验的业务依据", idempotency_key=uuid4().hex))
    task = cases.command(c.actors["employee"], task["id"], "respond", CaseRespond(expected_version=task["version"], idempotency_key=uuid4().hex, reason="员工填写真实采购用途", fields={"用途":"日常办公耗材"}))
    task = cases.command(c.actors["accountant"], task["id"], "review-response", CaseReviewResponse(expected_version=task["version"], idempotency_key=uuid4().hex, reason="独立核验用途", decision="accept"))
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.worker.queue.automation.resume_pending() == 0
    cases.command(c.actors["accountant"], task["id"], "close", CaseCommand(expected_version=task["version"], idempotency_key=uuid4().hex, reason="完成补件复核并关闭"))
    assert c.worker.queue.automation.resume_pending() == 1
    receipt = c.queue.automation.events(c.actors["accountant"], c.book["id"])["events"][0]["reason"]
    assert receipt["new_fact_version"] == receipt["old_fact_version"] + 1 and len(receipt["accepted_response_ids"]) == 1


def test_pg_no_raw_metrics_receipts_or_worker_resume_from_api_and_force_rls(pg_stage7):
    c = pg_stage7
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        for function in ("accounting.worker_claim_resume_candidates(integer)", "accounting.worker_resume_proposal(text,text,text,text)", "accounting.worker_record_job_metric(text,text,text,text,bigint)"):
            assert not conn.execute("SELECT has_function_privilege(current_user,?,'EXECUTE')", (function,)).fetchone()[0]
        for table in ("proposal_resume_events", "processing_job_metrics", "queue_verified_evidence_versions", "proposal_resume_watch", "queue_resume_signals"):
            assert not conn.execute("SELECT has_table_privilege(current_user,?,'INSERT,UPDATE,DELETE')", ("accounting." + table,)).fetchone()[0]
    with c.worker.queue.worker_transaction() as conn:
        for table in ("queue_automation_policies", "proposal_resume_events", "processing_job_metrics", "queue_verified_evidence_versions", "proposal_resume_watch", "queue_resume_signals"):
            assert not conn.execute("SELECT has_table_privilege(current_user,?,'SELECT,INSERT,UPDATE,DELETE')", ("accounting." + table,)).fetchone()[0]
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ("queue_automation_policies", "proposal_resume_events", "processing_job_metrics", "queue_verified_evidence_versions", "proposal_resume_watch", "queue_resume_signals"):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("accounting." + table,)).fetchone() == (True, True)
