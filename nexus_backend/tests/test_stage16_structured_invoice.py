"""Real format shape, synthetic business values; no paid supplier or real invoice."""
from pathlib import Path
import sqlite3

import pytest

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventCreate
from app.business.storage import validate_attachment
from app.intelligence.cache import config_key
from app.intelligence.checks import extraction_checks
from app.intelligence.provider import AISettings
from app.intelligence.requests import OCRRequest
from app.intelligence.structured import parse_invoice_xml, safe_xml, StructuredError
from app.intake.models import IntakeRecognize
from app.jobs.requests import BatchItem, BatchRequest
from test_intelligence_workflow import ai_case, extract, handoff
from test_queue_workflow import queue_case
from test_stage12_intake import intake, create_intake, confirmed, attach_intake
from test_stage7_proposal_queue import proposal_case


def xml_bytes():
    return (Path(__file__).parent / "fixtures/stage16_SYNTHETIC_einvoice_0_3.xml").read_bytes()


def xml_event(c, data=None, actor="employee", company=None):
    who = c.actors[actor]
    event = c.business.create(who, BusinessEventCreate(
        company_id=(company or c.company)["id"], template_id="procurement_expense",
        payload={"occurred_on": "2026-10-03", "description": "合成 XML 办公采购",
                 "counterparty": "耗材商店", "amount": "0.30", "direction": "expense",
                 "payment_status": "unpaid", "category": "office"},
    ))
    metadata = c.storage.put("SYNTHETIC_数电票.xml", "text/xml", data or xml_bytes())
    return c.business.add_attachment(who, event["id"], event["version"], metadata)


def test_xml_known_shape_uses_exact_money_and_anchored_evidence():
    output = parse_invoice_xml(xml_bytes())
    document = output.result.data.documents[0]
    assert document.gross_amount == "0.30" and document.tax_amount == "0.00"
    assert document.seller_tax_id == "913101TESTSELLER"
    assert document.buyer_tax_id == "913101TESTBUYER" and document.issued_on == "2026-10-03"
    assert output.result.usage is None and output.result.provider == "local-structured-parser"
    assert extraction_checks([document.model_dump()], [1], {1: output.pages[0].native_text}) == []
    assert validate_attachment("原件.xml", "text/xml; charset=utf-8", xml_bytes()) == ("原件.xml", "application/xml")


@pytest.mark.parametrize("field,other", [("gross_amount", "net_amount"), ("net_amount", "gross_amount"), ("tax_amount", "net_amount")])
def test_xml_paths_cannot_qualify_another_money_column(field, other):
    output = parse_invoice_xml(xml_bytes())
    document = output.result.data.documents[0].model_dump()
    quote = next(item["quote"] for item in document["evidence"] if item["field"] == other)
    next(item for item in document["evidence"] if item["field"] == field)["quote"] = quote
    codes = {item["code"] for item in extraction_checks([document], [1], {1: output.pages[0].native_text})}
    assert "document_1_evidence_" + field in codes


@pytest.mark.parametrize("replacement", ["-0.30", "1e2", "0.301", "NaN", "9999999999999.00"])
def test_xml_never_rounds_or_sanitizes_invalid_amounts(replacement):
    raw = xml_bytes().replace(b"<TotalTax-includedAmount>0.30", ("<TotalTax-includedAmount>" + replacement).encode())
    with pytest.raises(StructuredError, match="金额"):
        parse_invoice_xml(raw)


@pytest.mark.parametrize("raw", [
    b'<!DOCTYPE EInvoice [<!ENTITY x "foo">]><EInvoice>&x;</EInvoice>',
    b'<!DOCTYPE EInvoice [<!ENTITY x SYSTEM "file:///private/secret">]><EInvoice>&x;</EInvoice>',
    b'<EInvoice xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="https://supplier.example.invalid" /></EInvoice>',
    b'<EInvoice>' + b'<x>' * 33 + b'</x>' * 33 + b'</EInvoice>',
    b'<x>' + b'a' * 1_048_576 + b'</x>',
], ids=["entity", "external-entity", "xinclude", "depth", "size"])
def test_xml_upload_rejects_entities_external_include_depth_and_size(raw):
    with pytest.raises(AccountingError):
        validate_attachment("恶意.xml", "application/xml", raw)


