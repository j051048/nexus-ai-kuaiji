"""AK01: real domain entries and transport, synthetic responses, no network."""

import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.assistant.meter import AssistantMeter
from app.assistant.models import ConversationCreate, TurnCreate
from app.assistant.provider import AssistantProvider
from app.assistant.service import AssistantService
from app.assistant.worker import AssistantWorker
from app.accounting.domain import AccountingError
from app.intelligence.endpoints import (
    AIEndpointPolicyService,
    CapabilityCreate,
    CompanyPolicyUpdate,
    EndpointCreate,
)
from app.intelligence.provider import ExecutionLimits, OpenAICompatibleProvider, ProviderError
from app.intelligence.models import PreparedPage
from app.operations.pricing import Pricing, Rate
from test_intelligence_workflow import ai_case, create_event, extract, handoff, invoice_document, propose
from test_model_cost_workflow import cost_case, policy


def _reply(payload):
    if payload["model"] == "synthetic-chat":
        return {"answer": "请核对当前授权资料。", "citation_ids": [], "proposals": []}
    if payload["model"] == "synthetic-vision":
        return {"documents": [invoice_document()]}
    context = json.loads(payload["messages"][1]["content"].split("\n", 1)[1])
    if "context_pack" in context:
        context = {row["key"]: row["value"] for row in context["context_pack"]["facts"]}
    document = context["documents"][0]
    amount = context["business"]["amount"]
    return {
        "description": "合成办公耗材",
        "lines": [
            {"account_code": "6602.02", "debit": amount, "credit": "0.00"},
            {"account_code": "2241", "debit": "0.00", "credit": amount},
        ],
        "reasoning": "只使用当前业务与原件的金额。",
        "evidence_refs": [{
            "attachment_id": document["attachment_id"],
            "extraction_id": document["extraction_id"],
            "field": "gross_amount",
            "page": 1,
        }],
        "warnings": [],
    }


