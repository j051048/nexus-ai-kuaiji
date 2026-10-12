"""Failed model output can still have billable, explicitly reported usage."""
import json
from types import SimpleNamespace
import pytest

from app.intelligence.models import InvoiceExtraction, ProviderUsage
from app.intelligence.provider import OpenAICompatibleProvider, ProviderError
from test_model_cost_workflow import cost_case, ai_case, policy, claimed_context, before_send, cost_rows


@pytest.mark.parametrize("fault,code", [("schema", "schema_invalid"), ("refusal", "refusal"), ("truncated", "truncated"), ("json", "invalid_json")])
def test_rejected_output_retains_reported_token_evidence_for_cost(fault, code):
    message = {"content": '{"documents":[]}' if fault == "schema" else "not valid JSON"}
    if fault == "refusal":
        message["refusal"] = "supplier refusal text that is not reflected"
    body = {"choices": [{"message": message, "finish_reason": "length" if fault == "truncated" else "stop"}],
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10}}
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider._parse(json.dumps(body).encode(), InvoiceExtraction, "fixture-model", "v1")
    assert caught.value.code == code and caught.value.usage.prompt_tokens == 6
    assert caught.value.usage.completion_tokens == 4
    assert "supplier refusal text" not in str(caught.value)


@pytest.mark.parametrize("code", ["unavailable", "document_invalid", "context_too_large"])
def test_local_preflight_rejection_does_not_hold_money(cost_case, code):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    context.finish(error=ProviderError(code, "仅测试本地拒绝，未发送供应商"), attempted=True)
    assert cost_rows(case)[0]["state"] == "released"
    assert case.operations.overview(case.manager, case.company["id"])["usage"]["exposure"] == "0.000000"


def test_monitor_timeout_hold_can_accept_late_usage_but_not_override_manual_confirmation(cost_case):
    case = cost_case
    policy(case)
    context, _ = claimed_context(case)
    before_send(case, context)
    case.clock.advance(181)
    case.operations.refresh(case.manager, case.company["id"])
    assert cost_rows(case)[0]["state"] == "uncertain"
    context.finish(result=SimpleNamespace(usage=ProviderUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)), attempted=True)
    assert cost_rows(case)[0]["state"] == "estimated" and cost_rows(case)[0]["estimated_micro"] == 11