def test_duplicate_xml_scalar_red_invoice_unknown_version_and_date_require_manual():
    for raw, code in [
        (xml_bytes().replace(b"<TotalTaxAm>0.00</TotalTaxAm>", b"<TotalTaxAm>0.00</TotalTaxAm><TotalTaxAm>0.01</TotalTaxAm>"), "structured_ambiguous"),
        (xml_bytes().replace(b"<LabelCode>Y", b"<LabelCode>N"), "structured_red_invoice"),
        (xml_bytes().replace(b"<Version>0.3", b"<Version>99"), "structured_unsupported"),
        (xml_bytes().replace(b"2026-10-03 10:20:30", b"2026-02-30 10:20:30"), "structured_invalid"),
    ]:
        with pytest.raises(StructuredError) as error:
            parse_invoice_xml(raw)
        assert error.value.code == code


def test_xml_local_processing_no_model_credentials_charge_or_fact_mutation(ai_case):
    c = ai_case
    c.provider.settings = AISettings()
    event = xml_event(c)
    run = extract(c, event)
    assert run["status"] == "succeeded" and run["extraction_method"] == "structured_xml"
    assert run["verification_status"] == "not_verified" and run["provider"] is None and run["usage"] == {}
    # Source provenance is retained by the server while supplier diagnostics
    # are hidden from the employee-facing response.
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert conn.execute("SELECT provider FROM ai_runs WHERE id=?", (run["id"],)).fetchone()[0] == "local-structured-parser"
    assert extract(c, event)["id"] == run["id"] and c.provider.extract_calls == 0
    assert c.business.get_event(c.actors["employee"], event["id"])["payload"] == event["payload"]
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_costs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM document_extractions").fetchone()[0] == 1
    detail = c.intelligence.detail(c.actors["employee"], event["id"])
    assert any(item["code"] == "structured_verification_pending" for item in detail["findings"])
    with pytest.raises(AccountingError):
        extract(c, event, actor="outsider")
    c.intelligence.company_settings(c.manager, c.company["id"], enabled=False)
    with pytest.raises(AccountingError):
        extract(c, event, key="after-disable")


def test_xml_cache_scoped_to_company_and_duplicates_still_flagged(ai_case):
    c = ai_case
    first, second = xml_event(c), xml_event(c)
    extract(c, first)
    result = extract(c, second, key="other-event")
    assert result["cache_hit"] == 1
    assert any(item["code"] == "duplicate_invoice_other_event" for item in c.intelligence.detail(c.actors["employee"], second["id"])["findings"])
    c.intelligence.company_settings(c.manager, c.other_company["id"], enabled=True)
    other = xml_event(c, actor="outsider", company=c.other_company)
    assert extract(c, other, actor="outsider", key="other-company")["cache_hit"] == 0
    assert not any(item["code"] == "duplicate_invoice_other_event" for item in c.intelligence.detail(c.actors["outsider"], other["id"])["findings"])
    assert config_key(AISettings(), "application/xml") == config_key(c.provider.settings, "application/xml")


def test_invalid_tax_sum_is_saved_as_blocking_and_never_cached(ai_case):
    c = ai_case
    event = xml_event(c, xml_bytes().replace(b"<TotalTaxAm>0.00", b"<TotalTaxAm>0.01"))
    assert extract(c, event)["status"] == "succeeded"
    assert any(item["code"] == "document_1_tax_sum" and item["severity"] == "blocking" for item in c.intelligence.detail(c.actors["employee"], event["id"])["findings"])
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM document_extractions").fetchone()[0] == 0


