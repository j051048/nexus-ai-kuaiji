import json
import time

import httpx
import pytest

from app.assistant.provider import AssistantProvider
from app.intelligence.models import PreparedPage
from app.intelligence.provider import (
    AISettings,
    ExecutionLimits,
    OpenAICompatibleProvider,
    ProviderError,
)


def envelope(content):
    return {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}]}


def extraction():
    return {"documents": [{
        "document_type": "invoice", "invoice_number": None, "issued_on": None,
        "seller_name": None, "seller_tax_id": None, "buyer_name": None, "buyer_tax_id": None,
        "gross_amount": None, "net_amount": None, "tax_amount": None, "currency": None,
        "pages": [{"page": 1, "raw_text": "x"}], "evidence": [],
    }]}


def journal():
    return {
        "description": "采购", "lines": [
            {"account_code": "6602", "debit": "1.00", "credit": "0"},
            {"account_code": "1002", "debit": "0", "credit": "1.00"},
        ], "reasoning": "依据", "evidence_refs": [], "warnings": [],
    }


def reply():
    return {"answer": "依据当前资料", "citation_ids": [], "proposals": []}


def provider(handler, **changes):
    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    settings = AISettings(
        base_url="https://supplier.example.test/v1", api_key="test",
        vision_model="vision", journal_model="journal", chat_model="chat", **changes,
    )
    return OpenAICompatibleProvider(settings, client=client)


def test_l01_context_limit_blocks_before_send():
    calls = []
    result_provider = provider(lambda request: calls.append(request) or httpx.Response(200, json=envelope(extraction())))
    page = PreparedPage(1, "data:image/png;base64,ZmFrZQ==", "长" * 20000)
    with pytest.raises(ProviderError) as caught:
        result_provider.extract_invoice([page], limits=ExecutionLimits(context_tokens=1, output_tokens=100))
    assert caught.value.code == "context_too_large"
    assert calls == []


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_l02_each_scenario_sends_effective_output_limit(scenario):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        content = extraction() if scenario == "ocr" else journal() if scenario == "journal" else reply()
        return httpx.Response(200, json=envelope(content))
    instance = provider(handler)
    limits = ExecutionLimits(output_tokens=1234)
    if scenario == "ocr":
        instance.extract_invoice([PreparedPage(1, "data:image/png;base64,ZmFrZQ==")], limits=limits)
    elif scenario == "journal":
        instance.suggest_journal({"accounts": []}, limits=limits)
    else:
        instance._request(
            model="chat", output_type=__import__("app.assistant.models", fromlist=["AssistantReply"]).AssistantReply,
            scenario="chat", prompt_version="v1", system="s", user_content="u", limits=limits,
        )
    assert calls[0]["max_tokens"] == 1234


def test_f01_f02_f03_http_429_usage_is_parsed_without_content():
    for usage, expected in [
        ({"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10}, 10),
        ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, 0),
        (None, None),
    ]:
        body = {"error": {"message": "busy"}}
        if usage is not None:
            body["usage"] = usage
        calls = []
        instance = provider(lambda request: calls.append(request) or httpx.Response(429, json=body))
        with pytest.raises(ProviderError) as caught:
            instance.extract_invoice([PreparedPage(1, "data:image/png;base64,ZmFrZQ==")])
        assert caught.value.code == "http_error" and caught.value.retryable is True
        assert len(calls) == 1
        if expected is None:
            assert caught.value.usage is None
        else:
            assert caught.value.usage.total_tokens == expected


def test_l03_transport_uses_effective_remaining_timeout():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=envelope(extraction()))
    instance = provider(handler, timeout_seconds=45)
    instance.extract_invoice(
        [PreparedPage(1, "data:image/png;base64,ZmFrZQ==")],
        limits=ExecutionLimits(timeout_seconds=1, output_tokens=100),
    )
    timeout = calls[0].extensions.get("timeout")
    assert timeout is not None
    assert max(timeout.values()) <= 1


def test_l04_request_cannot_raise_output_limit():
    calls = []
    instance = provider(lambda request: calls.append(json.loads(request.content)) or httpx.Response(200, json=envelope(journal())))
    instance.suggest_journal({}, limits=ExecutionLimits(output_tokens=10))
    assert calls[0]["max_tokens"] == 10


def test_l06_expired_deadline_does_not_send():
    calls = []
    instance = provider(lambda request: calls.append(request) or httpx.Response(200, json=envelope(extraction())))
    with pytest.raises(ProviderError) as caught:
        instance.extract_invoice(
            [PreparedPage(1, "data:image/png;base64,ZmFrZQ==")],
            limits=ExecutionLimits(timeout_seconds=1, deadline_monotonic=time.monotonic() - 1),
        )
    assert caught.value.code == "timeout"
    assert calls == []
