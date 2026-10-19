from decimal import Decimal
import hashlib

import pytest

from tools.ai_inference_lab.contracts import (
    AttemptConflict, CallRequest, CancelToken, Capabilities, Cost, Endpoint,
    EnterprisePolicy, Executor, Failure, MockTransport, RunLimits, Scope, Status,
    TransportError, TrustedConfig,
)


def scope(company="company-a", firm="firm-a"):
    return Scope(firm, company, "ledger-a", "book-a", "2026-10")


def policy(local_only=False, providers=("mock", "cloud")):
    return EnterprisePolicy(
        allowed_firm_id="firm-a",
        allowed_company_ids=frozenset({"company-a", "company-b"}),
        allowed_providers=frozenset(providers),
        local_only=local_only,
    )


def request(**changes):
    base = dict(
        request_id="req-1", run_id="run-1", attempt_id="attempt-1", sample_id="A1",
        scope=scope(), input_sha256=hashlib.sha256(b"frozen input").hexdigest(), input_text="frozen input", scenario="invoice", schema_version="v1",
        policy_version="p1", provider="mock", model="mock-model", endpoint="mock://local",
        provider_is_local=True, allowed_data="ocr_text", max_output_tokens=100,
        endpoint_id="mock-endpoint",
        max_calls=3, deadline_seconds=30, capabilities=Capabilities(structured_output=True, usage_receipt=True),
        required_capabilities=frozenset({"structured_output"}),
        output_schema={"required": ["gross_amount"], "properties": {"gross_amount": {"type": "string"}}},
    )
    base.update(changes)
    return CallRequest(**base)


def trusted(local_only=False, endpoint_id="mock-endpoint"):
    endpoint = Endpoint(
        endpoint_id=endpoint_id,
        url="mock://local" if endpoint_id == "mock-endpoint" else "https://cloud.example/v1",
        provider="mock" if endpoint_id == "mock-endpoint" else "cloud",
        model="mock-model",
        is_local=endpoint_id == "mock-endpoint",
        capabilities=Capabilities(structured_output=True, usage_receipt=True),
    )
    return TrustedConfig(
        scopes={"company-a": scope(), "company-b": scope("company-b")},
        endpoints={endpoint_id: endpoint},
        local_only=local_only,
    )


def _exec(transport, p=None, *, trusted=None, limits=None, **kwargs):
    return Executor(
        transport, p or policy(),
        trusted=trusted if trusted is not None else globals()["trusted"](),
        limits=limits if limits is not None else RunLimits(3, 30, 100),
        **kwargs,
    )


def payload(**changes):
    value = {
        "content": '{"gross_amount":"106.00"}',
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "cost": "0.001",
    }
    value.update(changes)
    return value


def test_normal_structured_response_and_known_usage_cost():
    result = _exec(MockTransport({"A1": payload()}), policy()).invoke(request())
    assert result.status == Status.SUCCEEDED
    assert result.structured == {"gross_amount": "106.00"}
    assert result.usage.known is True and result.usage.total_tokens == 2
    assert result.cost.known is True and result.cost.amount == Decimal("0.001")
    assert result.sent and result.received and result.complete


@pytest.mark.parametrize("content", ['{"a":1,"a":2}', '{"a":NaN}', "not-json", ""])
def test_invalid_or_duplicate_json_is_rejected(content):
    result = _exec(MockTransport({"A1": payload(content=content)}), policy()).invoke(request())
    assert result.status == Status.FAILED
    assert result.failure == Failure.INVALID_RESPONSE


def test_schema_mismatch_refusal_truncation_and_tool_call():
    for response, failure in [
        (payload(content='{"gross_amount":106}'), Failure.INVALID_RESPONSE),
        (payload(refused=True), Failure.REFUSED),
        (payload(finish_reason="length"), Failure.TRUNCATED),
        (payload(tool_calls=[{"name": "shell"}]), Failure.UNSUPPORTED),
    ]:
        result = _exec(MockTransport({"A1": response}), policy()).invoke(request())
        assert result.status == Status.FAILED
        assert result.failure == failure


def test_missing_usage_and_price_are_unknown_not_zero():
    response = payload()
    response.pop("usage")
    response.pop("cost")
    result = _exec(MockTransport({"A1": response}), policy()).invoke(request())
    assert result.status == Status.SUCCEEDED
    assert result.usage.known is False and result.usage.total_tokens is None
    assert result.cost.known is False and result.cost.amount is None