def test_unknown_xml_keeps_original_and_failed_run_without_model_call(ai_case):
    c = ai_case
    event = xml_event(c, b"<OtherInvoice><Amount>0.30</Amount></OtherInvoice>")
    run = extract(c, event)
    assert run["status"] == "failed" and run["error_code"] == "structured_unsupported"
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        metadata = conn.execute("SELECT * FROM business_attachments WHERE id=?", (event["attachments"][0]["id"],)).fetchone()
    assert c.storage.read(metadata) == b"<OtherInvoice><Amount>0.30</Amount></OtherInvoice>"
    assert c.provider.extract_calls == 0


def test_xml_employee_intake_transfers_evidence_without_model_or_second_charge(ai_case):
    c = ai_case
    c.provider.settings = AISettings()
    service = intake(c)
    draft = create_intake(c, service)
    draft = service.add_attachment(c.actors["employee"], draft["id"], draft["version"], c.storage.put("数电票.xml", "application/xml", xml_bytes()))
    result = service.recognize(c.actors["employee"], draft["id"], draft["attachments"][0]["id"], IntakeRecognize(expected_version=draft["version"], idempotency_key="xml-intake"))
    assert result["status"] == "succeeded" and result["extraction_method"] == "structured_xml"
    with pytest.raises(AccountingError):
        service.download(c.actors["peer"], draft["attachments"][0]["id"])
    event = service.complete(c.actors["employee"], draft["id"], confirmed(draft))["event"]
    run = c.intelligence.detail(c.actors["employee"], event["id"])["runs"][0]
    assert run["provider"] is None and run["extraction_method"] == "structured_xml" and run["verification_status"] == "not_verified"
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert conn.execute("SELECT provider FROM ai_runs WHERE id=?", (run["id"],)).fetchone()[0] == "local-structured-parser"
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_costs").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_xml_durable_batch_restarts_and_finishes_without_provider(queue_case):
    c = queue_case
    c.provider.settings = AISettings()
    event = xml_event(c)
    request = BatchRequest(company_id=c.company["id"], items=[BatchItem(event_id=event["id"], attachment_id=event["attachments"][0]["id"], expected_version=event["version"])], idempotency_key="xml-batch")
    batch = c.queue.enqueue(c.actors["employee"], request)
    assert c.queue.enqueue(c.actors["employee"], request)["id"] == batch["id"]
    from app.jobs.service import QueueService
    from app.jobs.worker import QueueWorker
    resumed = QueueService(c.db, c.intelligence, clock=c.clock)
    assert QueueWorker(resumed, worker_id="restarted-xml-worker").run_once()
    assert c.queue.get_batch(c.actors["employee"], batch["id"])["progress"]["succeeded"] == 1
    assert c.provider.extract_calls == 0