@pytest.fixture
def service_case(cost_case):
    case = cost_case
    case.endpoint_service = AIEndpointPolicyService(case.db)
    case.calls = []
    case.response_hook = None
    case.response_body = None
    case.response_status = 200
    case.provider.settings = replace(case.provider.settings, chat_model="synthetic-chat")

    def setup(
        scenario, *, context_limit=128000, output_limit=173, timeout_seconds=2,
        published_template=False,
    ):
        endpoint = case.endpoint_service.create_endpoint(case.manager, EndpointCreate(
            host="supplier.example.invalid",
            path_prefix="/v1",
            service_identity="AK01 synthetic transport only",
            capabilities=[CapabilityCreate(
                scenario=name,
                model={"ocr": "synthetic-vision", "journal": "synthetic-journal", "chat": "synthetic-chat"}[name],
                supports_image=name == "ocr",
                context_limit=context_limit if name == scenario else 128000,
                output_limit=output_limit if name == scenario else 4096,
                timeout_seconds=timeout_seconds if name == scenario else 45,
                data_location="offline MockTransport",
                reason="AK01 本地合成限额",
            ) for name in ("ocr", "journal", "chat")],
            reason="AK01 本地合成端点",
        ))
        case.endpoint = endpoint
        case.endpoint_service.update_policy(case.manager, case.company["id"], CompanyPolicyUpdate(
            allowed_scenarios=["ocr", "journal", "chat"],
            allowed_data_categories=["document", "counterparty", "amount", "accounting", "chat"],
            allowed_endpoint_ids=[endpoint["id"]],
            reason="AK01 本地合成外发政策",
        ))
        case.target = scenario
        case.event = create_event(case)
        if scenario == "journal":
            assert extract(case, case.event)["status"] == "succeeded"
            if published_template:
                from app.accounting.catalog import CatalogService
                from app.jobs.service import QueueService
                from test_stage7_proposal_queue import template

                case.catalog = CatalogService(case.db)
                case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
                case.business.proposal_queue = case.queue.proposals
                template(case)
            case.mapping = handoff(case, case.event)

        def response(request):
            payload = json.loads(request.content)
            case.calls.append(request)
            if case.response_hook:
                case.response_hook(request)
            body = case.response_body
            if body is None:
                body = {
                    "choices": [{"finish_reason": "stop", "message": {
                        "content": json.dumps(_reply(payload), ensure_ascii=False),
                    }}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                }
            return httpx.Response(case.response_status, json=body)

        client = httpx.Client(transport=httpx.MockTransport(response), trust_env=False)
        case.real_provider = OpenAICompatibleProvider(case.provider.settings, client=client)
        case.intelligence.provider = case.real_provider
        case.client = client
        if scenario == "chat":
            case.meter.pricing = Pricing(
                ocr=case.pricing.ocr, proposal=case.pricing.proposal,
                chat=Rate("1.000000", "2.000000"),
            )
            case.chat_provider = AssistantProvider(case.real_provider)
            case.chat_service = AssistantService(
                case.db, case.ledger, case.business, provider=case.chat_provider,
            )
            case.chat_worker = AssistantWorker(
                case.chat_service, case.chat_provider, AssistantMeter(case.db, case.meter),
            )
            case.conversation = case.chat_service.create(case.manager, ConversationCreate(
                company_id=case.company["id"], title="AK01 offline",
                idempotency_key=uuid4().hex,
            ))["conversation"]
            case.turn = case.chat_service.enqueue(case.manager, case.conversation["id"], TurnCreate(
                question="如何核对当前资料？", expected_scope_version=1,
                idempotency_key=uuid4().hex,
            ))["turn"]
        return case

    case.setup = setup
    yield case
    if hasattr(case, "client"):
        case.client.close()


def execute(case):
    if case.target == "ocr":
        return extract(case, case.event)
    if case.target == "journal":
        return propose(case, case.mapping["draft_id"])
    case.chat_worker.run_once()
    return case.chat_service.turn(
        case.manager, case.conversation["id"], case.turn["id"],
    )["turn"]


def costs(case):
    with case.db.transaction() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM model_costs WHERE kind=?",
            ({"ocr": "ocr", "journal": "proposal", "chat": "chat"}[case.target],),
        )]


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_real_service_sends_endpoint_output_timeout_and_meters_once(service_case, scenario):
    case = service_case.setup(scenario)
    result = execute(case)
    assert result["status"] == "succeeded"
    assert len(case.calls) == 1
    request = case.calls[0]
    assert json.loads(request.content)["max_tokens"] == 173
    assert max(request.extensions["timeout"].values()) <= 2
    row, = costs(case)
    assert row["state"] == "estimated"
    assert (row["input_tokens"], row["output_tokens"], row["estimated_micro"]) == (3, 4, 11)
    execute(case)
    assert len(case.calls) == 1


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_real_service_tiny_context_does_not_send_or_hold_unknown_cost(service_case, scenario):
    case = service_case.setup(scenario, context_limit=1)
    result = execute(case)
    assert case.calls == []
    assert result["error_code"] == "context_too_large"
    assert result["status"] != "succeeded"
    assert all(row["state"] == "released" for row in costs(case))


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_endpoint_output_above_application_cap_is_intersected(service_case, scenario):
    case = service_case.setup(scenario, output_limit=8192)
    assert execute(case)["status"] == "succeeded"
    assert len(case.calls) == 1
    assert json.loads(case.calls[0].content)["max_tokens"] == 4096


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
@pytest.mark.parametrize("body", [
    {"usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
    {"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
    {"choices": [{}, {}], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
])
def test_invalid_structure_retains_valid_usage_through_real_service(service_case, scenario, body):
    case = service_case.setup(scenario)
    case.response_body = body
    assert execute(case)["status"] != "succeeded"
    assert len(case.calls) == 1
    row, = costs(case)
    assert row["state"] == "estimated"
    assert row["input_tokens"] == body["usage"]["prompt_tokens"]
    assert row["output_tokens"] == body["usage"]["completion_tokens"]


@pytest.mark.parametrize("context_limit", [128000, 1])
def test_paddle_native_field_model_uses_same_endpoint_limits(service_case, monkeypatch, context_limit):
    case = service_case.setup("ocr", context_limit=context_limit)
    case.real_provider.settings = replace(
        case.real_provider.settings, ocr_engine="paddleocr",
        paddle_url="https://ocr.example.invalid", paddle_token="synthetic-only",
    )
    from app.intelligence import service

    original = service.prepare_document

    def native_document(*args, **kwargs):
        pages = original(*args, **kwargs)
        return [PreparedPage(
            page.page, page.image_data_url,
            "invoice 合成发票\n" + invoice_document()["pages"][0]["raw_text"],
        ) for page in pages]

    monkeypatch.setattr(service, "prepare_document", native_document)
    result = execute(case)
    if context_limit == 1:
        assert case.calls == []
        assert result["error_code"] == "context_too_large"
        assert all(row["state"] == "released" for row in costs(case))
    else:
        assert result["status"] == "succeeded"
        assert len(case.calls) == 1
        assert json.loads(case.calls[0].content)["max_tokens"] == 173
        assert max(case.calls[0].extensions["timeout"].values()) <= 2
        assert costs(case)[0]["state"] == "estimated"


@pytest.mark.parametrize("context_limit", [128000, 1])
def test_paddle_bitmap_field_model_limits_after_real_ocr_transport(service_case, context_limit):
    from test_paddle_evidence import make_provider

    case = service_case.setup("ocr", context_limit=context_limit)
    synthetic, ocr_calls = make_provider()
    case.real_provider.settings = replace(
        case.real_provider.settings, ocr_engine="paddleocr",
        paddle_url="https://ocr.example.invalid", paddle_token="synthetic-only",
    )
    case.real_provider._paddle_client = synthetic._paddle_client
    try:
        result = execute(case)
        if context_limit == 1:
            # AK02 can reject the mandatory source pack before OCR or reservation.
            assert not ocr_calls["ocr"]
            assert not case.calls
            assert result["error_code"] == "context_too_large"
            assert not costs(case)
        else:
            assert len(ocr_calls["ocr"]) == 1
            assert result["status"] == "succeeded"
            assert len(case.calls) == 1
            assert json.loads(case.calls[0].content)["max_tokens"] == 173
            assert max(case.calls[0].extensions["timeout"].values()) <= 2
            assert costs(case)[0]["state"] == "estimated"
    finally:
        synthetic._paddle_client.close()
        synthetic._client.close()


def test_ocr_preparation_consumes_vision_call_deadline(service_case, monkeypatch):
    case = service_case.setup("ocr")
    from app.intelligence import provider, service

    clock = SimpleNamespace(value=1000.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.value)
    monkeypatch.setattr(provider, "time", fake_time)
    monkeypatch.setattr(service, "time", fake_time)
    original = service.prepare_document

    def delayed_document(*args, **kwargs):
        pages = original(*args, **kwargs)
        clock.value += 3
        return pages

    monkeypatch.setattr(service, "prepare_document", delayed_document)
    result = execute(case)
    assert case.calls == []
    assert result["error_code"] == "timeout"
    assert all(row["state"] == "released" for row in costs(case))


def test_chat_policy_check_consumes_original_deadline(service_case, monkeypatch):
    case = service_case.setup("chat")
    from app.assistant import worker
    from app.intelligence import provider

    clock = SimpleNamespace(value=1000.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.value)
    monkeypatch.setattr(provider, "time", fake_time)
    monkeypatch.setattr(worker, "time", fake_time, raising=False)
    original = AIEndpointPolicyService.check_send

    def delayed_policy(self, *args, **kwargs):
        evidence = original(self, *args, **kwargs)
        clock.value += 3
        return evidence

    monkeypatch.setattr(AIEndpointPolicyService, "check_send", delayed_policy)
    result = execute(case)
    assert case.calls == []
    assert result["error_code"] == "timeout"
    assert all(row["state"] == "released" for row in costs(case))


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
@pytest.mark.parametrize("usage, expected", [
    ({"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}, "estimated"),
    ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "estimated"),
    (None, "uncertain"),
])
def test_429_real_service_preserves_cost_and_does_not_resend(service_case, scenario, usage, expected):
    case = service_case.setup(scenario)
    case.response_status = 429
    case.response_body = {"error": {"message": "synthetic busy"}}
    if usage is not None:
        case.response_body["usage"] = usage
    assert execute(case)["status"] != "succeeded"
    assert len(case.calls) == 1
    row, = costs(case)
    assert row["state"] == expected
    if usage is not None:
        assert row["input_tokens"] == usage["prompt_tokens"]
        assert row["output_tokens"] == usage["completion_tokens"]
    else:
        assert row["estimated_micro"] is None
    execute(case)
    assert len(case.calls) == 1


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
@pytest.mark.parametrize("usage", [
    {"prompt_tokens": True, "completion_tokens": 0, "total_tokens": 1},
    {"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0},
    {"prompt_tokens": 0.5, "completion_tokens": 0.5, "total_tokens": 1},
    {"prompt_tokens": 1, "completion_tokens": 1},
    {"prompt_tokens": 5_000_001, "completion_tokens": 5_000_000, "total_tokens": 10_000_001},
])
def test_invalid_usage_never_becomes_free_through_real_service(service_case, scenario, usage):
    case = service_case.setup(scenario)
    case.response_body = {"choices": [], "usage": usage}
    assert execute(case)["status"] != "succeeded"
    assert len(case.calls) == 1
    row, = costs(case)
    assert row["state"] == "uncertain"
    assert row["estimated_micro"] is None


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_real_service_company_pause_blocks_transport(service_case, scenario):
    case = service_case.setup(scenario)
    case.intelligence.company_settings(case.manager, case.company["id"], enabled=False)
    if scenario == "chat":
        assert execute(case)["status"] != "succeeded"
    else:
        with pytest.raises(AccountingError):
            execute(case)
    assert not case.calls
    assert not costs(case)


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_real_service_exhausted_budget_blocks_transport(service_case, scenario):
    case = service_case.setup(scenario)
    policy(case, monthly_limit="0.01", chat_reserve="0.10")
    if scenario == "chat":
        assert execute(case)["status"] != "succeeded"
    else:
        with pytest.raises(AccountingError):
            execute(case)
    assert not case.calls
    assert not costs(case)


@pytest.mark.parametrize("key,value", [
    ("context_limit", True), ("context_limit", "100"),
    ("output_limit", False), ("output_limit", 1.5),
    ("timeout_seconds", True), ("timeout_seconds", float("nan")),
])
def test_evidence_limits_never_coerce_invalid_types(key, value):
    with pytest.raises(ProviderError, match="预算"):
        ExecutionLimits.from_evidence({key: value}, started_at=100.0)


def test_evidence_limits_cannot_extend_an_existing_deadline():
    limits = ExecutionLimits.from_evidence(
        {"timeout_seconds": 45}, started_at=100, timeout_cap=30, deadline_monotonic=120,
    )
    assert limits.timeout_seconds == 30
    assert limits.deadline_monotonic == 120
    refreshed = ExecutionLimits.from_evidence(
        {"context_limit": 1_000_000, "output_limit": 8192, "timeout_seconds": 80},
        prior_limits=limits, started_at=110,
    )
    assert refreshed.context_tokens == limits.context_tokens
    assert refreshed.output_tokens == limits.output_tokens
    assert refreshed.timeout_seconds == limits.timeout_seconds
    assert refreshed.deadline_monotonic == limits.deadline_monotonic


@pytest.mark.parametrize("value", [True, "123", float("nan"), float("inf")])
def test_execution_deadline_has_strict_finite_type(value):
    with pytest.raises(ProviderError):
        ExecutionLimits(deadline_monotonic=value)


@pytest.mark.parametrize("scenario", ["ocr", "journal"])
def test_policy_change_invalidates_frozen_request_before_send(service_case, monkeypatch, scenario):
    case = service_case.setup(scenario)
    original = case.meter.context

    def loosen_after_initial_authorization(*args, **kwargs):
        case.endpoint_service.update_policy(case.manager, case.company["id"], CompanyPolicyUpdate(
            managed=False, allowed_scenarios=["ocr", "journal", "chat"],
            allowed_data_categories=["document", "counterparty", "amount", "accounting", "chat"],
            reason="AK01 本地模拟政策变化",
        ))
        return original(*args, **kwargs)

    monkeypatch.setattr(case.meter, "context", loosen_after_initial_authorization)
    with pytest.raises(AccountingError):
        execute(case)
    assert not case.calls
    assert not costs(case)


@pytest.mark.parametrize("scenario", ["ocr", "journal", "chat"])
def test_highest_legal_usage_survives_real_service(service_case, scenario):
    case = service_case.setup(scenario)
    # Content failure still must retain the complete legal reported usage.
    case.response_body = {
        "choices": [], "usage": {
            "prompt_tokens": 5_000_000, "completion_tokens": 5_000_000,
            "total_tokens": 10_000_000,
        },
    }
    assert execute(case)["status"] != "succeeded"
    row, = costs(case)
    assert row["state"] == "estimated"
    assert row["input_tokens"] == row["output_tokens"] == 5_000_000
    assert row["estimated_micro"] == 15_000_000
    assert len(case.calls) == 1


def test_published_template_uses_zero_real_model_requests_and_no_proposal_fee(service_case):
    from app.jobs.worker import QueueWorker

    case = service_case.setup("journal", published_template=True)
    worker = QueueWorker(case.queue, worker_id="ak01-template-worker")
    assert worker.run_once()
    task = case.queue.proposals.get(
        case.actors["accountant"], case.mapping["automatic_job"]["id"],
    )
    assert task["status"] == "succeeded"
    assert task["current_run_id"] is None
    assert task["result_template_id"]
    assert not case.calls
    assert not costs(case)
    draft = case.ledger.get_draft(
        case.actors["accountant"], case.mapping["book_id"], case.mapping["draft_id"],
    )
    assert draft["status"] == "draft"


def _intake(case):
    from app.intake.models import IntakeRecognize
    from app.intake.service import IntakeService
    from test_stage12_intake import attach_intake, create_intake

    service = IntakeService(
        case.db, case.storage, case.real_provider, meter=case.meter,
        business=case.business, intelligence=case.intelligence,
    )
    draft = attach_intake(case, service, create_intake(case, service))
    request = IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex)

    def recognize(actor=None):
        return service.recognize(
            actor or case.actors["employee"], draft["id"],
            draft["attachments"][0]["id"], request,
        )

    return service, draft, recognize


@pytest.mark.parametrize("context_limit", [128000, 1])
def test_intake_entry_cannot_bypass_endpoint_limits(service_case, context_limit):
    case = service_case.setup("ocr", context_limit=context_limit)
    _, _, recognize = _intake(case)
    result = recognize()
    if context_limit == 1:
        assert not case.calls
        assert result["error_code"] == "context_too_large"
        # Mandatory ContextPack validation precedes reservation in AK02.
        assert not costs(case)
    else:
        assert result["status"] == "succeeded"
        assert len(case.calls) == 1
        assert json.loads(case.calls[0].content)["max_tokens"] == 173
        assert max(case.calls[0].extensions["timeout"].values()) <= 2
        assert costs(case)[0]["state"] == "estimated"
        recognize()
        assert len(case.calls) == 1


def test_intake_entry_rejects_unapproved_ocr_scenario(service_case):
    case = service_case.setup("ocr")
    _, _, recognize = _intake(case)
    case.endpoint_service.update_policy(case.manager, case.company["id"], CompanyPolicyUpdate(
        allowed_scenarios=["chat"], allowed_data_categories=["chat"],
        allowed_endpoint_ids=[case.endpoint["id"]], reason="AK01 禁止 OCR 外发",
    ))
    with pytest.raises(AccountingError):
        recognize()
    assert not case.calls
    assert not costs(case)


def test_intake_entry_rechecks_policy_before_publishing_and_keeps_usage(service_case):
    case = service_case.setup("ocr")
    _, _, recognize = _intake(case)

    def revoke_policy(_):
        case.endpoint_service.update_policy(case.manager, case.company["id"], CompanyPolicyUpdate(
            allowed_scenarios=["chat"], allowed_data_categories=["chat"],
            allowed_endpoint_ids=[case.endpoint["id"]], reason="AK01 在途政策撤回",
        ))

    case.response_hook = revoke_policy
    with pytest.raises(AccountingError):
        recognize()
    assert len(case.calls) == 1
    assert costs(case)[0]["state"] == "estimated"
    with case.db.transaction() as conn:
        run, = conn.execute("SELECT status,result_json FROM intake_runs").fetchall()
        assert run["status"] == "stale"
        assert run["result_json"] is None


def test_paddle_capacity_denial_preserves_transport_unsent_evidence(service_case, monkeypatch):
    from app.concurrency import provider as capacity
    from app.intelligence import service

    case = service_case.setup("ocr")
    case.real_provider.settings = replace(
        case.real_provider.settings, ocr_engine="paddleocr",
        paddle_url="https://ocr.example.invalid", paddle_token="synthetic-only",
    )
    original = service.prepare_document

    def native_document(*args, **kwargs):
        return [PreparedPage(
            page.page, page.image_data_url,
            "invoice 合成发票\n" + invoice_document()["pages"][0]["raw_text"],
        ) for page in original(*args, **kwargs)]

    @contextmanager
    def deny_capacity(*args, **kwargs):
        raise AccountingError("合成授权变更，供应商调用未发送", 403)
        yield  # pragma: no cover

    monkeypatch.setattr(service, "prepare_document", native_document)
    monkeypatch.setattr(capacity, "provider_capacity", deny_capacity)
    with pytest.raises(AccountingError) as caught:
        execute(case)
    assert caught.value.llm_attempted is False
    assert not case.calls
    assert costs(case)[0]["state"] == "released"
