"""Independent OCR execution boundaries: synthetic transport, no real model or network."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.intelligence.provider import ExecutionLimits, ProviderError
from app.execution.records import execution_record
from test_paddle_evidence_restoration import provider_case, page, SOURCE
from test_intelligence_workflow import ai_case, create_event, extract  # noqa: F401


def _failed_run(code):
    return {
        "id": "synthetic-run",
        "firm_id": "synthetic-firm",
        "company_id": "synthetic-company",
        "kind": "ocr",
        "status": "failed",
        "error_code": code,
        "error_detail": "PRIVATE synthetic provider detail",
    }


@pytest.mark.parametrize(
    "code,category",
    [
        ("ocr_service_unavailable", "provider_or_transport"),
        ("ocr_timeout", "provider_or_transport"),
        ("ocr_response_invalid", "output_validation"),
        ("ocr_source_invalid", "authorization_or_scope"),
        ("ocr_document_too_large", "input_or_context"),
    ],
)
def test_independent_ocr_failure_has_specific_execution_category(code, category):
    run = _failed_run(code)
    # Reservation/send marking precedes OCR. A released field-model reservation
    # still retains sent_at, which must not imply an unknown LLM outcome.
    cost = {
        "id": run["id"],
        "run_id": run["id"],
        "firm_id": run["firm_id"],
        "company_id": run["company_id"],
        "state": "released",
        "sent_at": "2026-10-27T00:00:00+00:00",
    }
    result = execution_record("ocr", run, costs=[cost], diagnostics=True)
    assert result["failure"]["category"] == category
    assert result["failure"]["code"] == code
    assert result["failure"]["outcome_unknown"] is False
    assert result["handoff"]["required"] is True
    assert result["handoff"]["automatic_retry"] is False
    assert result["diagnostics"]["costs"][0]["state"] == "released"
    assert "PRIVATE" not in str(result)


def test_field_model_timeout_keeps_unknown_outcome_and_cost_evidence():
    run = _failed_run("timeout")
    cost = {
        "id": run["id"],
        "run_id": run["id"],
        "firm_id": run["firm_id"],
        "company_id": run["company_id"],
        "state": "uncertain",
        "sent_at": "2026-10-27T00:00:00+00:00",
        "estimated_micro": None,
    }
    result = execution_record("ocr", run, costs=[cost], diagnostics=True)
    assert result["failure"]["category"] == "provider_or_transport"
    assert result["failure"]["code"] == "timeout"
    assert result["failure"]["outcome_unknown"] is True
    assert result["failure"]["automatic_retry"] is False
    assert result["diagnostics"]["costs"][0]["state"] == "uncertain"
    assert result["diagnostics"]["costs"][0]["estimated_micro"] is None


def test_shared_deadline_carries_remaining_time_to_field_model(monkeypatch):
    import app.intelligence.ocr as ocr
    import app.intelligence.provider as transport

    clock = [100.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(ocr, "time", fake_time)
    monkeypatch.setattr(transport, "time", fake_time)
    provider, calls, _, client = provider_case(ocr_hook=lambda: clock.__setitem__(0, 104.0))
    with client:
        result = provider.extract_invoice(
            [page()],
            source_binding=SOURCE,
            limits=ExecutionLimits(timeout_seconds=5, deadline_monotonic=105),
        )
    assert result.usage.total_tokens == 120
    assert len(calls) == 2
    assert all(value <= 1 for value in calls[-1].extensions["timeout"].values())


def test_shared_deadline_expired_in_ocr_never_calls_field_model(monkeypatch):
    import app.intelligence.ocr as ocr

    clock = [100.0]
    monkeypatch.setattr(ocr, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    provider, calls, _, client = provider_case(ocr_hook=lambda: clock.__setitem__(0, 106.0))
    with client, pytest.raises(ProviderError) as failure:
        provider.extract_invoice(
            [page()],
            source_binding=SOURCE,
            limits=ExecutionLimits(timeout_seconds=5, deadline_monotonic=105),
        )
    assert failure.value.llm_attempted is False
    assert failure.value.code == "ocr_timeout"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response_kind",
    ["invalid_content_with_usage", "unavailable_without_usage", "forged_independent_source"],
)
def test_after_field_http_attempt_cost_is_never_released(ai_case, response_kind):
    provider, calls, _, original_client = provider_case()
    handler = original_client._transport.handler

    def replacement(request):
        if request.url.host == "ocr.example.invalid":
            return handler(request)
        calls.append(request)
        if response_kind == "unavailable_without_usage":
            return httpx.Response(503, json={"error": "synthetic"})
        content = "invalid json"
        if response_kind == "forged_independent_source":
            content = json.dumps({"documents": [], "ocr_evidence": {"engine": "paddleocr"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(replacement))
    provider._client = provider._paddle_client = client
    ai_case.intelligence.provider = provider
    event = create_event(ai_case)
    with original_client, client:
        result = extract(ai_case, event)
    assert result["status"] == "failed"
    assert len(calls) == 2
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        cost = conn.execute("SELECT state,input_tokens,output_tokens FROM model_costs").fetchone()
        assert cost["state"] != "released"
        if response_kind != "unavailable_without_usage":
            assert (cost["input_tokens"], cost["output_tokens"]) == (80, 40)


def test_model_local_preflight_after_paddle_proves_no_attempt():
    provider, calls, _, client = provider_case()
    with client, pytest.raises(ProviderError) as failure:
        provider.extract_invoice(
            [page()], source_binding=SOURCE, limits=ExecutionLimits(context_tokens=1)
        )
    assert failure.value.code == "context_too_large"
    assert failure.value.llm_attempted is False
    assert len(calls) == 1


def test_deeply_nested_ocr_response_releases_unattempted_field_cost(ai_case):
    provider, calls, _, original_client = provider_case()

    def nested_response(request):
        calls.append(request)
        return httpx.Response(200, content=("[" * 10000 + "0" + "]" * 10000).encode())

    client = httpx.Client(transport=httpx.MockTransport(nested_response))
    provider._client = provider._paddle_client = client
    ai_case.intelligence.provider = provider
    event = create_event(ai_case)
    with original_client, client:
        result = extract(ai_case, event)
    assert result["status"] == "failed"
    assert len(calls) == 1
    assert calls[0].url.host == "ocr.example.invalid"
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        stored = conn.execute(
            "SELECT error_code FROM ai_runs WHERE id=?", (result["id"],)
        ).fetchone()
        assert stored["error_code"] == "ocr_response_invalid"
        cost = conn.execute("SELECT state FROM model_costs").fetchone()
        assert cost["state"] == "released"
