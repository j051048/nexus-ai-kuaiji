"""Resolved supplier routing is explicit; synthetic HTTP only, no real credentials."""
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from app.assistant.provider import AssistantProvider
from app.intake.service import IntakeService
from app.intelligence.cache import config_key
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError, effective_settings
from app.jobs.proposals import ProposalQueueService
from app.jobs.service import QueueService
from test_intelligence_provider import envelope, extraction, page, suggestion


def settings(**changes):
    return AISettings(
        ocr_base_url="https://vision.example.test/v1", ocr_api_key="synthetic-vision-secret",
        ocr_model="same-model", journal_base_url="https://journal.example.test/openai/v1",
        journal_api_key="synthetic-journal-secret", journal_model="same-model",
        chat_base_url="https://chat.example.test/v1", chat_api_key="synthetic-chat-secret",
        chat_model="same-model", **changes,
    )


def queue(config):
    instance = object.__new__(QueueService)
    instance.intelligence = SimpleNamespace(provider=SimpleNamespace(settings=config))
    instance.proposals = object.__new__(ProposalQueueService)
    instance.proposals.queue = instance
    instance.proposals.intelligence = instance.intelligence
    return instance


def fingerprints(config):
    instance = queue(config)
    chat = AssistantProvider(OpenAICompatibleProvider(config))
    intake = object.__new__(IntakeService)
    intake.provider = instance.intelligence.provider
    return [config_key(config, "image/png"), instance.configuration_fingerprint(),
            instance.proposals.configuration_fingerprint(), chat.configuration_fingerprint(),
            intake._configuration_input({"sha256": "a" * 64})]


def test_complete_scene_configuration_needs_no_common_gateway():
    config = AISettings.from_env({
        "ACCOUNTING_AI_OCR_BASE_URL": "https://vision.example.test/v1/",
        "ACCOUNTING_AI_OCR_API_KEY": "synthetic-vision-secret", "ACCOUNTING_AI_OCR_MODEL": "ocr-model",
        "ACCOUNTING_AI_JOURNAL_BASE_URL": "https://journal.example.test/v1",
        "ACCOUNTING_AI_JOURNAL_API_KEY": "synthetic-journal-secret", "ACCOUNTING_AI_JOURNAL_MODEL": "journal-model",
        "ACCOUNTING_AI_CHAT_BASE_URL": "https://chat.example.test/v1",
        "ACCOUNTING_AI_CHAT_API_KEY": "synthetic-chat-secret", "ACCOUNTING_AI_CHAT_MODEL": "chat-model",
        "ACCOUNTING_AI_CHAT_RESPONSE_FORMAT": "json_object",
    })
    assert not config.base_url and not config.api_key
    assert config.extraction_available and config.suggestion_available and config.chat_available
    assert config.for_scenario("ocr").base_url == "https://vision.example.test/v1"
    assert config.for_scenario("ocr").model == "ocr-model"
    assert config.for_scenario("journal").model == "journal-model"
    assert config.for_scenario("chat").response_format == "json_object"
    assert config.for_scenario("journal").response_format == "json_schema"
    assert "synthetic-" not in repr(config)
    assert "synthetic-" not in repr(config.for_scenario("ocr"))


def test_legacy_one_gateway_still_resolves_all_scenes_and_per_model_override():
    config = AISettings.from_env({
        "ACCOUNTING_AI_BASE_URL": "https://common.example.test/v1", "ACCOUNTING_AI_API_KEY": "synthetic-old-secret",
        "ACCOUNTING_AI_VISION_MODEL": "old-vision", "ACCOUNTING_AI_JOURNAL_MODEL": "old-journal",
        "ACCOUNTING_AI_CHAT_MODEL": "old-chat", "ACCOUNTING_AI_OCR_MODEL": "new-vision",
    })
    assert [config.for_scenario(scene).model for scene in ("ocr", "journal", "chat")] == ["new-vision", "old-journal", "old-chat"]
    assert {config.for_scenario(scene).base_url for scene in ("ocr", "journal", "chat")} == {config.base_url}
    assert all(config.for_scenario(scene).api_key == "synthetic-old-secret" for scene in ("ocr", "journal", "chat"))


@pytest.mark.parametrize("scene", ["OCR", "JOURNAL", "CHAT"])
@pytest.mark.parametrize("field,value", [("BASE_URL", "https://other.example.test/v1"), ("API_KEY", "synthetic-private-override")])
def test_partial_scene_override_cannot_forward_old_secret_to_new_supplier(scene, field, value):
    with pytest.raises(ProviderError) as caught:
        AISettings.from_env({"ACCOUNTING_AI_BASE_URL": "https://legacy.example.test/v1", "ACCOUNTING_AI_API_KEY": "synthetic-old-secret",
                             f"ACCOUNTING_AI_{scene}_{field}": value})
    assert caught.value.code == "configuration"
    assert "synthetic-" not in str(caught.value) and "example.test" not in str(caught.value)


