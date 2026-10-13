from period_fixtures import restore_period_state
"""Dedicated PostgreSQL tests; no real software or customer data is accessed."""
import json
from unittest.mock import patch
from uuid import uuid4
import pytest
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, SourceRow
from app.delivery.models import ReceiptCreate, DeliveryNote
from app.delivery.service import DeliveryService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


def prepare(c, legacy=False):
    ledger, actor = c.service, c.reviewer
    ledger.import_sources(actor, c.book, ImportRequest(source_name="合成回执测试", rows=[SourceRow(external_id=uuid4().hex, date="2026-10-03", description="办公用品", counterparty="合成供应商", amount="113.01", direction="expense", evidence_ref="核实合成原件")]))
    ledger.suggestions(actor, c.book)
    draft = ledger.get_book(actor, c.book)["drafts"][0]
    ledger.approve(actor, c.book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    approved = ledger.get_draft(actor, c.book, draft["id"])
    delivery = DeliveryService(c.db)
    if legacy:
        with patch.object(DeliveryService, "capture_export_in_transaction", return_value=None):
            export = ledger.export(actor, c.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    else:
        export = ledger.export(actor, c.book, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    return delivery, export, approved


def receipt(delivery, c, export):
    facts = json.loads(delivery.template(c.reviewer, c.book, export["id"]))
    facts["items"][0].update(status="accepted", external_voucher_id="合成客户凭证-001")
    return ReceiptCreate(**facts, reason="客户财务提供的合成明细", idempotency_key=uuid4().hex)


def test_pg_delivery_force_rls_no_worker_mutation_or_public_access(business_case):
    c = business_case
    delivery, export, _ = prepare(c)
    result = delivery.create_receipt(c.reviewer, c.book, export["id"], receipt(delivery, c, export))
    for actor in (c.employee, c.colleague, c.unassigned):
        with c.db.transaction(actor=actor) as conn:
            for table in ("delivery_manifests", "delivery_receipts", "delivery_receipt_items", "delivery_notes"):
                assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ("delivery_manifests", "delivery_receipts", "delivery_receipt_items", "delivery_notes"):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("accounting." + table,)).fetchone() == (True, True)
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ("accounting." + table,)).fetchone()[0]
            assert not conn.execute("SELECT has_table_privilege('accounting_app',%s,'UPDATE')", ("accounting." + table,)).fetchone()[0]
    assert result["summary"]["verified"] == 1


def test_pg_receipt_immutable_closed_book_append_and_editor_write_denied(business_case):
    c = business_case
    delivery, export, _ = prepare(c)
    request = receipt(delivery, c, export)
    restore_period_state(c.service.db, c.book)
    first = delivery.create_receipt(c.reviewer, c.book, export["id"], request)
    assert delivery.create_receipt(c.reviewer, c.book, export["id"], request) == first
    for statement in ("DELETE FROM delivery_receipts", "UPDATE delivery_receipts SET body_json='{}'", "UPDATE delivery_manifests SET payload_json='{}'"):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            with c.db.transaction(write=True, actor=c.reviewer) as conn:
                conn.execute(statement)
    with pytest.raises(Exception):
        delivery.create_receipt(c.finance, c.book, export["id"], request)
    assert c.service.get_draft(c.reviewer, c.book, first["items"][0]["draft_id"])["status"] == "exported"


def test_pg_manifest_capability_rejects_false_versions_lines_and_editor(business_case):
    c = business_case
    delivery, export, approved = prepare(c, legacy=True)
    with c.db.transaction(actor=c.reviewer) as conn:
        book = c.service.access.book(conn, c.reviewer, c.book)
    items = [{"draft_id": approved["id"], "source_id": approved["source_id"], "approved_version": approved["version"], "exported_version": approved["version"] + 1, "date": "2026-10-03", "description": approved["description"], "amount": "113.01", "currency": "CNY", "source_fingerprint": "forged", "lines": []}]
    payload = {"version": 1, "export_id": export["id"], "book_id": c.book, "company_id": c.company, "period": "2026-10", "currency": "CNY", "catalog": None, "items": items}
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("SELECT accounting.capture_delivery_manifest(?,?,?)", (export["id"], json.dumps(payload), "legacy_audit"))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute("SELECT accounting.capture_delivery_manifest(?,?,?)", (export["id"], json.dumps(payload), "legacy_audit"))
    result = delivery.backfill_manifest(c.reviewer, c.book, export["id"])
    assert result["status"] == "captured" and result["origin"] == "legacy_audit" and result["catalog_fingerprint"] is None


def test_pg_native_receipt_index_cannot_forge_external_identity_or_verified_state(business_case):
    c = business_case
    delivery, export, _ = prepare(c)
    request = receipt(delivery, c, export)
    result = delivery.create_receipt(c.reviewer, c.book, export["id"], request)
    with pytest.raises(c.psycopg.errors.UndefinedColumn):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("INSERT INTO delivery_receipts(verified) VALUES(true)")
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("INSERT INTO delivery_receipt_items(receipt_id,book_id,firm_id,company_id,ordinal,claimed_draft_id,external_voucher_id,raw_json) VALUES(?,?,?,?,1,?,?,?)", (result["id"], c.book, c.reviewer.firm_id, c.company, request.items[0].draft_id, "fake-ID", json.dumps(request.items[0].model_dump())))
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("INSERT INTO delivery_receipts(id,export_id,book_id,firm_id,company_id,body_json,request_fingerprint,idempotency_key,reason,import_format,raw_hash,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,'structured',?,?,?)", (uuid4().hex, export["id"], c.book, c.reviewer.firm_id, c.company, '{"items":[{"draft_id":"x","status":"accepted"}],"verified":true}', "a" * 64, uuid4().hex, "不能伪造状态", "b" * 64, c.reviewer.user_id, "2026-10-12"))


def test_pg_other_book_receipts_hidden_and_scope_fk_prevents_moving_receipts(business_case):
    c = business_case
    delivery, export, _ = prepare(c)
    with pytest.raises(Exception):
        delivery.detail(c.reviewer, c.other_book, export["id"])
    request = receipt(delivery, c, export)
    request = request.model_copy(update={"book_id": c.other_book, "company_id": c.other_company})
    result = delivery.create_receipt(c.reviewer, c.book, export["id"], request)
    assert "book_id_mismatch" in result["items"][0]["issues"] and "company_id_mismatch" in result["items"][0]["issues"]
    assert result["summary"]["verified"] == 0


def test_pg_note_preserves_difference_and_original_journal(business_case):
    c = business_case
    delivery, export, _ = prepare(c)
    request = receipt(delivery, c, export)
    request.items[0].status = "rejected"
    result = delivery.create_receipt(c.reviewer, c.book, export["id"], request)
    request = DeliveryNote(receipt_id=result["id"], draft_id=result["items"][0]["draft_id"], issue_key="external_rejected", reason="客户正在核实，不能假定已入账", idempotency_key=uuid4().hex)
    result = delivery.add_note(c.reviewer, c.book, request)
    assert result["items"][0]["state"] == "discrepancy" and len(result["items"][0]["notes"]) == 1
