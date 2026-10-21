"""AK04 actual ticket/intake projections use synthetic output and current grants."""
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.intake.models import IntakeRecognize
from app.intake.router import build_intake_router
from app.intelligence.provider import ProviderError
from app.intelligence.models import ProviderUsage
from app.intelligence.router import build_intelligence_router
from test_intelligence_workflow import ai_case, create_event, extract, handoff, propose
from test_stage12_intake import attach_intake, create_intake, intake


RAW_ERROR = "upstream private endpoint=https://private.invalid secret=raw-error-token"


@pytest.mark.parametrize("entry", ["ocr", "intake"])
def test_unknown_provider_error_code_is_not_a_public_error_message(ai_case, entry):
    case = ai_case
    private_code = "provider-private-key-value"
    case.provider.failure = ProviderError(private_code, RAW_ERROR)
    if entry == "ocr":
        event = create_event(case)
        run = extract(case, event)
        detail = case.intelligence.detail(case.actors["employee"], event["id"])
        table = "ai_runs"
    else:
        service, draft, run = recognize(case)
        detail = service.get(case.actors["employee"], draft["id"])
        table = "intake_runs"
    assert private_code not in json.dumps(run)
    assert private_code not in json.dumps(detail)
    assert run["error_code"] == "worker_failure"
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT error_code FROM " + table + " WHERE id=?", (run["id"],)).fetchone()[0] == private_code


def recognize(case):
    service = intake(case)
    draft = attach_intake(case, service, create_intake(case, service))
    run = service.recognize(
        case.actors["employee"], draft["id"], draft["attachments"][0]["id"],
        IntakeRecognize(expected_version=draft["version"], idempotency_key=uuid4().hex),
    )
    return service, draft, run


def test_ticket_detail_exposes_durable_execution_without_resending(ai_case):
    case = ai_case
    event = create_event(case)
    run = extract(case, event)
    detail = case.intelligence.detail(case.actors["employee"], event["id"])
    execution = detail["runs"][0]["execution"]
    assert execution["execution_id"] == "ocr:" + run["id"]
    assert execution["status"] == "succeeded"
    assert execution["schema"]
    assert "diagnostics" not in execution
    # A blank source bitmap retains the canonical original-verification block.
    assert "document_1_original_unverified" in json.dumps(execution, ensure_ascii=False)
    assert case.intelligence.run_execution(case.actors["employee"], event["id"], run["id"]) == execution
    assert case.provider.extract_calls == 1


def test_ticket_execution_does_not_publish_raw_supplier_error_to_employee(ai_case):
    case = ai_case
    case.provider.failure = ProviderError("timeout", RAW_ERROR)
    event = create_event(case)
    run = extract(case, event)
    execution = case.intelligence.run_execution(case.actors["employee"], event["id"], run["id"])
    detail_run = case.intelligence.detail(case.actors["employee"], event["id"])["runs"][0]
    assert RAW_ERROR not in json.dumps(execution)
    assert RAW_ERROR not in json.dumps(detail_run)
    assert "diagnostics" not in execution
    assert execution["failure"] is not None
    assert execution["failure"]["outcome_unknown"] is True
    assert execution["failure"]["automatic_retry"] is False
    assert execution["handoff"] is not None
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT state FROM model_costs WHERE id=?", (run["id"],)).fetchone()[0] == "uncertain"
    assert case.provider.extract_calls == 1


def test_actual_local_xml_record_does_not_invent_a_model_call_or_zero_cost(ai_case):
    from app.intelligence.provider import AISettings
    from test_stage16_structured_invoice import xml_event

    case = ai_case
    case.provider.settings = AISettings()
    event = xml_event(case)
    run = extract(case, event)
    handoff(case, event)
    diagnostics = case.intelligence.run_execution(case.manager, event["id"], run["id"])["diagnostics"]
    assert diagnostics["deterministic_parse"] is True
    assert diagnostics["send_marked"] is False
    assert diagnostics["costs"] == []
    assert case.provider.extract_calls == 0


def test_actual_cache_record_keeps_original_call_separate(ai_case, monkeypatch):
    from dataclasses import replace
    from app.intelligence import service as module
    from test_intelligence_workflow import invoice_document

    case = ai_case
    prepare = module.prepare_document
    original = invoice_document()["pages"][0]["raw_text"]
    monkeypatch.setattr(module, "prepare_document", lambda *args, **kwargs: [
        replace(page, native_text=original) for page in prepare(*args, **kwargs)
    ])
    event = create_event(case)
    first = extract(case, event)
    cached = extract(case, event, key="ak04-cached-ocr-record")
    assert first["id"] != cached["id"]
    handoff(case, event)
    diagnostics = case.intelligence.run_execution(case.manager, event["id"], cached["id"])["diagnostics"]
    assert diagnostics["cache_hit"] is True
    assert diagnostics["send_marked"] is False
    assert diagnostics["costs"] == []
    assert case.provider.extract_calls == 1


