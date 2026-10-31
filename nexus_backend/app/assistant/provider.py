"""Accounting dialogue through the same bounded, tool-free model transport."""

import hashlib
import json

from ..intelligence.provider import ExecutionLimits, ProviderError, effective_settings
from .request_budget import CONTEXT_BUDGET_VERSION, SYSTEM_PROMPT, encode_pack

PROMPT_VERSION = "accounting-assistant-facts-v3"


class AssistantProvider:
    def __init__(self, shared_provider):
        self.transport = shared_provider
        self.settings = shared_provider.settings

    @property
    def available(self):
        return bool(getattr(self.settings, "chat_available", False))

    def configuration_fingerprint(self):
        settings = effective_settings(self.settings, "chat")
        return hashlib.sha256(
            json.dumps(
                {
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "format": settings.response_format,
                    "prompt": PROMPT_VERSION,
                    "context_budget": CONTEXT_BUDGET_VERSION,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def reply(
        self,
        context,
        question,
        history,
        *,
        limits: ExecutionLimits | None = None,
        context_pack=None,
    ):
        from .models import AssistantModelReply

        if not self.available:
            raise ProviderError(
                "unavailable", "会计助手模型尚未配置，请联系管理员；现有核对流程仍可使用"
            )
        try:
            evidence = json.dumps(context, ensure_ascii=False, allow_nan=False)
            past = json.dumps(history, ensure_ascii=False, allow_nan=False)
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
                raise ValueError
        except (TypeError, ValueError):
            raise ProviderError("context_invalid", "会计助手上下文格式无效") from None
        if context_pack is None and (len(evidence.encode()) > 65536 or len(past.encode()) > 49152):
            raise ProviderError(
                "context_too_large", "会计助手资料超过单轮范围，请缩小账套与事项范围"
            )
        if context_pack is not None:
            from .context_pack import validate_assistant_context_pack

            supplied = {
                "context_pack": validate_assistant_context_pack(
                    context_pack,
                    context,
                    question,
                    history,
                    limits=limits,
                    prompt_version=PROMPT_VERSION,
                    response_format=effective_settings(self.settings, "chat").response_format,
                )
            }
        else:
            # Compatibility for direct gateway callers. Persistent worker calls
            # always supply a pack derived from their freshly authorized snapshot.
            supplied = {
                "current_authorized_evidence": context,
                "untrusted_history": history,
                "untrusted_question": question,
            }
        return self.transport._request(
            model=effective_settings(self.settings, "chat").model,
            output_type=AssistantModelReply,
            scenario="chat",
            prompt_version=PROMPT_VERSION,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            user_content=(
                encode_pack(supplied["context_pack"])
                if context_pack is not None
                else json.dumps(supplied, ensure_ascii=False, allow_nan=False)
            ),
            limits=limits,
        )
