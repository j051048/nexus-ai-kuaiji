"""AK04 execution projections consume durable facts, never grant authority."""
from copy import deepcopy

import pytest


def row(**changes):
    return dict(id="run-a", firm_id="firm-a", company_id="company-a", kind="ocr",
                status="failed", error_code="timeout", error_detail="SECRET supplier prompt",
                created_at="2026-10-21T00:00:00+00:00", finished_at="2026-10-21T00:00:01+00:00",
                input_fingerprint="a" * 64, **changes)


def project(kind="ocr", run=None, **kwargs):
    from app.execution.records import execution_record
    return execution_record(kind, run or row(), **kwargs)


def test_no_diagnostics_or_raw_supplier_text_in_business_record():
    result = project()
    assert result["schema"] == "ak04-execution-v1"
    assert result["execution_id"] == "ocr:run-a"
    assert "diagnostics" not in result
    assert "SECRET" not in str(result)
    assert "supplier" not in str(result)
    assert "input_fingerprint" not in result


def test_sent_unknown_is_not_free_or_a_retry_instruction():
    cost = dict(id="run-a", run_id="run-a", firm_id="firm-a", company_id="company-a",
                state="uncertain", sent_at="2026-10-21T00:00:00+00:00",
                input_tokens=None, output_tokens=None, estimated_micro=None)
    result = project(costs=[cost], diagnostics=True)
    assert result["failure"]["outcome_unknown"] is True
    assert result["failure"]["automatic_retry"] is False
    assert result["handoff"]["required"] is True
    assert result["diagnostics"]["costs"][0]["estimated_micro"] is None
    assert result["diagnostics"]["context_pack_hash"] is None


@pytest.mark.parametrize("value", [0, 17, None])
def test_known_zero_known_positive_unknown_are_distinct(value):
    cost = dict(id="run-a", run_id="run-a", firm_id="firm-a", company_id="company-a",
                state="estimated" if value is not None else "uncertain",
                estimated_micro=value, charge_token="SECRET", pricing_json="SECRET",
                input_tokens=0 if value == 0 else None, output_tokens=0 if value == 0 else None)
    output = project(costs=[cost], diagnostics=True)
    assert output["diagnostics"]["costs"][0]["estimated_micro"] == value
    assert "charge_token" not in str(output) and "pricing_json" not in str(output)


@pytest.mark.parametrize("defect", ["firm_id", "company_id", "run_id"])
def test_foreign_cost_evidence_cannot_be_attached_to_execution(defect):
    cost = dict(id="run-a", run_id="run-a", firm_id="firm-a", company_id="company-a", state="uncertain")
    cost[defect] = "foreign"
    from app.execution.records import ExecutionRecordError
    with pytest.raises(ExecutionRecordError):
        project(costs=[cost], diagnostics=True)


def test_claim_validation_is_not_accounting_approval():
    run = row(); run.update(status="succeeded", error_code="ak04_fact_verified")
    result = project("chat", run)
    assert result["validation"]["code"] == "verified"
    assert result["failure"] is None
    assert result["accounting_approved"] is False
    assert result["handoff"]["required"] is False


@pytest.mark.parametrize("code", ["missing", "conflict", "invalid", "unsupported", "accountant"])
def test_completed_safe_abstention_remains_human_work(code):
    run = row(); run.update(status="succeeded", error_code="ak04_fact_" + code)
    output = project("chat", run)
    assert output["status"] == "succeeded"
    assert output["validation"]["code"] != "verified"
    assert output["handoff"]["required"] is True


def test_projection_is_deterministic_and_does_not_mutate_evidence():
    run = row(); before = deepcopy(run)
    assert project(run=run) == project(run=run)
    assert before == run


def test_unknown_error_code_and_nested_finding_payload_do_not_leak():
    run = row(); run["error_code"] = "SECRET <script>"
    output = project(run=run, findings=[{"code": "document_1_original_unverified", "severity": "blocking", "payload": {"secret": "SECRET"}}])
    assert output["failure"]["category"] == "worker_internal"
    assert "SECRET" not in str(output) and "<script>" not in str(output)


def test_bare_historical_success_does_not_claim_fact_validation():
    run = row(); run.update(status="succeeded", error_code="")
    assert project("chat", run)["validation"]["code"] == "not_checked"


def test_terminal_timeline_has_only_observed_stages():
    output = project()
    assert [item["stage"] for item in output["timeline"]] == ["created", "finished"]
    assert all(item["source"] == "execution" for item in output["timeline"])


def test_unproven_queue_interruption_is_not_claimed_to_be_sent():
    run = row(); run.update(status="stale", error_code="queue_interrupted")
    output = project(run=run)
    assert output["failure"]["outcome_unknown"] is False
