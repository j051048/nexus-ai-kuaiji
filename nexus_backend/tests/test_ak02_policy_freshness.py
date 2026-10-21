"""Policy proof stays current through result publication, not only egress."""
import pytest

from app.intelligence.endpoints import CompanyPolicyUpdate
from test_intelligence_workflow import ai_case  # noqa: F401
from test_model_cost_workflow import cost_case  # noqa: F401
from test_ak01_service_limits import service_case, execute, costs  # noqa: F401


@pytest.mark.parametrize("change", ["deny_chat", "deny_data", "revision"])
def test_assistant_policy_change_during_call_fences_reply_but_retains_usage(service_case, change):
    case = service_case.setup("chat")

    def change_policy(_):
        case.endpoint_service.update_policy(case.manager, case.company["id"], CompanyPolicyUpdate(
            allowed_scenarios=["ocr"] if change == "deny_chat" else ["ocr", "journal", "chat"],
            allowed_data_categories=["document"] if change == "deny_data" else ["document", "counterparty", "amount", "accounting", "chat"],
            allowed_endpoint_ids=[case.endpoint["id"]],
            reason="AK02 合成在途政策变更",
        ))

    case.response_hook = change_policy
    result = execute(case)
    assert result["status"] == "stale"
    assert not result.get("reply")
    row, = costs(case)
    assert row["state"] == "estimated"
    assert (row["input_tokens"], row["output_tokens"]) == (3, 4)
    assert len(case.calls) == 1
    execute(case)
    assert len(case.calls) == 1
