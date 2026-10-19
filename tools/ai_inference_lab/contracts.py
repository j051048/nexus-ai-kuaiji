"""DL01 offline inference contracts; no network, database, app or production provider."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import math
import re
import threading
import time
from typing import Callable, Mapping


class Failure(str, Enum):
    PRE_SEND = "pre_send_failure"
    REFUSED = "refused"
    RATE_LIMITED = "rate_limited"
    UNSUPPORTED = "capability_unsupported"
    INVALID_RESPONSE = "invalid_response"
    TRUNCATED = "truncated"
    CANCELLED_PRE_SEND = "cancelled_pre_send"
    CANCELLED_POST_SEND = "cancelled_post_send"
    TIMEOUT_POST_SEND = "timeout_post_send"
    UNKNOWN = "unknown"


class Status(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Scope:
    firm_id: str
    company_id: str
    ledger_id: str
    book_id: str
    period: str


@dataclass(frozen=True)
class Capabilities:
    structured_output: bool = False
    image_input: bool = False
    streaming: bool = False
    usage_receipt: bool = False
    cancellation: bool = False


@dataclass(frozen=True)
class EnterprisePolicy:
    allowed_firm_id: str
    allowed_company_ids: frozenset[str]
    allowed_providers: frozenset[str]
    local_only: bool = False
    allowed_data: frozenset[str] = frozenset({"ocr_text"})

    def allows(self, scope: Scope, provider: str, provider_is_local: bool, data: str) -> bool:
        if scope.firm_id != self.allowed_firm_id or scope.company_id not in self.allowed_company_ids:
            return False
        if data not in self.allowed_data or provider not in self.allowed_providers:
            return False
        return not self.local_only or provider_is_local


@dataclass(frozen=True)
class Endpoint:
    endpoint_id: str
    url: str
    provider: str
    model: str
    is_local: bool
    capabilities: Capabilities
    allowed_data: frozenset[str] = frozenset({"ocr_text"})


@dataclass(frozen=True)
class TrustedConfig:
    """Local experiment authority; never supplied by the model request."""

    scopes: Mapping[str, Scope]  # key: company_id
    endpoints: Mapping[str, Endpoint]
    local_only: bool = False

    def endpoint_for(self, request):
        endpoint = self.endpoints.get(request.endpoint_id or "")
        if endpoint is None:
            raise PermissionError("endpoint is not trusted")
        if endpoint.url != request.endpoint or endpoint.provider != request.provider or endpoint.model != request.model:
            raise PermissionError("provider/model/endpoint identity mismatch")
        scope = self.scopes.get(request.scope.company_id)
        if scope is None or scope != request.scope:
            raise PermissionError("scope is not trusted")
        if self.local_only and not endpoint.is_local:
            raise PermissionError("local-only experiment")
        if request.allowed_data not in endpoint.allowed_data:
            raise PermissionError("data class is not allowed")
        return endpoint


@dataclass(frozen=True)
class RunLimits:
    max_calls: int
    deadline_seconds: float
    max_output_tokens: int


class AttemptConflict(Exception):
    pass


@dataclass(frozen=True)
class CancelToken:
    cancelled: bool = False

    def cancel(self):
        object.__setattr__(self, "cancelled", True)


@dataclass(frozen=True)
class CallRequest:
    request_id: str
    run_id: str
    attempt_id: str
    sample_id: str
    scope: Scope
    input_sha256: str
    scenario: str
    schema_version: str
    policy_version: str
    provider: str
    model: str
    endpoint: str
    provider_is_local: bool
    allowed_data: str
    max_output_tokens: int
    max_calls: int
    deadline_seconds: float
    cancel: CancelToken = field(default_factory=CancelToken)
    capabilities: Capabilities = field(default_factory=Capabilities)
    required_capabilities: frozenset[str] = frozenset()
    output_schema: Mapping = field(default_factory=dict)
    input_text: str | None = None
    endpoint_id: str | None = None
    policy_config: Mapping = field(default_factory=dict)

    def semantic_fingerprint(self):
        payload = {
            "request_id": self.request_id, "run_id": self.run_id, "attempt_id": self.attempt_id,
            "sample_id": self.sample_id, "scope": self.scope.__dict__, "input_sha256": self.input_sha256,
            "input_text": self.input_text, "scenario": self.scenario,
            "schema_version": self.schema_version, "schema": self.output_schema,
            "policy_version": self.policy_version, "policy_config": self.policy_config,
            "provider": self.provider, "model": self.model, "endpoint": self.endpoint,
            "endpoint_id": self.endpoint_id, "provider_is_local": self.provider_is_local,
            "allowed_data": self.allowed_data, "max_output_tokens": self.max_output_tokens,
            "max_calls": self.max_calls, "deadline_seconds": self.deadline_seconds,
            "required_capabilities": sorted(self.required_capabilities),
            "capabilities": self.capabilities.__dict__,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Usage:
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    known: bool
    partial: bool = False


@dataclass
class Cost:
    amount: Decimal | None
    currency: str
    known: bool


@dataclass
class CallResult:
    request_id: str
    attempt_id: str
    status: Status
    finish_reason: str | None
    structured: dict | None
    validation: str
    provider: str
    model: str
    sent: bool
    received: bool
    complete: bool
    failure: Failure | None
    error: str
    usage: Usage
    cost: Cost
    latency_ms: float | None
    simulated: bool = True
    source: dict = field(default_factory=dict)

    @property
    def re_send_safe(self):
        return not self.sent or (self.received and self.complete and self.status == Status.FAILED)


class TransportError(Exception):
    def __init__(self, kind: str, message="", status_code=None, sent=False):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.sent = sent


class MockTransport:
    """Deterministic frozen-response transport. Never opens a socket."""

    def __init__(self, responses: Mapping[str, object], *, declared: Capabilities | None = None):
        self.responses = dict(responses)
        self.declared = declared or Capabilities(
            structured_output=True, usage_receipt=True, cancellation=True
        )
        self.calls = []
        self.bound = {}

    def capabilities(self):
        return self.declared

    def send(self, request: CallRequest, body: Mapping):
        if request.cancel.cancelled:
            raise TransportError("cancelled_post_send", "cancelled after send", sent=True)
        prior_input = self.bound.get(request.sample_id)
        if prior_input is not None and prior_input != request.input_sha256:
            raise TransportError("response_not_bound", "frozen response is bound to another input", sent=False)
        self.calls.append({"request_id": request.request_id, "attempt_id": request.attempt_id})
        value = self.responses.get((request.sample_id, request.input_sha256))
        if value is None:
            value = self.responses.get(request.sample_id)
        if value is None:
            value = self.responses.get("*")
        if value is None:
            raise TransportError("response_missing", "no frozen response for this sample/input", sent=False)
        self.bound[request.sample_id] = request.input_sha256
        if isinstance(value, TransportError):
            raise value
        if isinstance(value, Exception):
            raise value
        return value


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def parse_structured(text: str):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty response")
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda v: (_ for _ in ()).throw(
        ValueError(f"non-finite JSON value: {v}")
    ))


_SCHEMA_KEYS = {
    "type", "required", "properties", "additionalProperties", "items", "enum",
    "minimum", "maximum", "minLength", "maxLength", "pattern",
}


def _finite(value, path="$"):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number at {path}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _finite(item, f"{path}[{i}]")


def validate_schema_declaration(schema, path="$"):
    """Validate the complete schema before send, including absent properties."""
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise ValueError(f"schema at {path} must be an object")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise ValueError(f"unsupported schema keyword at {path}: {sorted(unknown)[0]}")
    expected = schema.get("type")
    if expected is not None and expected not in {"string", "integer", "number", "boolean", "object", "array", "null"}:
        raise ValueError(f"unsupported type at {path}: {expected}")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        raise ValueError(f"invalid required at {path}")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ValueError(f"invalid enum at {path}")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise ValueError(f"invalid pattern at {path}")
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise ValueError(f"invalid pattern at {path}") from exc
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            raise ValueError(f"invalid {key} at {path}")
    for key in ("minLength", "maxLength"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError(f"invalid {key} at {path}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"invalid properties at {path}")
    for key, subschema in properties.items():
        validate_schema_declaration(subschema, f"{path}.{key}")
    additional = schema.get("additionalProperties", True)
    if additional not in (True, False) and not isinstance(additional, dict):
        raise ValueError(f"invalid additionalProperties at {path}")
    if isinstance(additional, dict):
        validate_schema_declaration(additional, f"{path}.*")
    if "items" in schema:
        validate_schema_declaration(schema["items"], f"{path}[]")


def _schema(value, schema, path="$"):
    if not isinstance(schema, dict):
        raise ValueError(f"schema at {path} must be an object")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise ValueError(f"unsupported schema keyword at {path}: {sorted(unknown)[0]}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number at {path}")
    if isinstance(value, dict):
        for item in value.values():
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError(f"non-finite number at {path}")
    expected = schema.get("type")
    if expected is not None:
        checks = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: type(v) is int,
            "number": lambda v: not isinstance(v, bool) and isinstance(v, (int, float)),
            "boolean": lambda v: type(v) is bool,
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "null": lambda v: v is None,
        }
        if expected not in checks:
            raise ValueError(f"unsupported type at {path}: {expected}")
        if not checks[expected](value):
            raise ValueError(f"type mismatch at {path}")
    if "enum" in schema and not any(
        type(value) is type(item) and value == item for item in schema["enum"]
    ):
        raise ValueError(f"enum mismatch at {path}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
            raise ValueError(f"invalid required at {path}")
        for key in required:
            if key not in value:
                raise ValueError(f"missing required {key} at {path}")
        props = schema.get("properties", {})
        if not isinstance(props, dict):
            raise ValueError(f"invalid properties at {path}")
        for key, spec in props.items():
            if key in value:
                _schema(value[key], spec, f"{path}.{key}")
        additional = schema.get("additionalProperties", True)
        if additional is False:
            extra = set(value) - set(props)
            if extra:
                raise ValueError(f"additional property at {path}: {sorted(extra)[0]}")
        elif isinstance(additional, dict):
            for key in set(value) - set(props):
                _schema(value[key], additional, f"{path}.{key}")
        elif additional is not True:
            raise ValueError(f"invalid additionalProperties at {path}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _schema(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"minLength at {path}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"maxLength at {path}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValueError(f"pattern at {path}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"minimum at {path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"maximum at {path}")


def _usage(payload):
    raw = payload.get("usage") if isinstance(payload, dict) else None
    if raw is None:
        return Usage(None, None, None, False)
    if not isinstance(raw, dict):
        return Usage(None, None, None, False)
    vals = [raw.get("prompt_tokens"), raw.get("completion_tokens"), raw.get("total_tokens")]
    valid = [type(v) is int and v >= 0 for v in vals]
    if not any(valid):
        return Usage(None, None, None, False)
    if not all(valid):
        return Usage(
            vals[0] if valid[0] else None,
            vals[1] if valid[1] else None,
            vals[2] if valid[2] else None,
            True,
            True,
        )
    return Usage(vals[0], vals[1], vals[2], True, False)


def _cost(payload):
    raw = payload.get("cost") if isinstance(payload, dict) else None
    if raw is None:
        return Cost(None, "CNY", False)
    if isinstance(raw, dict):
        amount, currency, known = raw.get("amount"), raw.get("currency", "CNY"), raw.get("known", True)
    else:
        amount, currency, known = raw, "CNY", True
    if not known:
        return Cost(None, currency, False)
    if isinstance(amount, bool) or not isinstance(amount, (str, int)):
        return Cost(None, currency, False)
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        return Cost(None, currency, False)
    if not value.is_finite() or value < 0:
        return Cost(None, currency, False)
    return Cost(value, currency, True)


def normalize_protocol(payload):
    """Normalize a frozen OpenAI-compatible response; preserve raw custom mock shape."""
    if not isinstance(payload, dict):
        return payload
    if "choices" in payload:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return {
                "error": {"kind": "invalid_choices"},
                "usage": payload.get("usage"),
                "cost": payload.get("cost"),
            }
        choice = choices[0]
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        return {
            "content": message.get("content"),
            "refused": bool(message.get("refusal")) or choice.get("finish_reason") == "refusal",
            "tool_calls": message.get("tool_calls"),
            "finish_reason": choice.get("finish_reason"),
            "usage": payload.get("usage"),
            "cost": payload.get("cost"),
            "latency_ms": payload.get("latency_ms"),
        }
    if "error" in payload:
        error = payload["error"] if isinstance(payload["error"], dict) else {}
        return {
            "refused": True,
            "error": error.get("message") or "provider error",
            "finish_reason": "error",
            "usage": payload.get("usage"),
            "cost": payload.get("cost"),
        }
    return payload


class Executor:
    """One invocation = one attempt. No hidden retries or model switching."""

    def __init__(self, transport: MockTransport, policy: EnterprisePolicy, *, trusted=None, limits=None, clock=time.monotonic):
        self.transport = transport
        self.policy = policy
        self.trusted = trusted
        self.limits = limits
        self.clock = clock
        self.calls = 0
        self.spent = Decimal("0")
        self.unknown_cost_calls = 0
        self.started = clock()
        self._lock = threading.Lock()
        self._attempts = {}

    def cost_summary(self):
        return {
            "spent_known": str(self.spent),
            "unknown_cost_calls": self.unknown_cost_calls,
            "currency": "CNY",
        }

    def _cached_source_allowed(self, request):
        if self.trusted is None or self.limits is None:
            return False
        try:
            endpoint = self.trusted.endpoint_for(request)
        except PermissionError:
            return False
        return self.policy.allows(request.scope, endpoint.provider, endpoint.is_local, request.allowed_data)

    def invoke(self, request: CallRequest) -> CallResult:
        with self._lock:
            fingerprint = request.semantic_fingerprint()
            prior = self._attempts.get(request.attempt_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise AttemptConflict("same attempt id with different semantic request")
                if not self._cached_source_allowed(request):
                    return self._preflight_failure(request, "current authorization revoked")
                if prior.get("result") is None:
                    return self._preflight_failure(request, "attempt has no completed result")
                return prior["result"]
            self._attempts[request.attempt_id] = {"fingerprint": fingerprint, "result": None}
            try:
                result = self._invoke(request)
            except Exception:
                result = self._preflight_failure(request, "internal post-send failure", status=Status.UNKNOWN, sent=True)
            self._attempts[request.attempt_id]["result"] = result
            return result

    def _source(self, request):
        return {
            "request_fingerprint": request.semantic_fingerprint(),
            "input_sha256": request.input_sha256,
            "schema_version": request.schema_version,
            "schema_sha256": hashlib.sha256(json.dumps(request.output_schema, sort_keys=True, default=str).encode()).hexdigest(),
            "policy_version": request.policy_version,
            "policy_sha256": hashlib.sha256(json.dumps(request.policy_config, sort_keys=True, default=str).encode()).hexdigest(),
            "provider": request.provider, "model": request.model,
            "endpoint": request.endpoint, "endpoint_id": request.endpoint_id,
            "attempt_id": request.attempt_id,
        }

    def _preflight_failure(self, request, message, *, status=Status.FAILED, sent=False, usage=None, cost=None):
        return CallResult(
            request_id=request.request_id, attempt_id=request.attempt_id, status=status,
            finish_reason=None, structured=None, validation="not_run", provider=request.provider,
            model=request.model, sent=sent, received=False, complete=False,
            failure=Failure.PRE_SEND, error=message,
            usage=usage or Usage(None, None, None, False),
            cost=cost or Cost(None, "CNY", False), latency_ms=None, source=self._source(request),
        )

    def _invoke(self, request: CallRequest) -> CallResult:
        caps = self.transport.capabilities()
        base = dict(
            request_id=request.request_id,
            attempt_id=request.attempt_id,
            provider=request.provider,
            model=request.model,
            sent=False,
            received=False,
            complete=False,
            simulated=True,
            source=self._source(request),
        )
        usage, cost = Usage(None, None, None, False), Cost(None, "CNY", False)

        def fail(kind, message, status=Status.FAILED, sent=False, received=False, structured=None):
            values = {k: v for k, v in base.items() if k not in {"sent", "received"}}
            return CallResult(
                **values, status=status, finish_reason=None, structured=structured,
                validation="not_run", failure=kind, error=message, sent=sent,
                received=received, usage=usage, cost=cost, latency_ms=None,
            )

        if self.trusted is None:
            return fail(Failure.PRE_SEND, "trusted execution context is required")
        if self.limits is None:
            return fail(Failure.PRE_SEND, "frozen run limits are required")
        if request.cancel.cancelled:
            return fail(Failure.CANCELLED_PRE_SEND, "cancelled before send", Status.CANCELLED)
        try:
            validate_schema_declaration(request.output_schema)
        except ValueError as exc:
            return fail(Failure.PRE_SEND, str(exc))
        if self.limits is not None:
            if request.max_calls > self.limits.max_calls:
                return fail(Failure.PRE_SEND, "request exceeds frozen call budget")
            if request.deadline_seconds > self.limits.deadline_seconds:
                return fail(Failure.PRE_SEND, "request exceeds frozen deadline")
            if request.max_output_tokens > self.limits.max_output_tokens:
                return fail(Failure.PRE_SEND, "request exceeds frozen output budget")
            if self.calls >= min(request.max_calls, self.limits.max_calls):
                return fail(Failure.PRE_SEND, "call budget exhausted")
            if self.clock() - self.started > min(request.deadline_seconds, self.limits.deadline_seconds):
                return fail(Failure.PRE_SEND, "task deadline exhausted")
        else:
            if self.calls >= request.max_calls:
                return fail(Failure.PRE_SEND, "call budget exhausted")
            if self.clock() - self.started > request.deadline_seconds:
                return fail(Failure.PRE_SEND, "task deadline exhausted")
        if request.max_output_tokens <= 0:
            return fail(Failure.PRE_SEND, "invalid output token limit")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", request.input_sha256 or ""):
            return fail(Failure.PRE_SEND, "invalid input digest")
        if request.input_text is None:
            return fail(Failure.PRE_SEND, "actual input is required")
        actual_input = hashlib.sha256(request.input_text.encode("utf-8")).hexdigest()
        if actual_input != request.input_sha256.lower():
            return fail(Failure.PRE_SEND, "input digest does not match input")
        if self.trusted is not None:
            try:
                endpoint = self.trusted.endpoint_for(request)
            except PermissionError as exc:
                return fail(Failure.PRE_SEND, str(exc))
            transport_caps = self.transport.capabilities()
            caps = Capabilities(
                structured_output=endpoint.capabilities.structured_output and transport_caps.structured_output,
                image_input=endpoint.capabilities.image_input and transport_caps.image_input,
                streaming=endpoint.capabilities.streaming and transport_caps.streaming,
                usage_receipt=endpoint.capabilities.usage_receipt and transport_caps.usage_receipt,
                cancellation=endpoint.capabilities.cancellation and transport_caps.cancellation,
            )
            if not self.policy.allows(
                request.scope, endpoint.provider, endpoint.is_local, request.allowed_data
            ):
                return fail(Failure.PRE_SEND, "enterprise policy or provider not allowed")
        elif not self.policy.allows(
            request.scope, request.provider, request.provider_is_local, request.allowed_data
        ):
            return fail(Failure.PRE_SEND, "enterprise policy or provider not allowed")
        for name in request.required_capabilities:
            if not getattr(caps, name, False):
                return fail(Failure.UNSUPPORTED, f"provider lacks {name}")
        if request.cancel.cancelled:
            return fail(Failure.CANCELLED_PRE_SEND, "cancelled before send", Status.CANCELLED)
        self.calls += 1
        try:
            raw_payload = self.transport.send(request, {})
        except TransportError as exc:
            if exc.kind == "rate_limited" or exc.status_code == 429:
                return fail(Failure.RATE_LIMITED, "rate limited", sent=True)
            if exc.kind == "refused":
                return fail(Failure.REFUSED, "provider refused", sent=True)
            if exc.kind == "cancelled_post_send":
                return fail(Failure.CANCELLED_POST_SEND, "cancelled after send", Status.CANCELLED, True)
            if not exc.sent and exc.kind != "timeout_post_send":
                return fail(Failure.PRE_SEND, exc.kind or "connect failed")
            return fail(Failure.TIMEOUT_POST_SEND, "post-send timeout or disconnect", Status.UNKNOWN, True)
        except Exception:
            return fail(Failure.UNKNOWN, "unexpected transport result", Status.UNKNOWN, True)
        base["sent"] = True
        payload = normalize_protocol(raw_payload)
        if not isinstance(payload, dict):
            return fail(Failure.INVALID_RESPONSE, "response is not an object", sent=True, received=True)
        base["received"] = True
        usage, cost = _usage(payload), _cost(payload)
        if cost.known:
            self.spent += cost.amount
        else:
            self.unknown_cost_calls += 1
        if request.cancel.cancelled:
            return fail(Failure.CANCELLED_POST_SEND, "cancelled while response arrived", Status.CANCELLED, True, True)
        if payload.get("refused") is True:
            return fail(Failure.REFUSED, "model refused", sent=True, received=True)
        if payload.get("tool_calls"):
            return fail(Failure.UNSUPPORTED, "unexpected tool call", sent=True, received=True)
        if payload.get("finish_reason") == "length":
            return fail(Failure.TRUNCATED, "response truncated", sent=True, received=True)
        if payload.get("finish_reason") == "refusal":
            return fail(Failure.REFUSED, "finish reason refusal", sent=True, received=True)
        raw = payload.get("content")
        if not isinstance(raw, str):
            return fail(Failure.INVALID_RESPONSE, "missing content", sent=True, received=True)
        try:
            structured = parse_structured(raw)
            _finite(structured)
            if request.output_schema:
                _schema(structured, request.output_schema)
        except (ValueError, TypeError) as exc:
            return fail(Failure.INVALID_RESPONSE, str(exc), sent=True, received=True)
        if self.clock() - self.started > self.limits.deadline_seconds:
            return fail(
                Failure.TIMEOUT_POST_SEND,
                "run deadline exceeded after response",
                sent=True,
                received=True,
                structured=structured,
            )
        base["complete"] = True
        return CallResult(
            **base, status=Status.SUCCEEDED, finish_reason=payload.get("finish_reason"),
            structured=structured, validation="schema_ok", failure=None, error="",
            usage=usage, cost=cost, latency_ms=payload.get("latency_ms"),
        )
