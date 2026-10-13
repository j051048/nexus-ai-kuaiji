"""Scene changes preserve isolation, independent reuse and deterministic fee displays."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.intake.models import IntakeRecognize
from app.operations.pricing import Pricing, Rate
from app.operations.service import OperationsService
from test_intelligence_workflow import ai_case, create_event
from test_stage12_intake import intake, create_intake, attach_intake
from test_stage7_intelligence import identify


def independent_ocr(case):
    case.provider.settings = replace(case.provider.settings, ocr_base_url="https://ocr-only.example.test/v1",
                                     ocr_api_key="synthetic-independent-key", ocr_model="independent-ocr")


def test_actual_ocr_vendor_and_model_are_audited_and_other_scene_changes_keep_reuse(ai_case):
    c = ai_case
    independent_ocr(c)
    config = c.intelligence.config(c.manager)
    assert config["vision_ready"] and config["journal_ready"]
    assert "ocr-only.example.test" in config["provider_label"]
    assert "supplier.example.invalid" in config["provider_label"]
    assert "synthetic-independent-key" not in str(config)
    event = create_event(c)
    first = identify(c, event)
    assert first["provider"] == "ocr-only.example.test" and first["model"] == "independent-ocr"
    c.provider.settings = replace(c.provider.settings, journal_base_url="https://journal-only.example.test/v1",
                                  journal_api_key="synthetic-journal-key", chat_model="new-chat")
    second = identify(c, event)
    assert second["cache_hit"] == 1 and c.provider.extract_calls == 1
    c.provider.settings = replace(c.provider.settings, ocr_base_url="https://new-ocr.example.test/v1")
    third = identify(c, event)
    assert third["cache_hit"] == 0 and c.provider.extract_calls == 2


def test_intake_discards_response_if_ocr_service_changes_after_send(ai_case):
    c = ai_case
    independent_ocr(c)
    service = intake(c)
    draft = attach_intake(c, service, create_intake(c, service))
    request = IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex)
    c.provider.extract_hook = lambda: setattr(c.provider, "settings", replace(c.provider.settings, ocr_base_url="https://new-ocr.example.test/v1"))
    with pytest.raises(AccountingError, match="配置已改变"):
        service.recognize(c.actors["employee"], draft["id"], draft["attachments"][0]["id"], request)
    assert c.provider.extract_calls == 1
    with c.db.transaction(actor=c.manager) as conn:
        run = conn.execute("SELECT status,result_json,provider,model FROM intake_runs WHERE intake_id=?", (draft["id"],)).fetchone()
    assert run["status"] == "stale" and run["result_json"] is None
    assert run["provider"] == "ocr-only.example.test" and run["model"] == "independent-ocr"


def test_intake_ocr_can_finish_when_only_unrelated_model_changes(ai_case):
    c = ai_case
    independent_ocr(c)
    service = intake(c)
    draft = attach_intake(c, service, create_intake(c, service))
    c.provider.extract_hook = lambda: setattr(c.provider, "settings", replace(c.provider.settings, journal_model="unrelated-new-journal"))
    result = service.recognize(c.actors["employee"], draft["id"], draft["attachments"][0]["id"],
                               IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex))
    assert result["status"] == "succeeded" and c.provider.extract_calls == 1


def test_company_tariffs_display_resolved_models_without_supplier_credentials(ai_case):
    c = ai_case
    independent_ocr(c)
    operations = object.__new__(OperationsService)
    operations.intelligence = c.intelligence
    operations.meter = SimpleNamespace(pricing=Pricing(ocr=Rate("1", "2"), proposal=Rate("3", "4"), chat=Rate("5", "6")))
    result = operations._pricing_json()
    assert result["ocr"]["model"] == "independent-ocr" and result["ocr"]["configured"]
    assert result["proposal"]["model"] == "synthetic-journal"
    assert result["chat"]["input_per_million"] == "5.000000"
    assert "synthetic-independent-key" not in str(result) and "example.test" not in str(result)
