"""Synthetic OCR contracts and actual scoped persistence; no cloud/model calls."""

import base64
from dataclasses import replace
from io import BytesIO
import json
import os
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventCreate
from app.intelligence.cache import config_key
from app.intelligence.documents import prepare_document
from app.intelligence.models import InvoiceExtraction
from app.intelligence.ocr import extraction_findings, prompt_version
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError
from app.intelligence.requests import OCRRequest
from app.intelligence.router import build_intelligence_router
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from app.operations.requests import PolicyRequest
from app.operations.service import OperationsService
from test_intelligence_workflow import ai_case, create_event, invoice_document
from test_stage12_intake import attach_intake, confirmed, create_intake, intake
from app.intake.models import IntakeRecognize


def settings(**kwargs):
    return AISettings(
        base_url="https://llm.example.invalid/v1",
        api_key="test-llm-key",
        vision_model="fields-test",
        ocr_engine="paddleocr",
        paddle_url="https://ocr.example.invalid",
        paddle_token="test-ocr-key",
        **kwargs,
    )


def make_provider(document=None, *, score=0.99, mutate=None, failure=None, ocr_hook=None):
    document = document or invoice_document()
    calls = {"ocr": [], "llm": []}
    provider = OpenAICompatibleProvider(settings())

    def recognize(request):
        calls["ocr"].append(request)
        if ocr_hook:
            ocr_hook()
        pages = []
        for page in json.loads(request.content)["pages"]:
            with Image.open(
                BytesIO(base64.b64decode(page["image_data_url"].split(",")[1]))
            ) as image:
                width, height = image.size
            blocks = [
                {
                    "id": f"p{page['page']}-b{index}",
                    "text": ref["quote"],
                    "score": score,
                    "polygon": [[0, 0], [width, 0], [width, height], [0, height]],
                }
                for index, ref in enumerate(document["evidence"])
            ]
            pages.append({"page": page["page"], "width": width, "height": height, "blocks": blocks})
        result = {
            "schema_version": "paddle-ocr-v1",
            "engine": "paddleocr",
            "engine_version": "3.7.0",
            "model": provider.settings.paddle_model,
            "pages": pages,
            "elapsed_ms": 12,
        }
        if mutate:
            mutate(result)
        return httpx.Response(200, json=result)

    def model(request):
        calls["llm"].append(request)
        if failure:
            return httpx.Response(failure, json={"error": "test-only"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"documents": [document]})},
                    }
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 30, "total_tokens": 51},
            },
        )

    provider._paddle_client = httpx.Client(
        transport=httpx.MockTransport(recognize), trust_env=False
    )
    provider._client = httpx.Client(transport=httpx.MockTransport(model), trust_env=False)
    return provider, calls


def pages(size=(40, 20)):
    buffer = BytesIO()
    Image.new("RGB", size, "white").save(buffer, "PNG")
    return prepare_document(buffer.getvalue(), "image/png", high_resolution=True)


def identify(case, event, **kwargs):
    return case.intelligence.extract(
        case.actors["employee"],
        event["id"],
        OCRRequest(
            attachment_id=event["attachments"][0]["id"],
            expected_version=event["version"],
            idempotency_key=uuid4().hex,
            **kwargs,
        ),
    )


def install(case, **kwargs):
    provider, calls = make_provider(**kwargs)
    case.provider = case.intelligence.provider = provider
    return provider, calls


def test_independent_schema_never_enters_model_prompt_and_pixels_are_not_sent_to_llm():
    provider, calls = make_provider()
    result = provider.extract_invoice(pages())
    schema = json.loads(calls["llm"][0].content)["response_format"]["json_schema"]["schema"]
    assert "ocr_evidence" not in schema["properties"]
    assert "image_url" not in calls["llm"][0].content.decode()
    assert calls["ocr"][0].headers["authorization"] == "Bearer test-ocr-key"
    assert calls["llm"][0].headers["authorization"] == "Bearer test-llm-key"
    assert isinstance(result.data, InvoiceExtraction)
    assert result.data.ocr_evidence.bindings[0].block_ids == ["p1-b0"]
    assert result.processing_usage["self_hosted_cost_status"] == "unpriced"
    assert not extraction_findings(result.data, pages())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(model="wrong-version"),
        lambda r: r.update(schema_version="unknown"),
        lambda r: r["pages"][0].update(width=999),
        lambda r: r["pages"][0]["blocks"][0].update(polygon=[[0, 0], [500, 0], [1, 1], [0, 1]]),
        lambda r: r["pages"][0]["blocks"].append(r["pages"][0]["blocks"][0]),
    ],
)
def test_bad_ocr_contract_blocks_llm_without_fallback(mutation):
    provider, calls = make_provider(mutate=mutation)
    with pytest.raises(ProviderError, match="格式、坐标或模型版本"):
        provider.extract_invoice(pages())
    assert not calls["llm"]


