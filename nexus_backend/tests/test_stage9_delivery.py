from period_fixtures import restore_period_state
"""Synthetic manual receipts only; no financial software or model is contacted."""
import json
import csv
import io
import hashlib
import sqlite3
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, SourceRow, DraftUpdate
from app.accounting.seed import LOCAL_ACTOR
from app.delivery.models import ReceiptCreate, ReceiptImport, DeliveryNote
from app.delivery.service import DeliveryService
from test_catalog import case, make_draft
from test_business import business


def exported(case, legacy=False):
    ledger, _, book = case
    draft = make_draft(case)
    ledger.approve(LOCAL_ACTOR, book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    approved = ledger.get_draft(LOCAL_ACTOR, book, draft["id"])
    delivery = DeliveryService(ledger.db)
    if legacy:
        with patch.object(DeliveryService, "capture_export_in_transaction", return_value=None):
            receipt = ledger.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    else:
        receipt = ledger.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
        with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            delivery.capture_export_in_transaction(conn, LOCAL_ACTOR, ledger.access.book(conn, LOCAL_ACTOR, book), receipt["id"], [approved])
    return ledger, delivery, book, receipt, approved


def facts(delivery, book, export_id):
    body = json.loads(delivery.template(LOCAL_ACTOR, book, export_id))
    for item in body["items"]:
        item.update(status="accepted", external_voucher_id="客户凭证-001")
    return body


def create(delivery, book, export_id, body, key=None):
    return delivery.create_receipt(LOCAL_ACTOR, book, export_id, ReceiptCreate(**body, idempotency_key=key or uuid4().hex, reason="客户财务提供的合成回执，逐行核对"))


def test_current_manifest_freezes_original_versions_catalog_and_exact_lines(case):
    ledger, delivery, book, export, approved = exported(case)
    manifest = delivery.manifest(LOCAL_ACTOR, book, export["id"])
    assert manifest["status"] == "captured" and len(manifest["canonical_fingerprint"]) == 64 and len(manifest["catalog_fingerprint"]) == 64
    item = manifest["items"][0]
    assert item["approved_version"] == approved["version"] and item["exported_version"] == approved["version"] + 1
    assert item["amount"] == "113.00" and [line["ordinal"] for line in item["lines"]] == [0, 1]
    with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE delivery_manifests SET payload_json='{}' WHERE export_id=?", (export["id"],))
    assert delivery.manifest(LOCAL_ACTOR, book, export["id"]) == manifest


def test_receipt_data_match_never_changes_frozen_journal_or_claims_external_connection(case):
    ledger, delivery, book, export, _ = exported(case)
    result = create(delivery, book, export["id"], facts(delivery, book, export["id"]))
    assert result["summary"] == {"verified": 1, "received_unverified": 0, "discrepancy": 0}
    assert result["evidence_mode"] == "manually_supplied_receipt" and result["automatic_resend"] is False
    assert ledger.get_draft(LOCAL_ACTOR, book, result["items"][0]["draft_id"])["status"] == "exported"


@pytest.mark.parametrize("kind", ["amount", "date", "description", "account_code", "debit", "credit", "auxiliary_values", "ordinal", "book", "company", "period", "currency"])
def test_each_manifest_field_is_checked_deterministically(case, kind):
    _, delivery, book, export, _ = exported(case)
    body = facts(delivery, book, export["id"])
    item = body["items"][0]
    if kind in {"amount", "date", "description"}:
        item[kind] = {"amount": "113.01", "date": "2026-10-02", "description": "另一笔事项"}[kind]
    elif kind in {"account_code", "debit", "credit", "auxiliary_values", "ordinal"}:
        item["lines"][0][kind] = {"account_code": "6001", "debit": "112.99", "credit": "0.01", "auxiliary_values": {"project": "其他项目"}, "ordinal": 9}[kind]
    else:
        body[{"book": "book_id", "company": "company_id", "period": "period", "currency": "currency"}[kind]] = "other"
    result = create(delivery, book, export["id"], body)
    assert result["summary"]["discrepancy"] == 1 and result["summary"]["verified"] == 0


@pytest.mark.parametrize("missing", ["external_voucher_id", "amount", "description", "date", "lines", "line_aux", "book_id"])
def test_missing_receipt_facts_are_received_unverified_not_assumed_success(case, missing):
    _, delivery, book, export, _ = exported(case)
    body = facts(delivery, book, export["id"])
    if missing == "book_id":
        body.pop("book_id")
    elif missing == "line_aux":
        body["items"][0]["lines"][0].pop("auxiliary_values")
    else:
        body["items"][0].pop(missing)
    result = create(delivery, book, export["id"], body)
    assert result["summary"] == {"verified": 0, "received_unverified": 1, "discrepancy": 0}


def test_unknown_rejected_duplicate_ids_and_multiple_external_vouchers_are_preserved(case):
    _, delivery, book, export, _ = exported(case)
    body = facts(delivery, book, export["id"])
    first = create(delivery, book, export["id"], body)
    body["items"][0]["external_voucher_id"] = "客户凭证-002"
    second = create(delivery, book, export["id"], body)
    assert "multiple_external_vouchers" in second["items"][0]["issues"]
    detail = delivery.detail(LOCAL_ACTOR, book, export["id"])
    assert all(result["items"][0]["state"] == "discrepancy" for result in detail["receipts"])
    body["items"].append(deepcopy(body["items"][0]))
    result = create(delivery, book, export["id"], body)
    assert "duplicate_draft_id" in result["items"][0]["issues"] and "duplicate_external_voucher_id" in result["items"][0]["issues"]
    body["items"] = [{"draft_id": "outside-this-export", "status": "rejected"}]
    result = create(delivery, book, export["id"], body)
    assert "unknown_draft_id" in result["items"][0]["issues"] and "external_rejected" in result["items"][0]["issues"]
    assert result["items"][1]["missing_fields"] == ["receipt_item_missing"]
    assert first["id"] != second["id"]


def test_receipt_idempotency_and_append_only_storage(case):
    ledger, delivery, book, export, _ = exported(case)
    body = facts(delivery, book, export["id"])
    key = uuid4().hex
    first = create(delivery, book, export["id"], body, key)
    assert create(delivery, book, export["id"], body, key) == first
    body["items"][0]["status"] = "unknown"
    with pytest.raises(AccountingError) as error:
        create(delivery, book, export["id"], body, key)
    assert error.value.status == 409
    with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        for table in ("delivery_receipts", "delivery_receipt_items"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"DELETE FROM {table}")
    with pytest.raises(ValidationError):
        ReceiptCreate(**body, verified=True, reason="不能伪造核验状态", idempotency_key=uuid4().hex)


def test_json_csv_templates_roundtrip_keep_precise_cents_and_manual_unknown(case):
    _, delivery, book, export, _ = exported(case)
    for format in ("json", "csv"):
        content = delivery.template(LOCAL_ACTOR, book, export["id"], format)
        assert "113.00" in content
        request = ReceiptImport(format=format, content=content, reason="模板合成回执结果仍不确定", idempotency_key=uuid4().hex)
        result = delivery.import_receipt(LOCAL_ACTOR, book, export["id"], request)
        assert result["summary"]["received_unverified"] == 1
        assert "external_status_unknown" in result["items"][0]["missing_fields"]


@pytest.mark.parametrize("format,content", [("json", "{bad"), ("json", '{"items":[]}'), ("csv", 'draft_id,status,status\nx,accepted,accepted'), ("csv", 'draft_id,status\nx,accepted,extra'), ("csv", 'draft_id,status,debit\nx,accepted,1e6')])
def test_malformed_files_do_not_create_receipt(case, format, content):
    _, delivery, book, export, _ = exported(case)
    with pytest.raises(AccountingError):
        delivery.import_receipt(LOCAL_ACTOR, book, export["id"], ReceiptImport(format=format, content=content, reason="合成输入", idempotency_key=uuid4().hex))
    assert delivery.detail(LOCAL_ACTOR, book, export["id"])["receipts"] == []


def test_close_period_allows_append_receipt_and_notes_without_reopening(case):
    ledger, delivery, book, export, _ = exported(case)
    restore_period_state(ledger.db, book)
    body = facts(delivery, book, export["id"])
    body["items"][0]["status"] = "rejected"
    result = create(delivery, book, export["id"], body)
    request = DeliveryNote(receipt_id=result["id"], draft_id=body["items"][0]["draft_id"], issue_key="external_rejected", reason="已联系客户核实拒绝原因；未重发", idempotency_key=uuid4().hex)
    noted = delivery.add_note(LOCAL_ACTOR, book, request)
    assert noted["items"][0]["state"] == "discrepancy" and len(noted["items"][0]["notes"]) == 1
    assert delivery.add_note(LOCAL_ACTOR, book, request) == noted
    assert delivery.overview(LOCAL_ACTOR, book)["period_status"] == "closed"
    with pytest.raises(AccountingError):
        delivery.add_note(LOCAL_ACTOR, book, request.model_copy(update={"issue_key": "not-real", "idempotency_key": uuid4().hex}))


def test_legacy_backfill_requires_approval_export_audit_and_exact_old_csv(case):
    ledger, delivery, book, export, _ = exported(case, legacy=True)
    assert delivery.manifest(LOCAL_ACTOR, book, export["id"])["status"] == "pending_legacy"
    with pytest.raises(AccountingError):
        delivery.template(LOCAL_ACTOR, book, export["id"])
    manifest = delivery.backfill_manifest(LOCAL_ACTOR, book, export["id"])
    assert manifest["status"] == "captured" and manifest["origin"] == "legacy_audit" and manifest["catalog_fingerprint"] is None
    assert delivery.backfill_manifest(LOCAL_ACTOR, book, export["id"]) == manifest
    unknown_id = uuid4().hex
    with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute("INSERT INTO exports(id,book_id,idempotency_key,request_fingerprint,csv,count,filename,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)", (unknown_id, book, uuid4().hex, "unknown", export["csv"], 1, "旧导出.csv", "2026-10-01", LOCAL_ACTOR.user_id))
    assert delivery.backfill_manifest(LOCAL_ACTOR, book, unknown_id)["status"] == "pending_legacy"


def test_employee_other_company_and_editor_cannot_import_or_read_ungranted_receipts(business):
    c = business
    ledger, book = c.ledger, c.book["id"]
    ledger.import_sources(c.manager, book, ImportRequest(source_name="合成", rows=[SourceRow(external_id="delivery-bank", date="2026-10-03", description="办公用品", counterparty="供应商", amount="1.01", direction="expense", evidence_ref="原件已核实")]))
    ledger.suggestions(c.manager, book)
    draft = ledger.get_book(c.manager, book)["drafts"][0]
    ledger.approve(c.actors["accountant"], book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    approved = ledger.get_draft(c.actors["accountant"], book, draft["id"])
    export = ledger.export(c.actors["accountant"], book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    delivery = DeliveryService(c.db)
    with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
        delivery.capture_export_in_transaction(conn, c.actors["accountant"], ledger.access.book(conn, c.actors["accountant"], book), export["id"], [approved])
    body = json.loads(delivery.template(c.actors["accountant"], book, export["id"]))
    for role in ("employee", "peer", "other_employee"):
        with pytest.raises(AccountingError):
            delivery.overview(c.actors[role], book)
    assert delivery.overview(c.actors["finance"], book)["can_import"] is False
    with pytest.raises(AccountingError):
        delivery.create_receipt(c.actors["finance"], book, export["id"], ReceiptCreate(**body, idempotency_key=uuid4().hex, reason="不能按编辑权限导入回执"))
    with pytest.raises(AccountingError):
        delivery.detail(c.actors["accountant"], c.other_book["id"], export["id"])


def test_missing_manifest_receipt_placeholder_displays_appended_processing_note(case):
    _, delivery, book, export, approved = exported(case)
    body = facts(delivery, book, export["id"])
    body["items"] = [{"draft_id": "not-in-this-export", "status": "unknown"}]
    receipt = create(delivery, book, export["id"], body)
    request = DeliveryNote(receipt_id=receipt["id"], draft_id=approved["id"], issue_key="receipt_item_missing", reason="已联系客户索取该凭证的回执，仍须核验", idempotency_key=uuid4().hex)
    result = delivery.add_note(LOCAL_ACTOR, book, request)
    placeholder = next(item for item in result["items"] if item["draft_id"] == approved["id"])
    assert placeholder["state"] == "received_unverified"
    assert len(placeholder["notes"]) == 1 and placeholder["notes"][0]["reason"] == request.reason
    again = delivery.detail(LOCAL_ACTOR, book, export["id"])["receipts"][0]
    assert next(item for item in again["items"] if item["draft_id"] == approved["id"])["notes"] == placeholder["notes"]


@pytest.mark.parametrize("description,legacy_code", [("=danger", "-legacy"), ("  +danger", "@legacy"), ("\t=SUM(1,2)", "=legacy"), ("'=actual-leading-apostrophe", "'-actual-code"), ("=" + "x" * 499, "-" + "c" * 29)])
def test_neutral_csv_safety_encoding_decodes_only_exact_frozen_expectations(case, description, legacy_code):
    ledger, _, book = case
    # Prior charts can contain codes outside the current AccountDefinition UI
    # contract; simulate such immutable history in the local test database.
    with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute("INSERT INTO accounts(book_id,code,name,category,normal_balance) VALUES(?,?,?,'expense','debit')", (book, legacy_code, "历史客户兼容科目"))
    draft = make_draft(case)
    lines = [{**line, "account_code": legacy_code if line["account_code"] != "1002" else "1002"} for line in draft["lines"]]
    draft = ledger.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(expected_version=draft["version"], description=description, lines=lines, correction_reason="合成历史科目及CSV安全编码验证"))
    if draft["description"] != description:
        # Existing native history can retain meaningful leading whitespace;
        # the current input UI strips it, so simulate only this legacy detail.
        with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            conn.execute("UPDATE drafts SET description=?,version=version+1 WHERE id=? AND book_id=? AND status='draft'", (description, draft["id"], book))
        draft = ledger.get_draft(LOCAL_ACTOR, book, draft["id"])
    ledger.approve(LOCAL_ACTOR, book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    export = ledger.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    delivery = DeliveryService(ledger.db)
    content = delivery.template(LOCAL_ACTOR, book, export["id"], "csv")
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    rows = list(reader)
    assert any(row["description"].startswith("'") for row in rows)
    for row in rows:
        row["status"], row["external_voucher_id"] = "accepted", "客户凭证-CSV-001"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\r\n")
    writer.writeheader(); writer.writerows(rows)
    raw = "\ufeff" + output.getvalue()
    result = delivery.import_receipt(LOCAL_ACTOR, book, export["id"], ReceiptImport(format="csv", content=raw, reason="合成客户CSV明细核验", idempotency_key=uuid4().hex))
    assert result["summary"]["verified"] == 1
    assert result["raw_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        body = json.loads(conn.execute("SELECT body_json FROM delivery_receipts WHERE id=?", (result["id"],)).fetchone()[0])
    assert body["items"][0]["description"] == description
    assert body["items"][0]["lines"][0]["account_code"] == legacy_code
    # An additional apostrophe is actual changed content, not safety encoding.
    for row in rows:
        row["description"] = "'" + row["description"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\r\n")
    writer.writeheader(); writer.writerows(rows)
    request = ReceiptImport(format="csv", content=output.getvalue(), reason="额外引号不能被任意删除", idempotency_key=uuid4().hex)
    if len(description) >= 500:
        with pytest.raises(AccountingError) as error:
            delivery.import_receipt(LOCAL_ACTOR, book, export["id"], request)
        assert error.value.status == 422
    else:
        mismatch = delivery.import_receipt(LOCAL_ACTOR, book, export["id"], request)
        assert "description_mismatch" in mismatch["items"][0]["issues"]
