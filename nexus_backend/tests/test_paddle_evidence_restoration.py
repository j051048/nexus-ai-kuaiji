"""Synthetic private OCR transport; never Paddle weights, network or live models."""

import base64
from copy import deepcopy
import hashlib
import io
import json

import httpx
from PIL import Image
import pytest

from app.intelligence.checks import extraction_findings
from app.intelligence.models import InvoiceExtraction, PreparedPage
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError
from test_intelligence_checks import document
from test_intelligence_workflow import ai_case, create_event, extract


SOURCE = {"firm_id": "firm-a", "company_id": "company-a", "attachment_sha256": "a" * 64}


def settings():
    return AISettings.from_env(
        {
            "ACCOUNTING_AI_BASE_URL": "https://model.example.invalid/v1",
            "ACCOUNTING_AI_API_KEY": "synthetic-key",
            "ACCOUNTING_AI_VISION_MODEL": "synthetic-text-model",
            "ACCOUNTING_OCR_ENGINE": "paddleocr",
            "ACCOUNTING_PADDLEOCR_URL": "https://ocr.example.invalid",
            "ACCOUNTING_PADDLEOCR_TOKEN": "synthetic-private-ocr-token",
        }
    )


def page(text=""):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, "PNG")
    return PreparedPage(
        1, "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(), text
    )


def provider_case(*, score=0.99, tamper=None, fail_ocr=False, ocr_hook=None):
    config = settings()
    value = document()
    value["pages"][0]["raw_text"] = value["pages"][0]["raw_text"].replace(
        "gross_amount 10.60", "Gross Total: 10.60"
    )
    for reference in value["evidence"]:
        if reference["field"] == "gross_amount":
            reference["quote"] = "Gross Total: 10.60"
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "ocr.example.invalid":
            if ocr_hook:
                ocr_hook()
            if fail_ocr:
                return httpx.Response(503, json={"error": "synthetic-private-detail"})
            payload = {
                "schema_version": "paddle-ocr-v1",
                "engine": "paddleocr",
                "engine_version": "synthetic-1",
                "model": config.paddle_model,
                "pages": [
                    {
                        "page": 1,
                        "width": 40,
                        "height": 20,
                        "blocks": [
                            {
                                "id": f"p1-b{i}",
                                "text": line,
                                "score": score,
                                "polygon": [[0, 0], [40, 0], [40, 20], [0, 20]],
                            }
                            for i, line in enumerate(value["pages"][0]["raw_text"].splitlines())
                        ],
                    }
                ],
                "elapsed_ms": 2,
            }
            if tamper:
                tamper(payload)
            return httpx.Response(200, json=payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"documents": [value]}, ensure_ascii=False)
                        },
                    }
                ],
                "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(config, client=client)
    provider._paddle_client = client
    return provider, calls, value, client


def test_private_ocr_configuration_is_not_silently_ignored():
    config = settings()
    assert getattr(config, "ocr_engine", None) == "paddleocr"
    assert getattr(config, "paddle_url", None) == "https://ocr.example.invalid"


def test_private_ocr_binds_independent_blocks_original_and_usage():
    provider, calls, _, client = provider_case()
    with client:
        result = provider.extract_invoice([page()], source_binding=SOURCE)
    evidence = result.data.ocr_evidence
    assert [request.url.host for request in calls] == [
        "ocr.example.invalid",
        "model.example.invalid",
    ]
    assert evidence.attachment_sha256 == SOURCE["attachment_sha256"]
    assert evidence.company_id == SOURCE["company_id"]
    assert evidence.firm_id == SOURCE["firm_id"]
    assert (
        evidence.pages[0].image_sha256 == hashlib.sha256(page().image_data_url.encode()).hexdigest()
    )
    assert all(binding.block_ids for binding in evidence.bindings)
    assert extraction_findings(result.data, [page()]) == []
    assert result.usage.total_tokens == 120
    assert result.processing_usage["self_hosted_cost_status"] == "unpriced"
    model_request = json.loads(calls[-1].content)
    assert "image_url" not in json.dumps(model_request)
    assert (
        "ocr_evidence"
        not in model_request["response_format"]["json_schema"]["schema"]["properties"]
    )