def test_low_score_is_blocking_and_original_native_conflict_is_not_erased():
    provider, _ = make_provider(score=0.2)
    prepared = pages()
    prepared[0] = replace(prepared[0], native_text="原文实际金额 999.00")
    result = provider.extract_invoice(prepared)
    findings = extraction_findings(result.data, prepared)
    assert any(f["code"].endswith("ocr_gross_amount") for f in findings)
    assert any(f["code"].endswith("evidence_gross_amount") for f in findings)
    assert all(f["severity"] == "blocking" for f in findings)


def test_native_pdf_text_avoids_ocr_but_still_uses_same_field_contract():
    document = invoice_document()
    document["pages"][0]["raw_text"] = "发票 合计 " + document["pages"][0]["raw_text"]
    provider, calls = make_provider(document)
    prepared = [replace(pages()[0], native_text=document["pages"][0]["raw_text"])]
    result = provider.extract_invoice(prepared)
    assert not calls["ocr"] and len(calls["llm"]) == 1
    assert result.data.ocr_evidence.pages[0].source == "pdf_native"
    assert not extraction_findings(result.data, prepared)


def test_hallucinated_raw_text_cannot_create_evidence():
    def mutate(payload):
        payload["pages"][0]["blocks"] = [
            {
                "id": "original",
                "text": "无法读取",
                "score": 0.99,
                "polygon": [[0, 0], [40, 0], [40, 20], [0, 20]],
            }
        ]

    provider, _ = make_provider(mutate=mutate)
    result = provider.extract_invoice(pages())
    assert result.data.documents[0].pages[0].raw_text == "无法读取"
    assert any(
        f["code"].endswith("evidence_gross_amount")
        for f in extraction_findings(result.data, pages())
    )


def test_model_failure_preserves_ocr_resource_usage():
    provider, _ = make_provider(failure=500)
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(pages())
    assert caught.value.processing_usage["recognized_pages"] == 1
    assert caught.value.processing_usage["self_hosted_cost_status"] == "unpriced"


def test_between_services_reauthorization_prevents_llm_send():
    provider, calls = make_provider()

    def denied():
        if calls["ocr"]:
            raise AccountingError("授权已撤销", 403)

    with pytest.raises(AccountingError):
        provider.extract_invoice(pages(), authorization_check=denied)
    assert len(calls["ocr"]) == 1 and not calls["llm"]


def test_http_only_exact_configured_internal_host_and_secrets_not_fingerprint():
    with pytest.raises(ProviderError):
        replace(settings(), paddle_url="http://ocr:8000")
    safe = replace(
        settings(), paddle_url="http://ocr:8000", paddle_allow_http=True, paddle_allowed_host="ocr"
    )
    assert safe.extraction_available
    with pytest.raises(ProviderError):
        replace(safe, paddle_url="http://other:8000")
    with pytest.raises(ProviderError):
        replace(safe, paddle_url="https://ocr/a?token=x")
    assert config_key(safe, "image/png") == config_key(
        replace(safe, paddle_token="new-token"), "image/png"
    )
    assert config_key(safe, "image/png") != config_key(
        replace(safe, paddle_min_score=0.9), "image/png"
    )
    assert prompt_version(safe) != prompt_version(replace(safe, paddle_model="next-model-version"))


def test_high_resolution_is_bounded_and_original_pixels_are_preserved():
    prepared = pages((3600, 1800))
    with Image.open(BytesIO(base64.b64decode(prepared[0].image_data_url.split(",")[1]))) as image:
        assert image.size == (3600, 1800)
    prepared = pages((5000, 1000))
    with Image.open(BytesIO(base64.b64decode(prepared[0].image_data_url.split(",")[1]))) as image:
        assert image.size == (4000, 800)


def test_cache_persists_independent_evidence_without_new_charge(ai_case):
    _, calls = install(ai_case)
    event = create_event(ai_case)
    first = identify(ai_case, event)
    assert first["status"] == "succeeded", first
    second = identify(ai_case, event)
    assert second["cache_hit"] == 1
    assert first["result"]["ocr_evidence"] == second["result"]["ocr_evidence"]
    assert first["usage"]["ocr_processing"]["recognized_pages"] == 1
    assert second["usage"] == {}
    assert len(calls["ocr"]) == len(calls["llm"]) == 1
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM model_costs WHERE company_id=?", (ai_case.company["id"],)
            ).fetchone()[0]
            == 1
        )


