from period_fixtures import restore_period_state
"""Real local transactions with explicit synthetic OCR/model output, no network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition, TemplateDefinition, TemplateUpdate
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest
from app.business.models import BusinessEventUpdate
from app.intelligence.provider import ProviderError
from app.jobs.requests import BatchItem, BatchRequest, ProposalRetryRequest
from app.jobs.service import QueueService
from app.jobs.worker import ProposalExecution, QueueWorker
from test_intelligence_workflow import ai_case, create_event, extract, handoff as workflow_handoff, invoice_document
from test_queue_workflow import TestClock, create_other_event
from test_model_cost_workflow import cost_case, policy


@pytest.fixture
def proposal_case(ai_case, monkeypatch):
    case = ai_case
    case.clock = TestClock()
    case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
    case.business.proposal_queue = case.queue.proposals
    case.worker = QueueWorker(case.queue, worker_id="stage7-synthetic-worker")
    case.catalog = CatalogService(case.db)
    independent_original(case, monkeypatch)
    return case


@pytest.fixture
def proposal_cost_case(cost_case, monkeypatch):
    case = cost_case
    case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
    case.business.proposal_queue = case.queue.proposals
    case.worker = QueueWorker(case.queue, worker_id="stage7-synthetic-cost-worker")
    independent_original(case, monkeypatch)
    return case


def independent_original(case, monkeypatch):
    """Automation positives have a separate, immutable synthetic OCR original.

    The blank PNG fixture proves storage only. This parser stub supplies an
    explicitly archived text layer, independent of mutable model output.
    """
    from app.intelligence import service
    prepare = service.prepare_document
    case.original_text = invoice_document()["pages"][0]["raw_text"]
    monkeypatch.setattr(service, "prepare_document", lambda *args, **kwargs: [
        replace(page, native_text=case.original_text) for page in prepare(*args, **kwargs)
    ])


def current(case, result):
    return case.queue.proposals.get(case.actors["accountant"], result["automatic_job"]["id"])


def draft(case, result):
    return case.ledger.get_draft(case.actors["accountant"], result["book_id"], result["draft_id"])


def handoff(case, event, **kwargs):
    result = workflow_handoff(case, event, **kwargs)
    result["initial_draft_version"] = draft(case, result)["version"]
    return result


def template(case, tax=False, **changes):
    lines = [{"account_code": "6602.02", "side": "debit", "amount_field": "net" if tax else "gross"},
             {"account_code": "2241", "side": "credit", "amount_field": "gross"}]
    if tax:
        case.catalog.create_account(case.manager, case.book["id"], AccountDefinition(code="2221.01", name="待核对税额", category="asset"))
        lines.insert(1, {"account_code": "2221.01", "side": "debit", "amount_field": "tax"})
    return case.catalog.save_template(case.manager, case.book["id"], TemplateDefinition(name="合成企业办公耗材", business_type="reimbursement", direction="expense", category="office", payment_status="unpaid", lines=lines, **changes))


def test_handoff_and_background_intent_are_one_atomic_idempotent_command(proposal_case):
    case = proposal_case
    event = create_event(case)
    result = handoff(case, event)
    task = current(case, result)
    assert task["status"] == "waiting" and task["expected_fact_version"] == event["fact_version"]
    assert case.provider.proposal_calls == case.provider.extract_calls == 0
    with case.db.transaction(actor=case.actors["finance"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposal_jobs").fetchone()[0] == 1
        raw_event = case.business._event(conn, case.actors["finance"], event["id"])
        raw_draft = conn.execute("SELECT * FROM drafts WHERE id=?", (result["draft_id"],)).fetchone()
        repeat = case.queue.proposals.enqueue_in_transaction(conn, case.actors["finance"], raw_event, {"id": result["book_id"]}, raw_draft)
        assert repeat["id"] == task["id"]
    restarted = QueueService(case.db, case.intelligence, clock=case.clock)
    assert restarted.proposals.get(case.actors["accountant"], task["id"])["status"] == "waiting"


def test_missing_ocr_then_model_candidate_adoption_stops_before_review_export(proposal_case):
    case = proposal_case
    result = handoff(case, create_event(case))
    assert case.worker.run_once()  # Durable OCR dependency.
    assert case.worker.run_once()  # Model candidate plus deterministic adoption.
    task, value = current(case, result), draft(case, result)
    assert task["status"] == "succeeded" and task["current_run_id"]
    assert value["status"] == "draft" and value["version"] > result["initial_draft_version"] and not value["issues"]
    assert case.provider.extract_calls == case.provider.proposal_calls == 1
    assert not case.worker.run_once()
    with pytest.raises(AccountingError):
        case.ledger.export(case.actors["accountant"], result["book_id"], ExportRequest(draft_ids=[value["id"]], idempotency_key="stage7-before-review"))
    approved = case.ledger.approve(case.manager, result["book_id"], value["id"], ApprovalRequest(expected_version=value["version"]))
    assert approved["status"] == "approved"
    exported = case.ledger.export(case.actors["accountant"], result["book_id"], ExportRequest(draft_ids=[value["id"]], idempotency_key="stage7-after-review"))
    assert exported["count"] == 1


def test_current_ocr_is_reused_without_adding_work_or_blocking_manual_review(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    assert current(case, result)["status"] == "queued"
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ocr_jobs").fetchone()[0] == 0
    assert case.worker.run_once() and current(case, result)["status"] == "succeeded"
    assert case.provider.extract_calls == case.provider.proposal_calls == 1


def test_changed_facts_enqueue_new_ocr_context_but_reuse_verified_image_content(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    event = case.business.update(case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload={**event["payload"], "description": "同一原件更新业务摘要"}))
    result = handoff(case, event)
    assert current(case, result)["status"] == "waiting"
    assert case.worker.run_once() and case.provider.extract_calls == 1
    assert case.worker.run_once() and case.provider.proposal_calls == 1
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_runs WHERE kind='ocr' AND cache_hit=1").fetchone()[0] == 1


def test_matching_published_template_avoids_journal_model_and_fee(proposal_case):
    case = proposal_case
    configured = template(case)
    result = handoff(case, create_event(case))
    assert case.worker.run_once() and case.worker.run_once()
    task, value = current(case, result), draft(case, result)
    assert task["status"] == "succeeded" and task["current_run_id"] is None
    assert task["result_template_id"] == configured["id"] and task["result_template_version"] == 1 and task["result_draft_version"] == value["version"] > result["initial_draft_version"]
    assert value["status"] == "draft" and not value["issues"]
    assert case.provider.proposal_calls == 0 and case.provider.extract_calls == 1
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_runs WHERE kind='proposal'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM model_costs WHERE kind='proposal'").fetchone()[0] == 0


def test_tax_template_consumes_only_verified_net_tax_after_ocr(proposal_case):
    case = proposal_case
    template(case, tax=True)
    case.provider.document.update(net_amount="0.20", tax_amount="0.10")
    for page in case.provider.document["pages"]:
        page["raw_text"] = page["raw_text"].replace("net_amount: 0.30", "net_amount: 0.20").replace("tax_amount: 0.00", "tax_amount: 0.10")
    for evidence in case.provider.document["evidence"]:
        evidence["quote"] = evidence["quote"].replace("net_amount: 0.30", "net_amount: 0.20").replace("tax_amount: 0.00", "tax_amount: 0.10")
    case.original_text = invoice_document(net_amount="0.20", tax_amount="0.10")["pages"][0]["raw_text"]
    result = handoff(case, create_event(case))
    assert draft(case, result)["lines"] == []
    assert case.worker.run_once() and case.worker.run_once()
    assert current(case, result)["status"] == "succeeded"
    assert {row["account_code"]: row["debit"] for row in draft(case, result)["lines"]}["2221.01"] == "0.10"
    assert case.provider.proposal_calls == 0


def test_unknown_tax_on_matching_template_requires_human_and_never_model_fallback(proposal_case):
    case = proposal_case
    template(case, tax=True)
    case.provider.document["tax_amount"] = None
    case.provider.document["evidence"] = [row for row in case.provider.document["evidence"] if row["field"] != "tax_amount"]
    result = handoff(case, create_event(case))
    assert case.worker.run_once() and case.worker.run_once()
    assert current(case, result)["status"] == "needs_attention"
    assert draft(case, result)["lines"] == [] and case.provider.proposal_calls == 0


@pytest.mark.parametrize("change", ["manual", "revoke", "disabled", "period", "template"])
def test_before_execution_rechecks_versions_permission_period_and_policy(proposal_case, change):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    if change == "manual":
        value = draft(case, result)
        case.ledger.update_draft(case.actors["accountant"], result["book_id"], result["draft_id"], DraftUpdate(expected_version=value["version"], description="会计人工修改绝不能覆盖", lines=[{"account_code": row["account_code"], "debit": row["debit"], "credit": row["credit"]} for row in value["lines"]]))
    elif change == "revoke":
        case.directory.revoke_company_member(case.manager, case.company["id"], "finance")
    elif change == "disabled":
        case.intelligence.company_settings(case.manager, case.company["id"], enabled=False)
    elif change == "period":
        with case.db.transaction(write=True) as conn:
            restore_period_state(case.ledger.db, result["book_id"], conn=conn)
    else:
        template(case)
    assert case.worker.run_once()
    assert current(case, result)["status"] == "stale" and case.provider.proposal_calls == 0
    if change == "manual":
        assert draft(case, result)["description"] == "会计人工修改绝不能覆盖"


def test_during_external_call_human_edit_wins_and_response_is_fenced(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    def edit():
        value = draft(case, result)
        case.ledger.update_draft(case.actors["accountant"], result["book_id"], result["draft_id"], DraftUpdate(expected_version=value["version"], description="模型等待期间会计修改", lines=[{"account_code": row["account_code"], "debit": row["debit"], "credit": row["credit"]} for row in value["lines"]]))
    case.provider.proposal_hook = edit
    assert case.worker.run_once()
    assert current(case, result)["status"] == "stale"
    assert draft(case, result)["description"] == "模型等待期间会计修改"
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications").fetchone()[0] == 0


def test_blocking_amount_discrepancy_waits_for_human_without_model_call(proposal_case):
    case = proposal_case
    case.provider.document["gross_amount"] = "0.40"
    result = handoff(case, create_event(case))
    assert case.worker.run_once() and case.worker.run_once()
    assert current(case, result)["status"] == "needs_attention" and case.provider.proposal_calls == 0
    assert draft(case, result)["version"] == result["initial_draft_version"]


def test_failed_ocr_dependency_becomes_attention_instead_of_waiting_forever(proposal_case):
    case = proposal_case
    result = handoff(case, create_event(case))
    case.provider.failure = ProviderError("invalid_output", "合成识别失败")
    assert case.worker.run_once()
    assert not case.worker.run_once()
    task = current(case, result)
    assert task["status"] == "needs_attention" and task["error_code"] == "ocr_dependency_failed"
    assert case.provider.proposal_calls == 0


def test_dependency_timeout_is_explicit_and_does_not_infer_ocr_success(proposal_case):
    case = proposal_case
    result = handoff(case, create_event(case))
    case.clock.advance(1801)
    case.queue.recover()
    task = current(case, result)
    assert task["status"] == "needs_attention" and task["error_code"] == "ocr_dependency_timeout"
    assert draft(case, result)["version"] == result["initial_draft_version"] and case.provider.proposal_calls == 0


def test_sent_timeout_requires_manual_retry_with_immutable_reason_receipt(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    case.provider.failure = ProviderError("timeout", "合成网络超时")
    assert case.worker.run_once() and current(case, result)["status"] == "interrupted"
    assert not case.worker.run_once()
    case.provider.failure = None
    task = case.queue.proposals.retry(case.actors["accountant"], result["automatic_job"]["id"], "stage7-manual-retry-key", "已核对供应商记录后重试")
    repeated = case.queue.proposals.retry(case.actors["accountant"], task["id"], "stage7-manual-retry-key", "已核对供应商记录后重试")
    assert repeated["id"] == task["id"] and task["attempt_count"] == 1
    with pytest.raises(AccountingError, match="理由"):
        case.queue.proposals.retry(case.actors["accountant"], task["id"], "stage7-manual-retry-key", "不能变更原请求理由")
    assert case.worker.run_once() and current(case, result)["status"] == "succeeded"
    assert case.provider.proposal_calls == 2
    value = draft(case, result)
    case.ledger.approve(case.manager, result["book_id"], value["id"], ApprovalRequest(expected_version=value["version"]))
    receipt_after_review = case.queue.proposals.retry(case.actors["accountant"], task["id"], "stage7-manual-retry-key", "已核对供应商记录后重试")
    assert receipt_after_review["status"] == "succeeded" and case.provider.proposal_calls == 2


def test_crash_before_send_is_recoverable_but_after_send_needs_manual_intervention(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    claimed = case.queue.claim("old-worker")
    assert claimed["job_kind"] == "proposal"
    case.clock.advance(181)
    assert case.queue.recover() == 1 and current(case, result)["status"] == "retry_wait"
    claimed = case.queue.claim("second-worker")
    with case.db.transaction(write=True) as conn:
        conn.execute("UPDATE proposal_attempts SET phase='sending' WHERE job_id=? AND lease_token=?", (claimed["id"], claimed["lease_token"]))
    case.clock.advance(181)
    assert case.queue.recover() == 1 and current(case, result)["status"] == "interrupted"
    assert case.queue.claim("third-worker") is None
    with case.db.transaction() as conn, pytest.raises(AccountingError, match="租约"):
        ProposalExecution(case.queue, claimed).check(conn)


def test_cancel_during_model_send_fences_response_and_does_not_adopt(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    case.provider.proposal_hook = lambda: case.queue.proposals.cancel(case.actors["accountant"], result["automatic_job"]["id"], "会计发现资料需要重新核对")
    assert case.worker.run_once()
    assert current(case, result)["status"] == "cancelled" and draft(case, result)["version"] == result["initial_draft_version"]


def test_company_isolation_and_sql_pagination_do_not_reveal_other_tasks(proposal_case):
    case = proposal_case
    results = [handoff(case, create_event(case)) for _ in range(4)]
    all_tasks = case.queue.proposals.list(case.actors["accountant"], limit=2)
    next_page = case.queue.proposals.list(case.actors["accountant"], limit=2, offset=2)
    assert all_tasks["total"] == next_page["total"] == 4
    assert len(all_tasks["jobs"]) == len(next_page["jobs"]) == 2
    assert {row["id"] for row in all_tasks["jobs"]}.isdisjoint({row["id"] for row in next_page["jobs"]})
    assert case.queue.proposals.list(case.actors["outsider"])["total"] == 0
    assert case.queue.proposals.list(case.actors["employee"])["total"] == 0
    for actor in ("outsider", "employee", "peer"):
        with pytest.raises(AccountingError):
            case.queue.proposals.get(case.actors[actor], results[0]["automatic_job"]["id"])
    text = str(all_tasks)
    assert "lease_token" not in text and "configuration_fingerprint" not in text and "execution_user_id" not in text


def test_ocr_and_proposal_share_global_and_per_company_running_limits(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    handoff(case, event)
    case.clock.advance(1)  # The fixed test clock must preserve submission order.
    other = create_other_event(case)
    case.queue.enqueue(case.actors["outsider"], BatchRequest(company_id=case.other_company["id"], items=[BatchItem(event_id=other["id"], attachment_id=other["attachments"][0]["id"], expected_version=other["version"])], idempotency_key="other-company-ocr"))
    own = create_event(case)
    case.queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[BatchItem(event_id=own["id"], attachment_id=own["attachments"][0]["id"], expected_version=own["version"])], idempotency_key="same-company-ocr"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda index: case.queue.claim(f"mixed-worker-{index}"), range(4)))
    jobs = [job for job in claimed if job]
    assert len(jobs) == 2 and {job["job_kind"] for job in jobs} == {"ocr", "proposal"}
    assert len({job["company_id"] for job in jobs}) == 2


def test_approval_status_change_does_not_invalidate_queued_ocr_facts(proposal_case):
    case = proposal_case
    event = create_event(case)
    batch = case.queue.enqueue(case.actors["employee"], BatchRequest(company_id=case.company["id"], items=[BatchItem(event_id=event["id"], attachment_id=event["attachments"][0]["id"], expected_version=event["version"])], idempotency_key="ocr-before-submission"))
    submitted = case.business.submit(case.actors["employee"], event["id"], event["version"])
    assert submitted["version"] != event["version"] and submitted["fact_version"] == event["fact_version"]
    assert case.worker.run_once()
    assert case.queue.get_batch(case.actors["employee"], batch["id"])["jobs"][0]["status"] == "succeeded"


def test_proposal_retry_input_requires_reason():
    with pytest.raises(ValueError):
        ProposalRetryRequest(idempotency_key="stage7-key-no-reason")
    with pytest.raises(ValueError):
        ProposalRetryRequest(idempotency_key="stage7-key-white-reason", reason="   ")


@pytest.mark.parametrize("concurrency", [0, 9, 500, True])
def test_claim_cannot_bypass_configured_maximum_even_when_called_directly(proposal_case, concurrency):
    with pytest.raises(ValueError):
        proposal_case.queue.claim("bounded-worker", concurrency=concurrency)


def test_handoff_preserved_when_automatic_ocr_capacity_is_full(proposal_case, monkeypatch):
    case = proposal_case
    def full(*_):
        raise AccountingError("当前可见企业范围待处理票据已达上限，请处理后再提交", 409)
    monkeypatch.setattr(case.queue, "_capacity", full)
    result = handoff(case, create_event(case))
    task = current(case, result)
    assert result["event"]["status"] == "handed_off"
    assert task["status"] == "needs_attention" and task["error_code"] == "queue_capacity"
    assert case.provider.extract_calls == case.provider.proposal_calls == 0


def test_rule_receipt_failure_rolls_back_draft_and_preserves_manual_attention(proposal_case, monkeypatch):
    case = proposal_case
    template(case)
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    before = draft(case, result)
    def lost_receipt(*_):
        raise AccountingError("合成任务回执冲突", 409)
    monkeypatch.setattr(ProposalExecution, "finish_rule", lost_receipt)
    assert case.worker.run_once()
    task, after = current(case, result), draft(case, result)
    assert task["status"] == "needs_attention"
    assert after["version"] == before["version"] and after["lines"] == before["lines"]
    assert case.provider.proposal_calls == 0


def test_batch_pagination_aggregates_only_visible_jobs_without_loading_history(proposal_case, monkeypatch):
    case = proposal_case
    for index in range(8):
        own, peer = create_event(case), create_event(case, actor="peer")
        case.queue.enqueue(case.actors["finance"], BatchRequest(company_id=case.company["id"], items=[BatchItem(event_id=item["id"], attachment_id=item["attachments"][0]["id"], expected_version=item["version"]) for item in (own, peer)], idempotency_key=f"sql-paged-batch-{index}"))
    statements, connect = [], case.db.connect
    def traced():
        connection = connect()
        connection.set_trace_callback(lambda statement: statements.append(statement) if statement.lstrip().upper().startswith("SELECT") else None)
        return connection
    monkeypatch.setattr(case.db, "connect", traced)
    page = case.queue.list_batches(case.actors["employee"], limit=3, offset=3)
    assert page["total"] == 8 and len(page["batches"]) == 3
    assert all(batch["progress"]["total"] == 1 and batch["jobs"] == [] for batch in page["batches"])
    assert len(statements) == 4


def test_model_warning_keeps_candidate_for_human_without_overwriting_draft(proposal_case):
    case = proposal_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    case.provider.proposal_mutator = lambda output: output["warnings"].append("无法确定业务实质，需会计核对")
    assert case.worker.run_once()
    task, value = current(case, result), draft(case, result)
    assert task["status"] == "needs_attention" and task["current_run_id"]
    assert value["version"] == result["initial_draft_version"] and value["status"] == "draft"
    with case.db.transaction() as conn:
        candidate = conn.execute("SELECT status,result_json FROM ai_runs WHERE id=?", (task["current_run_id"],)).fetchone()
        assert candidate["status"] == "succeeded" and json.loads(candidate["result_json"])["warnings"]
        assert conn.execute("SELECT COUNT(*) FROM ai_proposal_applications").fetchone()[0] == 0


def test_background_proposal_reserves_company_quota_before_external_send(proposal_cost_case):
    case = proposal_cost_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    policy(case, monthly_limit="0.01")
    assert case.worker.run_once()
    task = current(case, result)
    assert task["status"] == "failed" and task["error_code"] == "quota_exhausted"
    assert case.provider.proposal_calls == 0 and draft(case, result)["version"] == result["initial_draft_version"]
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_costs WHERE kind='proposal'").fetchone()[0] == 0


def test_background_proposal_reported_usage_remains_metered_on_success(proposal_cost_case):
    case = proposal_cost_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    policy(case)
    assert case.worker.run_once() and current(case, result)["status"] == "succeeded"
    with case.db.transaction() as conn:
        cost = conn.execute("SELECT * FROM model_costs WHERE kind='proposal'").fetchone()
        assert cost["state"] == "estimated" and cost["estimated_micro"] == 11


def test_cancel_sent_background_proposal_does_not_erase_reported_cost(proposal_cost_case):
    case = proposal_cost_case
    event = create_event(case)
    extract(case, event)
    result = handoff(case, event)
    policy(case)
    case.provider.proposal_hook = lambda: case.queue.proposals.cancel(case.actors["accountant"], result["automatic_job"]["id"], "已核对需要取消，但费用保留")
    assert case.worker.run_once()
    assert current(case, result)["status"] == "cancelled" and draft(case, result)["version"] == result["initial_draft_version"]
    with case.db.transaction() as conn:
        cost = conn.execute("SELECT * FROM model_costs WHERE kind='proposal'").fetchone()
        assert cost["state"] == "estimated" and cost["estimated_micro"] == 11