def test_xml_sqlite_upgrade_preserves_existing_originals_runs_and_foreign_keys(tmp_path, monkeypatch):
    from app.accounting import database
    from app.business.storage import AttachmentStore
    from test_business_api import attachment_bytes

    original = database.migration_files
    monkeypatch.setattr(database, "migration_files", lambda dialect: [path for path in original(dialect) if int(path.name.split("_")[0]) < 58])
    # Build the historical 057 contract directly. Modern directory/ACL code
    # requires newer admin columns and must never run against an old schema.
    legacy = database.Database(tmp_path / "pre-xml.db")
    storage = AttachmentStore(tmp_path / "pre-xml-originals")
    attachment = storage.put("synthetic-original.png", "image/png", attachment_bytes())
    stamp = "2026-10-14T00:00:00+00:00"
    with legacy.transaction(write=True) as conn:
        def insert(table, **fields):
            conn.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", tuple(fields.values()))
        insert("users", id="legacy-user", name="合成历史员工")
        insert("firms", id="legacy-firm", name="合成历史事务所", created_by="legacy-user")
        insert("memberships", firm_id="legacy-firm", user_id="legacy-user", role="owner")
        insert("companies", id="legacy-company", name="合成历史企业")
        insert("firm_clients", firm_id="legacy-firm", company_id="legacy-company", status="active")
        insert("departments", id="legacy-department", company_id="legacy-company", name="合成历史部门")
        insert("company_memberships", company_id="legacy-company", user_id="legacy-user", role="employee", department_id="legacy-department")
        scope = {"firm_id": "legacy-firm", "company_id": "legacy-company"}
        insert("business_events", id="legacy-event", **scope, department_id="legacy-department", applicant_id="legacy-user", template_id="reimbursement", payload="{}", created_at=stamp, updated_at=stamp)
        insert("intake_drafts", id="legacy-intake", **scope, department_id="legacy-department", created_by="legacy-user", template_id="reimbursement", fields_json="{}", create_key="legacy-create", create_fingerprint="a" * 64, created_at=stamp, updated_at=stamp)
        original_fields = {key: attachment[key] for key in ("id", "storage_key", "filename", "media_type", "sha256", "size_bytes")}
        insert("intake_attachments", intake_id="legacy-intake", **scope, **original_fields, created_by="legacy-user", created_at=stamp)
        insert("business_attachments", event_id="legacy-event", **scope, **original_fields, created_by="legacy-user", created_at=stamp)
        insert("intake_runs", id="legacy-run", intake_id="legacy-intake", attachment_id=attachment["id"], **scope, created_by="legacy-user", input_fingerprint="b" * 64, idempotency_key="pre-xml-upgrade", status="succeeded", result_json='{"documents":[]}', provider="synthetic-provider", model="synthetic-model", prompt_version="synthetic-prompt", created_at=stamp, finished_at=stamp)
        conn.execute("UPDATE intake_drafts SET status='completed',version=2,event_id='legacy-event',completion_key='legacy-complete',completion_fingerprint=? WHERE id='legacy-intake'", ("c" * 64,))
    legacy.close()
    monkeypatch.setattr(database, "migration_files", original)
    upgraded = database.Database(legacy.path)
    with upgraded.transaction() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT result_json FROM intake_runs WHERE id='legacy-run'").fetchone()[0]
        before = conn.execute("SELECT * FROM intake_attachments WHERE id=?", (attachment["id"],)).fetchone()
        transferred = conn.execute("SELECT * FROM business_attachments WHERE event_id='legacy-event'").fetchone()
        assert before["sha256"] == transferred["sha256"] == attachment["sha256"]
        assert before["storage_key"] == transferred["storage_key"] and storage.read(before)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with upgraded.transaction(write=True) as conn:
            conn.execute("UPDATE intake_attachments SET media_type='application/xml' WHERE id=?", (before["id"],))


def test_xml_automatic_template_pipeline_stops_at_independent_accountant_review(proposal_case):
    from app.accounting.catalog_models import TemplateDefinition
    from app.accounting.models import ApprovalRequest, ExportRequest
    c = proposal_case
    c.provider.settings = AISettings()
    c.catalog.save_template(c.manager, c.book["id"], TemplateDefinition(
        name="XML 办公费用固定模板", business_type="procurement_expense", direction="expense",
        category="office", payment_status="unpaid", lines=[
            {"account_code": "6602.02", "side": "debit", "amount_field": "gross"},
            {"account_code": "2202", "side": "credit", "amount_field": "gross"},
        ],
    ))
    result = handoff(c, xml_event(c))
    assert c.worker.run_once() and c.worker.run_once()
    assert c.queue.proposals.get(c.actors["accountant"], result["automatic_job"]["id"])["status"] == "succeeded"
    value = c.ledger.get_draft(c.actors["accountant"], result["book_id"], result["draft_id"])
    assert value["status"] == "draft" and c.provider.extract_calls == c.provider.proposal_calls == 0
    request = ExportRequest(draft_ids=[value["id"]], idempotency_key="xml-after-independent-review")
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors["accountant"], result["book_id"], request)
    with pytest.raises(AccountingError):
        c.ledger.approve(c.actors["finance"], result["book_id"], value["id"], ApprovalRequest(expected_version=value["version"]))
    c.ledger.approve(c.actors["accountant"], result["book_id"], value["id"], ApprovalRequest(expected_version=value["version"]))
    assert c.ledger.export(c.actors["accountant"], result["book_id"], request)["count"] == 1