def test_explicit_zero_usage_and_cost_are_known():
    response = payload(usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, cost="0")
    result = _exec(MockTransport({"A1": response}), policy()).invoke(request())
    assert result.usage.known is True and result.usage.total_tokens == 0
    assert result.cost.known is True and result.cost.amount == Decimal("0")


def test_cancellation_before_and_after_send():
    token = CancelToken()
    token.cancel()
    pre = _exec(MockTransport({"A1": payload()}), policy()).invoke(request(cancel=token))
    assert pre.status == Status.CANCELLED and pre.failure == Failure.CANCELLED_PRE_SEND and not pre.sent
    post_token = CancelToken()
    transport = MockTransport({"A1": payload()})
    # Cancel after transport observes the send.
    class CancelAfterSend(MockTransport):
        def send(self, request, body):
            post_token.cancel()
            return super().send(request, body)
    post = _exec(CancelAfterSend({"A1": payload()}), policy()).invoke(request(cancel=post_token))
    assert post.status == Status.CANCELLED and post.failure == Failure.CANCELLED_POST_SEND and post.sent


def test_timeout_unknown_does_not_retry():
    transport = MockTransport({"A1": TransportError("timeout_post_send", sent=True)})
    executor = _exec(transport, policy())
    first = executor.invoke(request())
    assert first.status == Status.UNKNOWN and first.failure == Failure.TIMEOUT_POST_SEND
    assert first.re_send_safe is False
    assert len(transport.calls) == 1


def test_rate_limit_refusal_connect_failure_are_distinct():
    for error, failure, sent in [
        (TransportError("rate_limited", status_code=429, sent=True), Failure.RATE_LIMITED, True),
        (TransportError("refused", sent=True), Failure.REFUSED, True),
        (TransportError("connect", sent=False), Failure.PRE_SEND, False),
    ]:
        result = _exec(MockTransport({"A1": error}), policy()).invoke(request())
        assert result.failure == failure
        assert result.sent is sent


def test_unsupported_capability_blocks_before_send():
    transport = MockTransport({"A1": payload()}, declared=Capabilities(structured_output=False))
    result = _exec(transport, policy()).invoke(request())
    assert result.failure == Failure.UNSUPPORTED
    assert result.sent is False and transport.calls == []


def test_policy_scope_provider_input_and_attempt_are_enforced():
    result = _exec(MockTransport({"A1": payload()}), policy()).invoke(
        request(scope=Scope("firm-b", "company-b", "l", "b", "2026-10"))
    )
    assert result.failure == Failure.PRE_SEND
    local = _exec(MockTransport({"A1": payload()}), policy(local_only=True)).invoke(
        request(provider="cloud", provider_is_local=False)
    )
    assert local.failure == Failure.PRE_SEND


def test_call_budget_and_deadline_stop_before_send():
    executor = _exec(MockTransport({"A1": payload()}), policy())
    assert executor.invoke(request(attempt_id="budget-zero", max_calls=0)).failure == Failure.PRE_SEND
    assert _exec(MockTransport({"A1": payload()}), policy()).invoke(
        request(attempt_id="deadline-zero", deadline_seconds=-1)
    ).failure == Failure.PRE_SEND


def test_attempt_binding_is_visible_in_result():
    result = _exec(MockTransport({"A1": payload()}), policy()).invoke(request(attempt_id="attempt-9"))
    assert result.attempt_id == "attempt-9" and result.request_id == "req-1"


def test_a02_local_only_uses_trusted_endpoint_not_self_report():
    trusted_cfg = trusted(local_only=True, endpoint_id="cloud-endpoint")
    transport = MockTransport({"A1": payload()})
    result = _exec(transport, policy(), trusted=trusted_cfg).invoke(
        request(
            endpoint_id="cloud-endpoint", endpoint="https://cloud.example/v1",
            provider="cloud", provider_is_local=True,
        )
    )
    assert result.failure == Failure.PRE_SEND
    assert transport.calls == []


def test_a03_scope_and_input_digest_are_verified():
    trusted_cfg = trusted()
    transport = MockTransport({"A1": payload()})
    executor = _exec(transport, policy(), trusted=trusted_cfg)
    for i, changed in enumerate((
        {"scope": Scope("firm-a", "company-a", "wrong-ledger", "book-a", "2026-10")},
        {"scope": Scope("firm-a", "company-a", "ledger-a", "book-a", "2026-11")},
        {"input_sha256": "0" * 64},
    )):
        result = executor.invoke(request(attempt_id=f"bad-scope-{i}", **changed))
        assert result.failure == Failure.PRE_SEND
    assert transport.calls == []