def test_model_cannot_forge_independent_evidence():
    with pytest.raises(ValueError):
        InvoiceExtraction.model_validate({"documents": [document()], "ocr_evidence": {}})


def test_pdf_with_complete_native_text_needs_no_paddle_request():
    provider, calls, value, client = provider_case()
    original = page(value["pages"][0]["raw_text"])
    with client:
        result = provider.extract_invoice([original], source_binding=SOURCE)
    assert len(calls) == 1 and calls[0].url.host == "model.example.invalid"
    assert extraction_findings(result.data, [original]) == []
    assert result.processing_usage["recognized_pages"] == 0
    assert result.processing_usage["native_pages"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(model="wrong-model"),
        lambda value: value["pages"][0].update(page=2),
        lambda value: value["pages"][0].update(width=41),
        lambda value: value["pages"][0]["blocks"][0].update(polygon=[[999, 0]] * 4),
    ],
)
def test_wrong_ocr_source_shape_stops_before_model(mutation):
    provider, calls, _, client = provider_case(tamper=mutation)
    with client, pytest.raises(ProviderError) as failure:
        provider.extract_invoice([page()], source_binding=SOURCE)
    assert failure.value.code == "ocr_response_invalid"
    assert failure.value.llm_attempted is False
    assert len(calls) == 1


def test_ocr_outage_never_falls_back_to_vision_or_charges_model():
    provider, calls, _, client = provider_case(fail_ocr=True)
    with client, pytest.raises(ProviderError) as failure:
        provider.extract_invoice([page()], source_binding=SOURCE)
    assert failure.value.code == "ocr_service_unavailable"
    assert failure.value.llm_attempted is False
    assert failure.value.usage is None
    assert len(calls) == 1


def test_low_score_and_pdf_text_conflict_remain_blocking():
    provider, _, _, client = provider_case(score=0.3)
    with client:
        result = provider.extract_invoice([page()], source_binding=SOURCE)
    assert any("_ocr_" in issue["code"] for issue in extraction_findings(result.data, [page()]))
    provider, _, _, client = provider_case()
    conflict = page("gross_amount 999.00")
    with client:
        result = provider.extract_invoice([conflict], source_binding=SOURCE)
    assert any(
        "gross_amount" in issue["code"] for issue in extraction_findings(result.data, [conflict])
    )


def test_changed_rendered_page_cannot_reuse_independent_proof():
    provider, _, _, client = provider_case()
    with client:
        result = provider.extract_invoice([page()], source_binding=SOURCE)
    changed = PreparedPage(1, page().image_data_url + "changed")
    assert any(
        issue["severity"] == "blocking" for issue in extraction_findings(result.data, [changed])
    )


def test_current_access_rechecked_after_ocr_before_model():
    provider, calls, _, client = provider_case()
    checks = []

    def reauthorize():
        checks.append(True)
        if len(checks) == 2:
            from app.accounting.domain import AccountingError

            raise AccountingError("synthetic revoked", 403)

    with client, pytest.raises(Exception) as failure:
        provider.extract_invoice([page()], source_binding=SOURCE, authorization_check=reauthorize)
    assert getattr(failure.value, "llm_attempted", None) is False
    assert len(calls) == 1


def test_event_service_persists_independent_source_and_cache_without_new_attempt(ai_case):
    provider, calls, _, client = provider_case()
    ai_case.intelligence.provider = provider
    event = create_event(ai_case, amount="10.60")
    with client:
        first = extract(ai_case, event)
        assert first["status"] == "succeeded"
        stored = first["result"]["ocr_evidence"]
        assert stored["company_id"] == event["company_id"]
        assert stored["attachment_sha256"] == event["attachments"][0]["sha256"]
        again = extract(ai_case, event, key="second-cache-key")
        assert again["status"] == "succeeded"
    assert len(calls) == 2
    assert bool(again["cache_hit"]) is True
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        assert conn.execute("SELECT count(*) FROM model_costs").fetchone()[0] == 1


