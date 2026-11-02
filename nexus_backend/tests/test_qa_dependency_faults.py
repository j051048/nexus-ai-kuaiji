"""Real entry/meter/transport chain, injected failures; never contact a supplier."""

import httpx
import pytest

from test_ak01_service_limits import service_case, execute, costs  # noqa: F401
from test_intelligence_workflow import ai_case  # noqa: F401
from test_model_cost_workflow import cost_case  # noqa: F401


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
@pytest.mark.parametrize("failure", ["gateway_timeout", "read_disconnect", "read_timeout"])
def test_sent_dependency_failure_keeps_unknown_cost_and_no_transport_retry(
    service_case, scenario, failure
):
    case = service_case.setup(scenario)
    if failure == "gateway_timeout":
        case.response_status = 504
        case.response_body = {"error": "synthetic upstream unavailable"}
    else:

        def disconnect(request):
            if failure == "read_timeout":
                raise httpx.ReadTimeout("synthetic timeout after send", request=request)
            raise httpx.ReadError("synthetic disconnect after send", request=request)

        case.response_hook = disconnect
    result = execute(case)
    assert result["status"] != "succeeded"
    assert len(case.calls) == 1
    (row,) = costs(case)
    assert row["state"] == "uncertain"
    assert row["input_tokens"] is None and row["output_tokens"] is None
    if scenario == "chat":
        # A new worker must not resend a possibly completed remote request.
        from app.assistant.worker import AssistantWorker

        worker = AssistantWorker(case.chat_service, case.chat_provider, case.chat_worker.meter)
        worker.run_once()
        assert len(case.calls) == 1
