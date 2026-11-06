"""IMP07-B: capture the actual tool-free assistant request surface."""

import json

import httpx

from app.assistant.provider import AssistantProvider
from app.intelligence.provider import AISettings, OpenAICompatibleProvider


def make_provider(handler):
    settings = AISettings(
        base_url="https://supplier.example.test/v1",
        api_key="synthetic-key",
        chat_model="synthetic-chat",
    )
    return AssistantProvider(
        OpenAICompatibleProvider(
            settings, httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        )
    )


def envelope():
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "请按当前范围核对。", "citation_ids": ["book:test"], "proposals": []}
                    ),
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


def test_actual_request_is_tool_free_and_exposes_only_bounded_inputs():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())

    provider = make_provider(handler)
    result = provider.reply(
        {"company_id": "company-a", "evidence": [{"id": "book:test"}]},
        "为什么这家企业本月还不能交付？",
        [],
    )
    assert result.data.answer == "请按当前范围核对。"
    payload = calls[0]
    assert "tools" not in payload and "tool_choice" not in payload
    assert payload["response_format"]["json_schema"]["strict"] is True
    system = payload["messages"][0]["content"]
    for required in ("只读", "不授予新权限", "禁止自行审批", "不得宣称任务已执行"):
        assert required in system
    supplied = json.loads(payload["messages"][1]["content"])
    assert set(supplied) == {
        "current_authorized_evidence",
        "untrusted_history",
        "untrusted_question",
    }
    assert supplied["untrusted_question"] == "为什么这家企业本月还不能交付？"
    assert "gold_fields" not in json.dumps(supplied, ensure_ascii=False)
    assert "candidate_version" not in json.dumps(supplied, ensure_ascii=False)


def test_prompt_fingerprint_is_stable_and_never_contains_the_secret():
    provider = make_provider(lambda _: httpx.Response(200, json=envelope()))
    first = provider.configuration_fingerprint()
    second = provider.configuration_fingerprint()
    assert first == second
    assert len(first) == 64
    assert "synthetic-key" not in first