def test_cache_foreign_company_proof_is_not_accepted(ai_case):
    from app.intelligence.cache import read_cache
    from app.business.service import encode

    provider, _, _, client = provider_case()
    ai_case.intelligence.provider = provider
    event = create_event(ai_case, amount="10.60")
    with client:
        result = extract(ai_case, event)
    with ai_case.db.transaction(write=True, actor=ai_case.manager) as conn:
        row = conn.execute("SELECT * FROM document_extractions").fetchone()
        altered = deepcopy(result["result"])
        altered["ocr_evidence"]["company_id"] = ai_case.other_company["id"]
        conn.execute("UPDATE document_extractions SET result_json=?", (encode(altered),))
        attachment = conn.execute(
            "SELECT * FROM business_attachments WHERE id=?", (event["attachments"][0]["id"],)
        ).fetchone()
        assert (
            read_cache(ai_case.db, conn, ai_case.manager, event, attachment, row["config_key"])
            is None
        )


def test_intake_evidence_survives_confirmed_transfer_without_second_charge(ai_case):
    from app.intake.models import IntakeRecognize
    from test_stage12_intake import intake, create_intake, confirmed

    provider, calls, _, client = provider_case()
    ai_case.provider = ai_case.intelligence.provider = provider
    service = intake(ai_case)
    draft = create_intake(ai_case, service)
    original = base64.b64decode(page().image_data_url.split(",", 1)[1])
    draft = service.add_attachment(
        ai_case.actors["employee"],
        draft["id"],
        draft["version"],
        ai_case.storage.put("synthetic.png", "image/png", original),
    )
    attachment_id = draft["attachments"][0]["id"]
    with client:
        recognized = service.recognize(
            ai_case.actors["employee"],
            draft["id"],
            attachment_id,
            IntakeRecognize(
                expected_version=draft["version"], idempotency_key="private-intake-ocr"
            ),
        )
        assert recognized["status"] == "succeeded"
        assert recognized["checks"] == []
        result = service.complete(ai_case.actors["employee"], draft["id"], confirmed(draft))
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        run = conn.execute(
            "SELECT * FROM ai_runs WHERE event_id=?", (result["event"]["id"],)
        ).fetchone()
        evidence = json.loads(run["result_json"])["ocr_evidence"]
        assert evidence == recognized["result"]["ocr_evidence"]
        assert conn.execute("SELECT count(*) FROM model_costs").fetchone()[0] == 1
    assert len(calls) == 2


def test_managed_policy_must_authorize_paddle_as_well_as_field_model(ai_case):
    from app.accounting.domain import AccountingError
    from app.intelligence.endpoints import (
        AIEndpointPolicyService,
        EndpointCreate,
        CapabilityCreate,
        CompanyPolicyUpdate,
    )

    provider, calls, _, client = provider_case()
    ai_case.intelligence.provider = provider
    policies = AIEndpointPolicyService(ai_case.db)
    approved = policies.create_endpoint(
        ai_case.manager,
        EndpointCreate(
            host="model.example.invalid",
            path_prefix="/v1",
            service_identity="synthetic-model",
            capabilities=[
                CapabilityCreate(
                    scenario="ocr",
                    model="synthetic-text-model",
                    data_location="synthetic",
                    reason="local synthetic test",
                )
            ],
            reason="local synthetic test",
        ),
    )
    policies.update_policy(
        ai_case.manager,
        ai_case.company["id"],
        CompanyPolicyUpdate(
            allowed_scenarios=["ocr"],
            allowed_data_categories=["document", "counterparty", "amount"],
            allowed_endpoint_ids=[approved["id"]],
            reason="local synthetic test",
        ),
    )
    event = create_event(ai_case)
    with client, pytest.raises(AccountingError) as failure:
        extract(ai_case, event)
    assert failure.value.status == 403
    assert calls == []