def test_low_confidence_evidence_does_not_enter_reusable_cache(ai_case):
    _, calls = install(ai_case, score=0.1)
    event = create_event(ai_case)
    first, second = identify(ai_case, event), identify(ai_case, event)
    assert first["status"] == second["status"] == "succeeded"
    assert not second["cache_hit"] and len(calls["ocr"]) == 2
    with ai_case.db.transaction(actor=ai_case.actors["employee"]) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM ai_findings WHERE severity='blocking' AND company_id=?",
                (ai_case.company["id"],),
            ).fetchone()[0]
            > 0
        )


def test_preview_enforces_current_scope_hash_and_no_store(ai_case):
    install(ai_case)
    event = create_event(ai_case)
    run = identify(ai_case, event)
    actor = ai_case.actors["employee"]
    service = ai_case.intelligence
    assert service.evidence_page(actor, event["id"], run["id"], 1).startswith(b"\xff\xd8")
    with pytest.raises(AccountingError):
        service.evidence_page(ai_case.actors["peer"], event["id"], run["id"], 1)
    with pytest.raises(AccountingError):
        service.evidence_page(ai_case.actors["outsider"], event["id"], run["id"], 1)
    with pytest.raises(AccountingError):
        service.evidence_page(actor, event["id"], run["id"], 2)
    app = FastAPI()
    app.state.intelligence = service
    app.include_router(build_intelligence_router(lambda: actor))
    with TestClient(app) as client:
        response = client.get(
            f"/api/business/events/{event['id']}/intelligence/runs/{run['id']}/pages/1"
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-type"] == "image/jpeg"
    metadata = ai_case.business.download_metadata(actor, event["attachments"][0]["id"])
    (ai_case.storage.root / metadata["storage_key"]).write_bytes(b"tampered")
    with pytest.raises(AccountingError):
        service.evidence_page(actor, event["id"], run["id"], 1)


def test_company_cache_isolation_and_revocation(ai_case):
    _, calls = install(ai_case)
    event = create_event(ai_case)
    identify(ai_case, event)
    outsider = ai_case.actors["outsider"]
    other = ai_case.business.create(
        outsider,
        BusinessEventCreate(
            company_id=ai_case.other_company["id"],
            template_id="reimbursement",
            payload=event["payload"],
        ),
    )
    metadata = ai_case.business.download_metadata(
        ai_case.actors["employee"], event["attachments"][0]["id"]
    )
    attachment = ai_case.storage.put("same.png", "image/png", ai_case.storage.read(metadata))
    other = ai_case.business.add_attachment(outsider, other["id"], other["version"], attachment)
    ai_case.intelligence.company_settings(ai_case.manager, ai_case.other_company["id"], True)
    result = ai_case.intelligence.extract(
        outsider,
        other["id"],
        OCRRequest(
            attachment_id=other["attachments"][0]["id"],
            expected_version=other["version"],
            idempotency_key=uuid4().hex,
        ),
    )
    assert not result["cache_hit"] and len(calls["ocr"]) == 2
    ai_case.directory.revoke_company_member(
        ai_case.manager, ai_case.company["id"], ai_case.actors["employee"].user_id
    )
    with pytest.raises(AccountingError):
        identify(ai_case, event)


def test_intake_preserves_independent_evidence_when_confirmed_into_business(ai_case):
    install(ai_case)
    service = intake(ai_case)
    draft = attach_intake(ai_case, service, create_intake(ai_case, service))
    result = service.recognize(
        ai_case.actors["employee"],
        draft["id"],
        draft["attachments"][0]["id"],
        IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex),
    )
    assert result["status"] == "succeeded", result
    assert result["result"]["ocr_evidence"]["attachment_sha256"]
    completed = service.complete(ai_case.actors["employee"], draft["id"], confirmed(draft))
    detail = ai_case.intelligence.detail(ai_case.actors["employee"], completed["event"]["id"])
    assert detail["runs"][0]["result"]["ocr_evidence"]["bindings"]