def test_a04_same_sample_with_different_input_cannot_reuse_attempt():
    trusted_cfg = trusted()
    executor = _exec(MockTransport({"A1": payload()}), policy(), trusted=trusted_cfg)
    first = executor.invoke(request(input_text="input one", input_sha256=hashlib.sha256(b"input one").hexdigest()))
    assert first.status == Status.SUCCEEDED
    with pytest.raises(AttemptConflict):
        executor.invoke(request(input_text="input two", input_sha256=hashlib.sha256(b"input two").hexdigest()))


def test_a05_nested_schema_required_additional_and_array_items():
    schema = {
        "type": "object",
        "required": ["invoice"],
        "additionalProperties": False,
        "properties": {
            "invoice": {
                "type": "object",
                "required": ["lines"],
                "additionalProperties": False,
                "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
            }
        },
    }
    good = '{"invoice":{"lines":["a"]}}'
    bad_values = [
        '{"invoice":{}}',
        '{"invoice":{"lines":["a"],"extra":1}}',
        '{"invoice":{"lines":[1]}}',
    ]
    executor = _exec(MockTransport({"A1": payload(content=good)}), policy())
    assert executor.invoke(request(output_schema=schema)).status == Status.SUCCEEDED
    for bad in bad_values:
        result = _exec(MockTransport({"A1": payload(content=bad)}), policy()).invoke(request(output_schema=schema))
        assert result.failure == Failure.INVALID_RESPONSE


def test_a06_overflow_numbers_are_never_success():
    result = _exec(MockTransport({"A1": payload(content='{"value":1e9999}')}), policy()).invoke(request())
    assert result.status == Status.FAILED
    assert result.structured is None


def test_a07_failure_preserves_known_usage_and_cost():
    response = payload(content="not-json", usage={"prompt_tokens": 3, "completion_tokens": 0, "total_tokens": 3}, cost="0.002")
    result = _exec(MockTransport({"A1": response}), policy()).invoke(request())
    assert result.status == Status.FAILED
    assert result.usage.known is True and result.usage.total_tokens == 3
    assert result.cost.known is True and result.cost.amount == Decimal("0.002")


def test_a08_unknown_attempt_is_not_resent_and_parameter_change_is_rejected():
    transport = MockTransport({"A1": TransportError("timeout_post_send", sent=True)})
    executor = _exec(transport, policy())
    first = executor.invoke(request())
    second = executor.invoke(request())
    assert first.status == Status.UNKNOWN and second.status == Status.UNKNOWN
    assert len(transport.calls) == 1
    with pytest.raises(AttemptConflict):
        executor.invoke(request(attempt_id="attempt-1", sample_id="A2"))