def test_managed_policy_allows_two_explicitly_approved_targets(ai_case):
    from app.intelligence.endpoints import (
        AIEndpointPolicyService,
        EndpointCreate,
        CapabilityCreate,
        CompanyPolicyUpdate,
    )

    provider, calls, _, client = provider_case()
    ai_case.intelligence.provider = provider
    policies = AIEndpointPolicyService(ai_case.db)
    approved = []
    for host, model, prefix in (
        ("model.example.invalid", provider.settings.vision_model, "/v1"),
        ("ocr.example.invalid", provider.settings.paddle_model, ""),
    ):
        endpoint = policies.create_endpoint(
            ai_case.manager,
            EndpointCreate(
                host=host,
                path_prefix=prefix,
                service_identity="synthetic-private-target",
                capabilities=[
                    CapabilityCreate(
                        scenario="ocr",
                        model=model,
                        data_location="synthetic",
                        reason="synthetic endpoint approval",
                    )
                ],
                reason="synthetic endpoint approval",
            ),
        )
        approved.append(endpoint["id"])
    policies.update_policy(
        ai_case.manager,
        ai_case.company["id"],
        CompanyPolicyUpdate(
            allowed_scenarios=["ocr"],
            allowed_data_categories=["document", "counterparty", "amount"],
            allowed_endpoint_ids=approved,
            reason="synthetic two target approval",
        ),
    )
    event = create_event(ai_case, amount="10.60")
    with client:
        run = extract(ai_case, event)
    assert run["status"] == "succeeded"
    assert len(calls) == 2


def test_ocr_failure_releases_model_reserve_and_same_key_never_resends(ai_case):
    provider, calls, _, client = provider_case(fail_ocr=True)
    ai_case.intelligence.provider = provider
    event = create_event(ai_case)
    with client:
        first = extract(ai_case, event)
        again = extract(ai_case, event)
    assert first["status"] == again["status"] == "failed"
    assert first["id"] == again["id"]
    assert len(calls) == 1
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        assert conn.execute("SELECT state FROM model_costs").fetchone()[0] == "released"


def test_queue_worker_uses_same_independent_evidence_boundary(ai_case):
    from app.jobs.requests import BatchItem, BatchRequest
    from app.jobs.service import QueueService
    from app.jobs.worker import QueueWorker

    provider, calls, _, client = provider_case()
    ai_case.intelligence.provider = provider
    event = create_event(ai_case, amount="10.60")
    queue = QueueService(ai_case.db, ai_case.intelligence)
    worker = QueueWorker(queue, worker_id="synthetic-private-ocr-worker")
    batch = queue.enqueue(
        ai_case.actors["employee"],
        BatchRequest(
            company_id=event["company_id"],
            idempotency_key="paddle-queue-once",
            items=[
                BatchItem(
                    event_id=event["id"],
                    attachment_id=event["attachments"][0]["id"],
                    expected_version=event["version"],
                )
            ],
        ),
    )
    with client:
        assert worker.run_once()
        assert not worker.run_once()
    job = queue.get_batch(ai_case.actors["employee"], batch["id"])["jobs"][0]
    assert job["status"] == "succeeded"
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        run = conn.execute(
            "SELECT * FROM ai_runs WHERE event_id=? AND kind='ocr'", (event["id"],)
        ).fetchone()
        assert json.loads(run["result_json"])["ocr_evidence"]["company_id"] == event["company_id"]
        assert conn.execute("SELECT count(*) FROM model_costs").fetchone()[0] == 1
    assert len(calls) == 2


def test_pause_during_ocr_stops_field_model_and_publication(ai_case):
    from app.accounting.domain import AccountingError

    def pause():
        ai_case.intelligence.company_settings(ai_case.manager, ai_case.company["id"], enabled=False)

    provider, calls, _, client = provider_case(ocr_hook=pause)
    ai_case.intelligence.provider = provider
    event = create_event(ai_case)
    with client, pytest.raises(AccountingError):
        extract(ai_case, event)
    assert len(calls) == 1
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        run = conn.execute(
            "SELECT status,result_json FROM ai_runs WHERE event_id=?", (event["id"],)
        ).fetchone()
        assert run["status"] == "stale" and run["result_json"] is None
        assert conn.execute("SELECT state FROM model_costs").fetchone()[0] == "released"


def test_independent_run_method_does_not_claim_vision_or_verification():
    from app.intelligence.ocr import prompt_version
    from app.intelligence.structured import extraction_metadata

    metadata = extraction_metadata({"prompt_version": prompt_version(settings())})
    assert metadata["extraction_method"] == "paddleocr"
    assert metadata["verification_status"] == "not_verified"