def test_quota_reserved_before_ocr_and_actual_llm_tokens_are_charged(ai_case):
    _, calls = install(ai_case)
    ai_case.intelligence.meter = MeterService(ai_case.db, Pricing(ocr=Rate("1", "1")))
    operations = OperationsService(ai_case.db, ai_case.intelligence._meter(), ai_case.intelligence)
    operations.policy(
        ai_case.manager,
        ai_case.company["id"],
        PolicyRequest(
            expected_version=0,
            quota_enabled=True,
            monthly_limit="1.00",
            ocr_reserve="1.00",
            proposal_reserve="1.00",
        ),
    )
    event = create_event(ai_case)
    result = identify(ai_case, event)
    assert result["status"] == "succeeded"
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        cost = conn.execute("SELECT * FROM model_costs WHERE run_id=?", (result["id"],)).fetchone()
        assert cost["estimated_micro"] == 51 and cost["state"] == "estimated"
    with pytest.raises(AccountingError, match="额度不足"):
        identify(ai_case, event, force_refresh=True)
    assert len(calls["ocr"]) == len(calls["llm"]) == 1


def test_failed_model_keeps_completed_ocr_metrics_in_database(ai_case):
    install(ai_case, failure=500)
    result = identify(ai_case, create_event(ai_case))
    assert result["status"] == "failed" and result["result"] is None
    assert result["usage"]["ocr_processing"]["recognized_pages"] == 1
    assert result["usage"]["ocr_processing"]["self_hosted_cost_status"] == "unpriced"
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        assert (
            conn.execute(
                "SELECT state FROM model_costs WHERE run_id=?", (result["id"],)
            ).fetchone()[0]
            == "uncertain"
        )


def test_invalid_ocr_response_releases_llm_reserve_and_allows_intake_retry(ai_case):
    provider, calls = install(
        ai_case, mutate=lambda payload: payload.update(model="unexpected-model")
    )
    service = intake(ai_case)
    draft = attach_intake(ai_case, service, create_intake(ai_case, service))
    result = service.recognize(
        ai_case.actors["employee"],
        draft["id"],
        draft["attachments"][0]["id"],
        IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex),
    )
    assert result["status"] == "failed"
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        assert (
            conn.execute(
                "SELECT state FROM model_costs WHERE intake_run_id=?", (result["id"],)
            ).fetchone()[0]
            == "released"
        )
    second = service.recognize(
        ai_case.actors["employee"],
        draft["id"],
        draft["attachments"][0]["id"],
        IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex),
    )
    assert second["id"] != result["id"] and len(calls["ocr"]) == 2
    assert not calls["llm"]


def test_post_model_binding_failure_preserves_consumed_tokens(monkeypatch):
    from app.intelligence import ocr

    provider, _ = make_provider()

    def fail(*args, **kwargs):
        raise ValueError("invalid bounded evidence")

    monkeypatch.setattr(ocr, "_binding", fail)
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(pages())
    assert caught.value.llm_attempted is True
    assert ocr.usage_payload(error=caught.value)["total_tokens"] == 51


def test_revocation_after_ocr_stops_llm_and_marks_stale(ai_case):
    def revoke():
        ai_case.directory.revoke_company_member(
            ai_case.manager, ai_case.company["id"], ai_case.actors["employee"].user_id
        )

    _, calls = install(ai_case, ocr_hook=revoke)
    event = create_event(ai_case)
    with pytest.raises(AccountingError):
        identify(ai_case, event)
    assert len(calls["ocr"]) == 1 and not calls["llm"]
    if ai_case.db.is_postgres:
        # An administrator is not entitled to inspect an employee's private
        # draft. Test the persisted state through the isolated test operator.
        import psycopg

        with psycopg.connect(os.environ["ACCOUNTING_TEST_POSTGRES_URL"]) as conn:
            run = conn.execute(
                "SELECT status,result_json FROM accounting.ai_runs WHERE event_id=%s",
                (event["id"],),
            ).fetchone()
        assert run == ("stale", None)
    else:
        with ai_case.db.transaction(actor=ai_case.manager) as conn:
            run = conn.execute(
                "SELECT status,result_json FROM ai_runs WHERE event_id=?", (event["id"],)
            ).fetchone()
            assert run["status"] == "stale" and run["result_json"] is None


def test_ambiguous_short_quote_does_not_highlight_arbitrary_first_block():
    document = invoice_document()
    document["evidence"] = [
        {"field": "gross_amount", "page": 1, "quote": "0.30"},
        {"field": "net_amount", "page": 1, "quote": "0.30"},
    ]
    provider, _ = make_provider(document)
    result = provider.extract_invoice(pages())
    assert all(
        binding.status == "unmatched" and not binding.block_ids
        for binding in result.data.ocr_evidence.bindings
    )