def test_a09_concurrent_same_attempt_sends_at_most_once():
    import threading

    transport = MockTransport({"A1": payload()})
    executor = _exec(transport, policy())
    results = []
    def call():
        results.append(executor.invoke(request()))
    threads = [threading.Thread(target=call) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(transport.calls) == 1
    assert len(results) == 5


def test_a10_frozen_limits_cannot_be_raised():
    executor = _exec(MockTransport({"A1": payload()}), policy(), limits=RunLimits(1, 1, 10))
    assert executor.invoke(request(attempt_id="over-calls", max_calls=2)).failure == Failure.PRE_SEND
    assert executor.invoke(request(attempt_id="over-tokens", max_output_tokens=11)).failure == Failure.PRE_SEND
    assert executor.invoke(request(attempt_id="over-deadline", deadline_seconds=2)).failure == Failure.PRE_SEND
    assert executor.invoke(request(attempt_id="within-limits", max_calls=1, max_output_tokens=10, deadline_seconds=1)).status == Status.SUCCEEDED


def test_a11_cancel_after_response_preserves_evidence():
    token = CancelToken()
    class CancelOnReturn(MockTransport):
        def send(self, request, body):
            token.cancel()
            return payload()
    result = _exec(CancelOnReturn({"A1": payload()}), policy()).invoke(request(cancel=token))
    assert result.status == Status.CANCELLED
    assert result.failure == Failure.CANCELLED_POST_SEND
    assert result.sent is True and result.received is True


def test_a01_a02_missing_trusted_or_limits_fails_before_send():
    transport = MockTransport({"A1": payload()})
    no_trusted = Executor(transport, policy(), trusted=None, limits=RunLimits(1, 5, 10)).invoke(request())
    no_limits = Executor(transport, policy(), trusted=trusted(), limits=None).invoke(request(attempt_id="a02"))
    assert no_trusted.failure == Failure.PRE_SEND and no_trusted.sent is False
    assert no_limits.failure == Failure.PRE_SEND and no_limits.sent is False
    assert transport.calls == []


def test_b04_b05_response_is_bound_to_sample_and_input():
    transport = MockTransport({"A1": payload()})
    executor = _exec(transport)
    first = executor.invoke(request(attempt_id="b04-1"))
    second = executor.invoke(request(attempt_id="b04-2", input_text="different", input_sha256=hashlib.sha256(b"different").hexdigest()))
    third = executor.invoke(request(attempt_id="b05-1", scope=scope("company-b"), input_text="other", input_sha256=hashlib.sha256(b"other").hexdigest()))
    assert first.status == Status.SUCCEEDED
    assert second.failure == Failure.PRE_SEND
    assert third.failure == Failure.PRE_SEND
    assert len(transport.calls) == 1


def test_c03_semantic_field_change_is_attempt_conflict():
    executor = _exec(MockTransport({"A1": payload()}))
    executor.invoke(request(attempt_id="c03"))
    for changed in (
        {"schema_version": "v2"}, {"allowed_data": "image"}, {"endpoint_id": "other"},
        {"scenario": "other"}, {"max_output_tokens": 99}, {"policy_config": {"prompt": "x"}},
    ):
        with pytest.raises(AttemptConflict):
            executor.invoke(request(attempt_id="c03", **changed))


def test_c04_cached_result_requires_current_authorization():
    class Revocable:
        allowed = True
        def endpoint_for(self, req):
            if not self.allowed:
                raise PermissionError("revoked")
            return trusted().endpoint_for(req)
    revocable = Revocable()
    executor = _exec(MockTransport({"A1": payload()}), trusted=revocable)
    assert executor.invoke(request(attempt_id="c04")).status == Status.SUCCEEDED
    revocable.allowed = False
    denied = executor.invoke(request(attempt_id="c04"))
    assert denied.status == Status.FAILED and denied.failure == Failure.PRE_SEND


def test_c05_schema_preflight_has_zero_sends():
    transport = MockTransport({"A1": payload()})
    result = _exec(transport).invoke(request(attempt_id="c05", output_schema={"type": "object", "properties": {"x": {"type": "wat"}}}))
    assert result.failure == Failure.PRE_SEND and transport.calls == []


def test_c06_post_send_internal_exception_is_recorded_once(monkeypatch):
    transport = MockTransport({"A1": payload()})
    executor = _exec(transport)
    monkeypatch.setattr("tools.ai_inference_lab.contracts.parse_structured", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    first = executor.invoke(request(attempt_id="c06"))
    second = executor.invoke(request(attempt_id="c06"))
    assert first.status == Status.UNKNOWN
    assert second.status == Status.UNKNOWN
    assert len(transport.calls) == 1


def test_c07_different_attempts_share_frozen_budget():
    executor = _exec(MockTransport({"A1": payload()}), limits=RunLimits(2, 30, 100))
    assert executor.invoke(request(attempt_id="c07-a", max_calls=2)).status == Status.SUCCEEDED
    assert executor.invoke(request(attempt_id="c07-b", max_calls=2)).status == Status.SUCCEEDED
    assert executor.invoke(request(attempt_id="c07-c", max_calls=2)).failure == Failure.PRE_SEND


def test_d01_d02_known_receipts_on_bad_envelopes():
    for response in (
        {"choices": [], "usage": {"total_tokens": 4}, "cost": "0.25"},
        payload(content="not-json", usage={"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}, cost="0.25"),
        payload(finish_reason="length", usage={"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}, cost="0.25"),
        payload(refused=True, usage={"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}, cost="0.25"),
    ):
        result = _exec(MockTransport({"A1": response})).invoke(request(attempt_id="d01-" + str(id(response))))
        assert result.failure is not None
        assert result.usage.known is True and result.cost.known is True


def test_d03_d04_unknown_vs_zero_and_no_double_charge():
    zero = _exec(MockTransport({"A1": payload(usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, cost="0")}))
    result = zero.invoke(request(attempt_id="d03-zero"))
    assert result.usage.known is True and result.cost.amount == Decimal("0")
    assert zero.invoke(request(attempt_id="d03-zero")).cost.amount == Decimal("0")
    assert zero.spent == Decimal("0")
    missing = _exec(MockTransport({"A1": payload(usage=None, cost=None)}))
    result = missing.invoke(request(attempt_id="d03-missing"))
    assert result.usage.known is False and result.cost.known is False
    assert missing.cost_summary()["unknown_cost_calls"] == 1


def test_e05_post_send_deadline_uses_injected_clock():
    now = [0.0]
    class Slow(MockTransport):
        def send(self, request, body):
            now[0] = 5.0
            return payload()
    result = _exec(Slow({"A1": payload()}), limits=RunLimits(2, 1, 100), clock=lambda: now[0]).invoke(request(attempt_id="e05", deadline_seconds=1, max_calls=2))
    assert result.failure == Failure.TIMEOUT_POST_SEND
    assert result.structured == {"gross_amount": "106.00"}
    assert result.usage.known is True


def test_f01_f02_f03_f05_schema_preflight_rejects_before_send():
    bad_schemas = [
        {"type": "object", "properties": {"optional": {"type": "string", "unknown": 1}}},
        {"type": "array", "items": {"type": "string", "unknown": 1}},
        {"type": "string", "enum": "abc"},
        {"type": "string", "pattern": "["},
    ]
    for index, schema in enumerate(bad_schemas):
        transport = MockTransport({"A1": payload()})
        result = _exec(transport).invoke(request(attempt_id=f"f-{index}", output_schema=schema))
        assert result.failure == Failure.PRE_SEND
        assert transport.calls == []


def test_f04_f06_enum_type_and_nested_schema_success():
    schema = {
        "type": "object", "required": ["value"],
        "properties": {"value": {"type": "integer", "enum": [1]}},
    }
    bad = _exec(MockTransport({"A1": payload(content='{"value":true}')})).invoke(request(attempt_id="f04", output_schema=schema))
    assert bad.failure == Failure.INVALID_RESPONSE
    good = _exec(MockTransport({"A1": payload(content='{"value":1}')})).invoke(request(attempt_id="f06", output_schema=schema))
    assert good.status == Status.SUCCEEDED


def test_a04_wrong_endpoint_model_or_scope_is_rejected():
    transport = MockTransport({"A1": payload()})
    executor = _exec(transport, trusted=trusted())
    for changed in (
        {"endpoint": "https://evil.example/v1"},
        {"model": "other-model"},
        {"scope": Scope("firm-a", "company-a", "ledger-a", "book-a", "2026-11")},
    ):
        result = executor.invoke(request(attempt_id="a04-" + str(id(changed)), **changed))
        assert result.failure == Failure.PRE_SEND
    assert transport.calls == []


def test_d06_post_send_cancel_preserves_usage_and_cost():
    token = CancelToken()
    class CancelAfterResponse(MockTransport):
        def send(self, request, body):
            token.cancel()
            return payload()
    result = _exec(CancelAfterResponse({"A1": payload()})).invoke(request(attempt_id="d06", cancel=token))
    assert result.status == Status.CANCELLED
    assert result.usage.known is True and result.cost.known is True


def test_e06_concurrent_attempts_cannot_exceed_shared_budget():
    import threading

    transport = MockTransport({"A1": payload()})
    executor = _exec(transport, limits=RunLimits(2, 30, 100))
    results = []
    def call(i):
        results.append(executor.invoke(request(attempt_id=f"e06-{i}", max_calls=2)))
    threads = [threading.Thread(target=call, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(transport.calls) == 2
    assert sum(1 for r in results if r.status == Status.SUCCEEDED) == 2


def test_a12_openai_compatible_frozen_response_is_normalized():
    response = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"gross_amount":"106.00"}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }
    result = _exec(MockTransport({"A1": response}), policy()).invoke(request())
    assert result.status == Status.SUCCEEDED
    assert result.structured == {"gross_amount": "106.00"}
    assert result.usage.total_tokens == 5
    error = {"error": {"message": "rate limited", "type": "rate_limit"}}
    failed = _exec(MockTransport({"A1": error}), policy()).invoke(request(attempt_id="attempt-error"))
    assert failed.failure in {Failure.REFUSED, Failure.RATE_LIMITED}