def test_legacy_unbound_intake_proof_does_not_break_manual_submission(ai_case, monkeypatch):
    from app.intake import service as intake_module
    from app.intake.models import IntakeRecognize
    from test_stage12_intake import intake, create_intake, confirmed

    provider, _, _, client = provider_case()
    ai_case.provider = ai_case.intelligence.provider = provider
    service = intake(ai_case)
    draft = create_intake(ai_case, service)
    draft = service.add_attachment(
        ai_case.actors["employee"],
        draft["id"],
        draft["version"],
        ai_case.storage.put(
            "synthetic.png", "image/png", base64.b64decode(page().image_data_url.split(",", 1)[1])
        ),
    )
    encode = intake_module.encode

    def historical_writer(value):
        # Simulate a previous release's terminal payload at its legal write
        # boundary; never UPDATE terminal evidence or disable DB constraints.
        if isinstance(value, dict) and "ocr_evidence" in value:
            value = deepcopy(value)
            del value["ocr_evidence"]["company_id"]
            value["ocr_evidence"]["schema_version"] = "independent-ocr-v1"
        return encode(value)

    with monkeypatch.context() as patch, client:
        patch.setattr(intake_module, "encode", historical_writer)
        run = service.recognize(
            ai_case.actors["employee"],
            draft["id"],
            draft["attachments"][0]["id"],
            IntakeRecognize(expected_version=draft["version"], idempotency_key="legacy-intake-ocr"),
        )
    assert run["result"]["ocr_evidence"]["schema_version"] == "independent-ocr-v1"
    submitted = service.complete(ai_case.actors["employee"], draft["id"], confirmed(draft))
    assert submitted["event"]["status"] == "submitted"
    with ai_case.db.transaction(actor=ai_case.manager) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM ai_runs WHERE event_id=?", (submitted["event"]["id"],)
            ).fetchone()[0]
            == 0
        )


def test_same_original_in_another_company_requires_its_own_recognition(ai_case):
    from app.accounting.domain import AccountingError
    from app.business.models import BusinessEventCreate

    provider, calls, _, client = provider_case()
    ai_case.intelligence.provider = provider
    first = create_event(ai_case, amount="10.60")
    outsider = ai_case.actors["outsider"]
    other = ai_case.business.create(
        outsider,
        BusinessEventCreate(
            company_id=ai_case.other_company["id"],
            template_id="reimbursement",
            payload=first["payload"],
        ),
    )
    original = base64.b64decode(page().image_data_url.split(",", 1)[1])
    other = ai_case.business.add_attachment(
        outsider,
        other["id"],
        other["version"],
        ai_case.storage.put("same-original.png", "image/png", original),
    )
    ai_case.intelligence.company_settings(ai_case.manager, other["company_id"], True)
    with client:
        a = extract(ai_case, first)
        b = extract(ai_case, other, actor="outsider")
        with pytest.raises(AccountingError) as denied:
            extract(ai_case, first, actor="outsider", key="foreign-ocr-must-not-send")
    assert denied.value.status in {403, 404}
    assert a["result"]["ocr_evidence"]["company_id"] == first["company_id"]
    assert b["result"]["ocr_evidence"]["company_id"] == other["company_id"]
    assert first["id"] not in json.dumps(b["result"])
    assert len(calls) == 4 and not b["cache_hit"]


def test_independent_ocr_does_not_clear_business_conflict_or_approve_accounting(ai_case):
    from app.accounting.domain import AccountingError
    from app.accounting.models import ExportRequest
    from test_intelligence_workflow import handoff, draft

    provider, _, _, client = provider_case()
    ai_case.intelligence.provider = provider
    event = create_event(ai_case, amount="10.59")
    with client:
        run = extract(ai_case, event)
    assert run["status"] == "succeeded"
    findings = ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])["findings"]
    assert any(
        item["code"] == "document_business_amount" and not item["resolved"] for item in findings
    )
    receipt = handoff(ai_case, event, verify_original=False)
    current = draft(ai_case, receipt["draft_id"])
    assert current["status"] != "approved"
    with pytest.raises(AccountingError):
        ai_case.ledger.export(
            ai_case.manager,
            ai_case.book["id"],
            ExportRequest(draft_ids=[current["id"]], idempotency_key="no-human-review-no-export"),
        )