def test_multpage_images_batch_under_service_pixel_limit_and_recheck_permissions():
    provider, calls = make_provider()
    prepared = [replace(pages((4000, 3200))[0], page=index) for index in range(1, 4)]
    checked = []
    result = provider.extract_invoice(prepared, authorization_check=lambda: checked.append(True))
    assert len(calls["ocr"]) == 3 and len(calls["llm"]) == 1
    assert len(checked) == 4
    assert result.data.ocr_evidence.recognized_pages == 3
    assert [page.page for page in result.data.ocr_evidence.pages] == [1, 2, 3]


def test_native_invalid_quote_binding_is_unmatched():
    provider, _ = make_provider()
    prepared = [
        replace(
            pages()[0], native_text="发票 合计 9999.00 这是完整但不同于模型声称的电子原件内容 " * 3
        )
    ]
    result = provider.extract_invoice(prepared)
    assert all(binding.status == "unmatched" for binding in result.data.ocr_evidence.bindings)


def test_compressed_ocr_response_is_rejected_without_llm():
    import gzip

    provider, calls = make_provider()
    provider._paddle_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=gzip.compress(b"{}"), headers={"Content-Encoding": "gzip"}
            )
        )
    )
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(pages())
    assert caught.value.code == "ocr_response_invalid" and not calls["llm"]
    assert caught.value.llm_attempted is False


def test_later_ocr_batch_failure_preserves_completed_pages_and_never_sends_partial_llm():
    provider, calls = make_provider()
    original = provider._paddle_client
    attempts = []

    def recognize(request):
        attempts.append(True)
        if len(attempts) == 1:
            return original.send(request)
        return httpx.Response(503, json={"detail": "busy"})

    provider._paddle_client = httpx.Client(transport=httpx.MockTransport(recognize))
    prepared = [replace(pages((4000, 3200))[0], page=index) for index in range(1, 4)]
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(prepared)
    assert caught.value.processing_usage["recognized_pages"] == 1
    assert caught.value.processing_usage["requested_pages"] == 3
    assert caught.value.llm_attempted is False and not calls["llm"]


def test_all_ocr_batches_share_one_deadline_and_late_batch_is_not_sent(monkeypatch):
    from app.intelligence import ocr

    provider, calls = make_provider()
    clock = [0.0]
    monkeypatch.setattr(ocr.time, "monotonic", lambda: clock[0])
    original = ocr._recognize

    def recognize(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 46.0
        return result

    monkeypatch.setattr(ocr, "_recognize", recognize)
    prepared = [replace(pages((4000, 3200))[0], page=index) for index in range(1, 4)]
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(prepared, processing_deadline=150)
    assert caught.value.code == "ocr_timeout"
    assert caught.value.processing_usage["recognized_pages"] == 1
    assert len(calls["ocr"]) == 1 and not calls["llm"]


def test_slow_unbuffered_response_checks_deadline_for_each_network_chunk(monkeypatch):
    from app.intelligence import ocr

    provider, calls = make_provider()
    clock = [0.0]
    monkeypatch.setattr(ocr.time, "monotonic", lambda: clock[0])

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            clock[0] = 46.0
            yield b"}"

    provider._paddle_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowStream()))
    )
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(pages(), processing_deadline=150)
    assert caught.value.code == "ocr_timeout" and not calls["llm"]


def test_expensive_document_preparation_reserves_time_for_llm_before_sending(monkeypatch):
    from app.intelligence import ocr

    provider, calls = make_provider()
    monkeypatch.setattr(ocr.time, "monotonic", lambda: 110.0)
    with pytest.raises(ProviderError) as caught:
        provider.extract_invoice(pages(), processing_deadline=150)
    assert caught.value.code == "ocr_timeout"
    assert not calls["ocr"] and not calls["llm"]


def test_private_preview_keeps_evidence_coordinates_but_fits_gateway():
    from app.intelligence.documents import MAX_PREVIEW_BYTES, preview_page_bytes
    from app.intelligence.models import PreparedPage

    with Image.effect_noise((2600, 2000), 100).convert("RGB") as image:
        original = BytesIO()
        image.save(original, format="JPEG", quality=97, optimize=True)
    original = original.getvalue()
    assert len(original) > MAX_PREVIEW_BYTES
    page = PreparedPage(1, "data:image/jpeg;base64," + base64.b64encode(original).decode())
    preview = preview_page_bytes(page)
    assert len(preview) <= MAX_PREVIEW_BYTES
    with Image.open(BytesIO(preview)) as image:
        assert image.size == (2600, 2000)
    assert base64.b64decode(page.image_data_url.split(",", 1)[1]) == original
