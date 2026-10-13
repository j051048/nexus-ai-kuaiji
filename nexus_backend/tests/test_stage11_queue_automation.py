"""Real SQLite transactions; local synthetic invoices only."""
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import DraftUpdate
from app.business.models import BusinessEventUpdate
from app.cases.models import CaseCreate, CaseCommand, CaseRespond, CaseReviewResponse
from app.cases.service import CaseService
from app.jobs.requests import AutomationPolicyRequest
from app.jobs.service import QueueService
from test_stage7_proposal_queue import proposal_case, proposal_cost_case, current, draft, template
from test_intelligence_workflow import ai_case, create_event, handoff, extract, invoice_document
from test_model_cost_workflow import cost_case, policy
from test_stage8_rule_queue_integration import company_rules, event as rule_event
from app.operations.meter import MeterService
from app.operations.pricing import Pricing
from app.operations.service import OperationsService
from app.accounting.catalog import CatalogService


def enable(c, **changes):
    old = c.queue.automation.policy(c.manager, c.company["id"])
    return c.queue.automation.update_policy(c.manager, c.company["id"], AutomationPolicyRequest(expected_version=old["version"], enabled=True, reason="仅核验企业恢复后的未发送任务", **changes))


def blocked(c):
    c.intelligence.company_settings(c.manager, c.company["id"], False)
    event = create_event(c)
    result = {**handoff(c, event), "event_id": event["id"]}
    assert current(c, result)["error_code"] == "processing_disabled"
    return result


def command(cases, actor, task, operation, cls=CaseCommand, **values):
    return cases.command(actor, task["id"], operation, cls(expected_version=task["version"], idempotency_key=uuid4().hex, reason="独立核对补充依据", **values))


def accepted_supplement(c, result):
    cases = CaseService(c.db, c.business)
    task = cases.create(c.actors["accountant"], CaseCreate(event_id=result["event_id"], kind="supplement", title="补充采购用途", reason="请明确采购用途", idempotency_key=uuid4().hex))
    task = command(cases, c.actors["employee"], task, "respond", CaseRespond, fields={"采购用途": "办公室日常耗材"})
    task = command(cases, c.actors["accountant"], task, "review-response", CaseReviewResponse, decision="accept")
    return cases, task


def test_opt_in_disabled_by_default_configuration_restores_and_receipt_survives_restart(proposal_case):
    c = proposal_case; result = blocked(c)
    assert c.queue.automation.policy(c.manager, c.company["id"])["enabled"] is False
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.queue.automation.resume_pending() == 0
    enable(c)
    assert c.queue.automation.resume_pending() == 1
    assert c.queue.automation.resume_pending() == 0
    assert current(c, result)["status"] == "waiting"
    restarted = QueueService(c.db, c.intelligence, clock=c.clock)
    receipt = restarted.automation.events(c.actors["accountant"], c.book["id"])["events"]
    assert len(receipt) == 1 and receipt[0]["generation"] == 2
    assert receipt[0]["reason"]["blocker"] == "processing_disabled"
    assert c.worker.run_once() and c.worker.run_once()
    assert current(c, result)["status"] == "succeeded" and draft(c, result)["status"] == "draft"


@pytest.mark.parametrize("mutation", ["manual", "revoke", "cancel", "disabled"])
def test_auto_resume_never_substitutes_principal_or_overwrites_human_work(proposal_case, mutation):
    c = proposal_case; result = blocked(c); enable(c)
    if mutation == "manual":
        c.ledger.update_draft(c.actors["accountant"], c.book["id"], result["draft_id"], DraftUpdate(expected_version=1, description="会计人工复核修改", lines=[{"account_code": "6602.02", "debit": "0.30", "credit": "0.00"}, {"account_code": "2241", "debit": "0.00", "credit": "0.30"}]))
    elif mutation == "revoke":
        c.directory.revoke_company_member(c.manager, c.company["id"], c.actors["finance"].user_id)
    elif mutation == "cancel":
        assert c.queue.proposals.cancel(c.actors["accountant"], result["automatic_job"]["id"], "本事项暂不自动处理")["status"] == "cancelled"
    if mutation != "disabled":
        c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.queue.automation.resume_pending() == 0
    assert c.provider.extract_calls == c.provider.proposal_calls == 0
    assert not c.queue.automation.events(c.actors["accountant"], c.book["id"])["events"]


def test_independent_accepted_and_closed_supplement_can_advance_fact_generation(proposal_case):
    c = proposal_case; result = blocked(c); enable(c)
    cases, task = accepted_supplement(c, result)
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.queue.automation.resume_pending() == 0  # Acceptance alone leaves a case in review.
    command(cases, c.actors["accountant"], task, "close")
    assert c.queue.automation.resume_pending() == 1
    receipt = c.queue.automation.events(c.actors["accountant"], c.book["id"])["events"][0]["reason"]
    assert receipt["new_fact_version"] == receipt["old_fact_version"] + 1 and len(receipt["accepted_response_ids"]) == 1
    assert c.worker.run_once() and c.worker.run_once()
    assert draft(c, result)["status"] == "draft"


def test_supplement_version_rebinding_can_be_disabled(proposal_case):
    c = proposal_case; result = blocked(c); enable(c, allow_verified_supplements=False)
    cases, task = accepted_supplement(c, result); command(cases, c.actors["accountant"], task, "close")
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.queue.automation.resume_pending() == 0


