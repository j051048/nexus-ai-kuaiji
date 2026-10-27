"""Current saved Paddle proof is a format check, never accounting approval."""

from copy import deepcopy
from dataclasses import replace

import pytest

from app.intelligence import ocr
from test_intelligence_workflow import ai_case, create_event, extract, invoice_document
from test_paddle_evidence_restoration import SOURCE, page, provider_case


def test_current_paddle_result_and_cache_are_not_misclassified_as_legacy(ai_case):
    provider, calls, value, client = provider_case()
    value.clear()
    value.update(invoice_document())
    ai_case.intelligence.provider = provider
    event = create_event(ai_case)
    with client:
        first = extract(ai_case, event)
        detail = ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])
        assert first["status"] == "succeeded"
        assert detail["findings"] == []
        cached = extract(ai_case, event, key="saved-current-paddle-cache")
    assert cached["cache_hit"] == 1 and len(calls) == 2
    assert ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])["findings"] == []


@pytest.fixture
def saved_sample():
    provider, _, _, client = provider_case()
    with client:
        result = provider.extract_invoice([page()], source_binding=SOURCE)
    return result.data.model_dump(), result.prompt_version


def test_saved_paddle_prompt_uses_evidence_config_not_new_global_config(saved_sample):
    payload, prompt = saved_sample
    assert ocr.saved_evidence_current(payload, prompt, SOURCE)
    from test_paddle_evidence_restoration import settings

    changed = replace(settings(), paddle_model="future-model", paddle_min_score=0.91)
    assert ocr.prompt_version(changed) != prompt
    assert ocr.saved_evidence_current(payload, prompt, SOURCE)


def test_native_pdf_proof_is_current_without_recognized_blocks():
    provider, calls, value, client = provider_case()
    native = page(value["pages"][0]["raw_text"])
    with client:
        result = provider.extract_invoice([native], source_binding=SOURCE)
    assert len(calls) == 1
    assert result.data.ocr_evidence.native_pages == 1
    assert ocr.saved_evidence_current(result.data.model_dump(), result.prompt_version, SOURCE)


def test_integral_minimum_score_retains_exact_saved_version():
    provider, _, _, client = provider_case()
    provider.settings = replace(provider.settings, paddle_min_score=1)
    with client:
        result = provider.extract_invoice([page()], source_binding=SOURCE)
    assert result.data.ocr_evidence.min_score == 1.0
    assert ocr.saved_evidence_current(result.data.model_dump(), result.prompt_version, SOURCE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "independent-ocr-v1"),
        ("engine", "untrusted-engine"),
        ("preprocessing_version", "unknown-preprocessor"),
        ("firm_id", "other-firm"),
        ("company_id", "other-company"),
        ("attachment_sha256", "b" * 64),
        ("model", "other-model"),
        ("min_score", 0.9),
        ("recognized_pages", -1),
        ("native_pages", 1),
        ("elapsed_ms", -1),
    ],
)
def test_saved_paddle_requires_exact_provenance_and_config(saved_sample, field, value):
    payload, prompt = saved_sample
    payload["ocr_evidence"][field] = value
    assert not ocr.saved_evidence_current(payload, prompt, SOURCE)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(pages=[]),
        lambda e: e["pages"].append(deepcopy(e["pages"][0])),
        lambda e: e["pages"][0].update(image_sha256="not-a-hash"),
        lambda e: e["pages"][0]["blocks"].append(deepcopy(e["pages"][0]["blocks"][0])),
        lambda e: e["pages"][0]["blocks"][0].update(polygon=[[999, 0]] * 4),
        lambda e: e["bindings"][0].update(block_ids=["foreign-page-block"]),
        lambda e: e["bindings"][0].update(document_index=1),
        lambda e: e["bindings"][0].update(status="unmatched"),
        lambda e: e.update(bindings=[]),
    ],
)
def test_saved_paddle_requires_bound_pages_blocks_and_fields(saved_sample, mutate):
    payload, prompt = saved_sample
    mutate(payload["ocr_evidence"])
    assert not ocr.saved_evidence_current(payload, prompt, SOURCE)


@pytest.mark.parametrize(
    "prompt",
    [
        "independent-invoice-extraction-facts-v4:anything",
        "independent-invoice-extraction-facts-v3:406a850bd360",
        "invoice-extraction-facts-v3",
    ],
)
def test_prefix_or_other_prompt_does_not_qualify_paddle_proof(saved_sample, prompt):
    payload, _ = saved_sample
    assert not ocr.saved_evidence_current(payload, prompt, SOURCE)


def test_missing_independent_evidence_is_not_an_implicit_success(saved_sample):
    payload, prompt = saved_sample
    del payload["ocr_evidence"]
    assert not ocr.saved_evidence_current(payload, prompt, SOURCE)


def test_recognized_current_format_does_not_hide_low_score_findings(ai_case):
    provider, _, value, client = provider_case(score=0.3)
    value.clear()
    value.update(invoice_document())
    ai_case.intelligence.provider = provider
    event = create_event(ai_case)
    with client:
        extract(ai_case, event)
    findings = ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])["findings"]
    assert not any(issue["code"].startswith("fact_validation_upgrade_") for issue in findings)
    assert any(
        "_ocr_" in issue["code"] and issue["severity"] == "blocking" and not issue["resolved"]
        for issue in findings
    )