@pytest.mark.parametrize("scene", ["ocr", "journal", "chat"])
@pytest.mark.parametrize("value", ["http://external.example.test/v1", "https://user:secret@example.test/v1", "https://example.test/v1?token=secret"])
def test_each_scene_url_uses_same_safe_validation(scene, value):
    with pytest.raises(ProviderError) as caught:
        AISettings(**{scene + "_base_url": value, scene + "_api_key": "synthetic-secret"})
    assert caught.value.code == "configuration" and "secret" not in str(caught.value)


def test_same_model_names_go_to_exact_scene_endpoints_and_credentials():
    seen = []
    def handler(request):
        payload = json.loads(request.content)
        seen.append((str(request.url), request.headers["authorization"], payload))
        data = {"vision.example.test": extraction(), "journal.example.test": suggestion(),
                "chat.example.test": {"answer": "请核对证据", "citation_ids": [], "proposals": []}}[request.url.host]
        return httpx.Response(200, json=envelope(data))
    config = settings(chat_response_format="json_object")
    provider = OpenAICompatibleProvider(config, httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.extract_invoice([page()]).model == "same-model"
    assert provider.suggest_journal({}).model == "same-model"
    assert AssistantProvider(provider).reply({}, "说明", []).model == "same-model"
    assert [(url, key) for url, key, _ in seen] == [
        ("https://vision.example.test/v1/chat/completions", "Bearer synthetic-vision-secret"),
        ("https://journal.example.test/openai/v1/chat/completions", "Bearer synthetic-journal-secret"),
        ("https://chat.example.test/v1/chat/completions", "Bearer synthetic-chat-secret"),
    ]
    assert all(payload["model"] == "same-model" for _, _, payload in seen)
    assert seen[0][2]["response_format"]["type"] == "json_schema"
    assert seen[2][2]["response_format"] == {"type": "json_object"}
    assert seen[2][2]["max_tokens"] == 4096


def test_two_supplier_configuration_can_share_one_explicit_pair():
    config = settings()
    config = replace(config, chat_base_url=config.journal_base_url, chat_api_key=config.journal_api_key)
    assert config.for_scenario("ocr").base_url != config.for_scenario("chat").base_url
    assert config.for_scenario("chat").base_url == config.for_scenario("journal").base_url


def test_ocr_reuse_and_queue_fingerprints_do_not_depend_on_other_scenes_or_secrets():
    config = settings()
    before = fingerprints(config)
    rotated = replace(config, ocr_api_key="synthetic-rotated-vision", journal_api_key="synthetic-rotated-journal", chat_api_key="synthetic-rotated-chat")
    assert fingerprints(rotated) == before
    changed_chat = fingerprints(replace(config, chat_base_url="https://other-chat.example.test/v1", chat_model="chat-new"))
    assert changed_chat[:3] == before[:3] and changed_chat[4] == before[4]
    assert changed_chat[3] != before[3]
    changed_journal = fingerprints(replace(config, journal_base_url="https://other-journal.example.test/v1", journal_response_format="json_object"))
    assert changed_journal[:2] == before[:2] and changed_journal[3:] == before[3:]
    assert changed_journal[2] != before[2]
    for change in [{"ocr_base_url": "https://new-vision.example.test/v1"}, {"ocr_model": "ocr-new"}, {"ocr_response_format": "json_object"}]:
        changed_ocr = fingerprints(replace(config, **change))
        assert all(changed_ocr[index] != before[index] for index in (0, 1, 2, 4))
        assert changed_ocr[3] == before[3]


def test_all_scenes_share_the_process_concurrency_limit():
    seen = []
    provider = OpenAICompatibleProvider(settings(max_concurrency=1), httpx.Client(transport=httpx.MockTransport(lambda request: seen.append(request))))
    provider._semaphore.acquire()
    try:
        for call in [lambda: provider.extract_invoice([page()]), lambda: provider.suggest_journal({}),
                     lambda: AssistantProvider(provider).reply({}, "说明", [])]:
            with pytest.raises(ProviderError) as caught:
                call()
            assert caught.value.code == "busy"
    finally:
        provider._semaphore.release()
    assert not seen


def test_unknown_timeout_never_calls_a_different_supplier_or_retries():
    seen = []
    def handler(request):
        seen.append(request.url.host)
        raise httpx.ReadTimeout("synthetic unknown result", request=request)
    provider = OpenAICompatibleProvider(settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError) as caught:
        provider.suggest_journal({})
    assert caught.value.code == "timeout" and not caught.value.retryable
    assert seen == ["journal.example.test"]


def test_fake_provider_settings_can_keep_original_shape():
    legacy = SimpleNamespace(base_url="https://legacy.example.test/v1", vision_model="v", journal_model="j", response_format="json_schema")
    assert effective_settings(legacy, "ocr").model == "v"
    assert effective_settings(legacy, "journal").model == "j"
    with pytest.raises(ProviderError):
        effective_settings(legacy, "anything")