def test_model_budget_is_classified_before_any_dependency_or_send_and_recovers(proposal_cost_case):
    c = proposal_cost_case; policy(c, monthly_limit="0.01")
    result = handoff(c, create_event(c)); task = current(c, result)
    assert task["status"] == "needs_attention" and task["error_code"] == "quota_exhausted"
    enable(c)
    assert c.queue.automation.resume_pending() == 0
    policy(c, monthly_limit="1.00")
    assert c.queue.automation.resume_pending() == 1
    assert c.worker.run_once() and c.worker.run_once()
    assert current(c, result)["status"] == "succeeded"


def test_permanent_stages_include_wait_model_and_validation_without_cross_enterprise_read(proposal_case):
    c = proposal_case; result = handoff(c, create_event(c))
    c.clock.advance(2)
    assert c.worker.run_once(); c.clock.advance(3); assert c.worker.run_once()
    value = c.queue.automation.metrics(c.manager, c.company["id"])
    stages = {row["stage"]: row for row in value["stages"]}
    assert {"dependency_wait", "eligible_queue_wait", "claim_lock", "model_call", "validation_persistence"} <= stages.keys()
    assert stages["model_call"]["samples"] == 2 and stages["claim_lock"]["samples"] == 2
    for actor in (c.actors["employee"], c.actors["accountant"], c.actors["outsider"]):
        with pytest.raises(AccountingError):
            c.queue.automation.metrics(actor, c.company["id"])
    with pytest.raises(AccountingError):
        c.queue.automation.events(c.actors["employee"], c.book["id"])
    with c.db.transaction(write=True) as conn:
        with pytest.raises(Exception):
            conn.execute("UPDATE processing_job_metrics SET duration_ms=0")
    assert current(c, result)["status"] == "succeeded"


def test_policy_scope_optimistic_version_and_check_batch_are_bounded(proposal_case):
    c = proposal_case
    with pytest.raises(AccountingError):
        c.queue.automation.update_policy(c.actors["employee"], c.company["id"], AutomationPolicyRequest(expected_version=0, enabled=True, reason="尝试越权配置"))
    enable(c)
    with pytest.raises(AccountingError):
        c.queue.automation.update_policy(c.manager, c.company["id"], AutomationPolicyRequest(expected_version=0, enabled=False, reason="旧版本不应覆盖"))
    for limit in (0, 13, True):
        with pytest.raises(ValueError):
            c.queue.automation.resume_pending(limit)


@pytest.mark.parametrize("reuse_new_fact", [False, True])
def test_budget_does_not_block_verified_ocr_and_template_with_no_new_external_call(proposal_cost_case, reuse_new_fact):
    c = proposal_cost_case; c.catalog = CatalogService(c.db); template(c); event = create_event(c); extract(c, event)
    if reuse_new_fact:
        event = c.business.update(c.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload={**event["payload"], "description":"保持同一原件并补充业务说明"}))
    policy(c, monthly_limit="0.01")
    c.meter.pricing = Pricing()  # No configured tariff: local/cache work is still free.
    result = handoff(c, event)
    assert current(c, result)["status"] in {"queued", "waiting"}
    if reuse_new_fact:
        assert c.worker.run_once()
    assert c.worker.run_once()
    assert current(c, result)["status"] == "succeeded" and c.provider.extract_calls == 1 and c.provider.proposal_calls == 0
    if reuse_new_fact:
        stages = {row["stage"]: row for row in c.queue.automation.metrics(c.manager, c.company["id"])["stages"]}
        assert stages["ocr_cache_reuse"]["samples"] == 1


def test_budget_does_not_block_published_rule_on_verified_ocr(company_rules):
    c = company_rules; value = rule_event(c, 3)
    c.provider.document = invoice_document(invoice_number="STAGE11-LOCAL-RULE")
    extract(c, value)
    c.meter = MeterService(c.db, pricing=Pricing(), clock=c.clock)
    c.intelligence.meter = c.meter
    c.operations = OperationsService(c.db, c.meter, intelligence=c.intelligence)
    policy(c, monthly_limit="0.01")
    result = handoff(c, value)
    assert current(c, result)["status"] == "queued"
    assert c.worker.run_once() and current(c, result)["status"] == "succeeded"
    assert current(c, result)["result_rule_id"] == c.published_rule["id"] and c.provider.proposal_calls == 0


def test_each_scan_resumes_at_most_one_job_per_company_and_revoked_policy_fences_claim(proposal_case):
    c = proposal_case; first = blocked(c); second = blocked(c); enable(c)
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.queue.automation.resume_pending() == 1
    assert c.queue.automation.resume_pending() == 1
    assert len(c.queue.automation.events(c.actors["accountant"], c.book["id"])["events"]) == 2
    third = blocked(c)
    candidate = c.queue.automation._candidates(12)[0]
    settings = c.queue.automation.policy(c.manager, c.company["id"])
    c.queue.automation.update_policy(c.manager, c.company["id"], AutomationPolicyRequest(expected_version=settings["version"], enabled=False, reason="管理员暂停本企业续跑"))
    c.intelligence.company_settings(c.manager, c.company["id"], True)
    assert c.queue.automation._resume(candidate) is False
    assert current(c, third)["status"] == "needs_attention"


def test_case_created_after_enqueue_is_classified_before_send_when_opted_in(proposal_case):
    c = proposal_case; enable(c); event = create_event(c); extract(c, event); result = handoff(c, event)
    cases = CaseService(c.db, c.business)
    task = cases.create(c.actors["accountant"], CaseCreate(event_id=event["id"], kind="exception", title="核对用途", reason="暂缓模型处理先核实用途", idempotency_key=uuid4().hex))
    assert c.worker.run_once() and current(c, result)["error_code"] == "case_pending"
    command(cases, c.actors["accountant"], task, "close")
    assert c.worker.run_once() and current(c, result)["status"] == "succeeded"
    assert c.provider.proposal_calls == 1