def test_current_owner_gets_diagnostics_while_employee_and_accountant_do_not(ai_case):
    case = ai_case
    case.provider.usage = ProviderUsage(prompt_tokens=12, completion_tokens=5, total_tokens=17)
    event = create_event(case)
    run = extract(case, event)
    assert run["usage"] == {} and run["provider"] is None and run["model"] is None
    # Direct idempotent responses have the same diagnostic restriction.
    assert extract(case, event)["usage"] == {}
    handoff(case, event)
    owner = case.intelligence.detail(case.manager, event["id"])["runs"][0]
    assert owner["usage"] == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    assert owner["provider"] and owner["model"]
    assert owner["execution"]["diagnostics"]["costs"][0]["input_tokens"] == 12
    assert "charge_token" not in json.dumps(owner["execution"])
    ordinary = case.intelligence.detail(case.actors["accountant"], event["id"])["runs"][0]
    assert ordinary["usage"] == {} and ordinary["provider"] is None
    assert "diagnostics" not in ordinary["execution"]
    assert case.provider.extract_calls == 1


def test_ticket_execution_reads_current_company_authorization(ai_case):
    case = ai_case
    event = create_event(case)
    run = extract(case, event)
    with pytest.raises(AccountingError) as cross_event:
        case.intelligence.run_execution(case.actors["peer"], event["id"], run["id"])
    assert cross_event.value.status == 404
    case.directory.revoke_company_member(case.manager, case.company["id"], case.actors["employee"].user_id)
    with pytest.raises(AccountingError):
        case.intelligence.run_execution(case.actors["employee"], event["id"], run["id"])


def test_proposal_execution_requires_current_book_access(ai_case):
    case = ai_case
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    run = propose(case, receipt["draft_id"])
    with pytest.raises(AccountingError):
        case.intelligence.run_execution(case.actors["employee"], event["id"], run["id"])
    visible = case.intelligence.run_execution(case.actors["accountant"], event["id"], run["id"])
    assert visible["execution_id"] == "journal:" + run["id"]
    assert "diagnostics" not in visible
    redacted = next(item for item in case.intelligence.detail(case.actors["employee"], event["id"])["runs"] if item["id"] == run["id"])
    assert redacted["result"] is None
    assert "execution" not in redacted


def test_intake_detail_and_execution_keep_private_source_and_unknown_cost(ai_case):
    case = ai_case
    case.provider.failure = ProviderError("network", RAW_ERROR)
    service, draft, run = recognize(case)
    detail_run = service.get(case.actors["employee"], draft["id"])["runs"][0]
    execution = service.run_execution(case.actors["employee"], draft["id"], run["id"])
    assert detail_run["execution"] == execution
    assert execution["execution_id"] == "intake:" + run["id"]
    assert "diagnostics" not in execution
    assert RAW_ERROR not in json.dumps(detail_run)
    with pytest.raises(AccountingError) as private:
        service.run_execution(case.actors["finance"], draft["id"], run["id"])
    assert private.value.status == 404
    assert case.provider.extract_calls == 1
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT state FROM model_costs WHERE id=?", (run["id"],)).fetchone()[0] == "uncertain"


def test_intake_execution_binds_run_to_its_current_private_draft(ai_case):
    case = ai_case
    service, draft, run = recognize(case)
    other = create_intake(case, service)
    with pytest.raises(AccountingError) as mismatch:
        service.run_execution(case.actors["employee"], other["id"], run["id"])
    assert mismatch.value.status == 404
    case.directory.revoke_company_member(case.manager, case.company["id"], case.actors["employee"].user_id)
    with pytest.raises(AccountingError):
        service.run_execution(case.actors["employee"], draft["id"], run["id"])


def test_execution_apis_are_read_only_and_route_to_authorized_services(ai_case):
    case = ai_case
    event = create_event(case)
    run = extract(case, event)
    service, draft, intake_run = recognize(case)
    app = FastAPI()
    app.state.intelligence, app.state.intake = case.intelligence, service
    actor = lambda: case.actors["employee"]
    app.include_router(build_intelligence_router(actor))
    app.include_router(build_intake_router(actor))
    client = TestClient(app)
    paths = [
        f"/api/business/events/{event['id']}/intelligence/runs/{run['id']}/execution",
        f"/api/intake/drafts/{draft['id']}/runs/{intake_run['id']}/execution",
    ]
    for path, expected, kind in zip(paths, (run, intake_run), ("ocr", "intake")):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["execution_id"] == kind + ":" + expected["id"]
        assert client.post(path).status_code == 405
    assert case.provider.extract_calls == 2
