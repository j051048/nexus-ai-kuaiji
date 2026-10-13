"""Synthetic transport validates the real gateway; no external calls or real keys."""
import json

import httpx
import pytest

from app.assistant.provider import AssistantProvider
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError


def make_provider(handler, **extra):
    settings = AISettings(base_url="https://supplier.example.test/v1", api_key="synthetic-key",
                          chat_model="synthetic-chat", **extra)
    return AssistantProvider(OpenAICompatibleProvider(settings, httpx.Client(transport=httpx.MockTransport(handler))))


def envelope(content=None, **choice):
    reply = content or {"answer": "请核对当前凭证。", "citation_ids": ["book:test"], "proposals": []}
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply), **choice}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}


def test_real_gateway_structured_bounded_tool_free_request():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())
    provider = make_provider(handler)
    result = provider.reply({"company_id": "company-a", "evidence": [{"id": "book:test"}]}, "说明依据", [])
    assert result.data.answer == "请核对当前凭证。"
    assert result.provider == "openai_compatible" and result.usage.total_tokens == 30
    payload = calls[0]
    assert payload["model"] == "synthetic-chat" and payload["max_tokens"] == 4096
    assert payload["store"] is False and payload["stream"] is False
    assert "tools" not in payload and "tool_choice" not in payload
    assert payload["response_format"]["json_schema"]["strict"] is True
    supplied = json.loads(payload["messages"][1]["content"])
    assert supplied["current_authorized_evidence"]["company_id"] == "company-a"
    assert "禁止自行审批" in payload["messages"][0]["content"]


@pytest.mark.parametrize("content", [
    {"answer": "已导出", "citation_ids": [], "proposals": [{"kind": "export", "reason": "立即执行"}]},
    {"answer": "文字", "citation_ids": [], "proposals": [], "tool_call": "arbitrary"},
])
def test_arbitrary_operations_or_extra_output_are_rejected(content):
    provider = make_provider(lambda _: httpx.Response(200, json=envelope(content)))
    with pytest.raises(ProviderError):
        provider.reply({}, "测试", [])


def test_tool_calls_and_unconfigured_provider_cannot_fake_an_answer():
    provider = make_provider(lambda _: httpx.Response(200, json=envelope(tool_calls=[{"id": "x"}])))
    with pytest.raises(ProviderError):
        provider.reply({}, "测试", [])
    blank = AssistantProvider(OpenAICompatibleProvider(AISettings()))
    assert not blank.available
    with pytest.raises(ProviderError, match="尚未配置"):
        blank.reply({}, "测试", [])


def test_limits_before_network_and_shared_model_concurrency():
    calls = []
    provider = make_provider(lambda _: calls.append(1) or httpx.Response(200, json=envelope()), max_concurrency=1)
    for context, question, history in [({"x": "中" * 30000}, "测试", []), ({}, "测试", [{"x": "中" * 20000}]), ({}, " " * 5, [])]:
        with pytest.raises(ProviderError):
            provider.reply(context, question, history)
    provider.transport._semaphore.acquire()
    try:
        with pytest.raises(ProviderError) as rejected:
            provider.reply({}, "测试", [])
        assert rejected.value.code == "busy"
    finally:
        provider.transport._semaphore.release()
    assert not calls


def test_explicit_chat_env_fingerprint_excludes_secret_and_changes_model():
    settings = AISettings.from_env({"ACCOUNTING_AI_CHAT_MODEL": "chat-a"})
    assert settings.chat_model == "chat-a" and not settings.chat_available
    a = AssistantProvider(OpenAICompatibleProvider(AISettings(chat_model="chat-a", api_key="secret-a")))
    b = AssistantProvider(OpenAICompatibleProvider(AISettings(chat_model="chat-a", api_key="secret-b")))
    c = AssistantProvider(OpenAICompatibleProvider(AISettings(chat_model="chat-b")))
    assert a.configuration_fingerprint() == b.configuration_fingerprint()
    assert a.configuration_fingerprint() != c.configuration_fingerprint()
    assert "secret-a" not in repr(a.settings)


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadError])
def test_real_transport_network_errors_are_safe_unknown_errors(error):
    def fail(request):
        raise error("synthetic disconnected supplier", request=request)
    provider = make_provider(fail)
    with pytest.raises(ProviderError) as caught:
        provider.reply({}, "合成网络错误验收", [])
    assert caught.value.code == "network_error"
    assert "disconnected supplier" not in caught.value.detail
