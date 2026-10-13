"""Native proof-forgery regression tests on the dedicated PostgreSQL fixture."""
import json
from unittest.mock import patch
from uuid import uuid4
import pytest
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, SourceRow
from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountUpdate
from app.delivery.service import DeliveryService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


def uncaptured_export(c, label="A", description=None):
    external = uuid4().hex
    c.service.import_sources(c.reviewer, c.book, ImportRequest(source_name="合成原生回执防伪", rows=[SourceRow(external_id=external, date="2026-10-03", description=description or "办公费用 " + label, counterparty="合成供应商 " + label, amount="23.01", direction="expense", evidence_ref="归档证据 " + label)]))
    c.service.suggestions(c.reviewer, c.book)
    with c.db.transaction(actor=c.reviewer) as conn:
        draft_id = conn.execute("SELECT d.id FROM drafts d JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id WHERE s.external_id=? AND s.book_id=?", (external, c.book)).fetchone()[0]
    draft = c.service.get_draft(c.reviewer, c.book, draft_id)
    c.service.approve(c.reviewer, c.book, draft_id, ApprovalRequest(expected_version=draft["version"]))
    with patch.object(DeliveryService, "capture_export_in_transaction", return_value=None):
        result = c.service.export(c.reviewer, c.book, ExportRequest(draft_ids=[draft_id], idempotency_key=uuid4().hex))
    return result, draft_id


def payload(c, export_id, draft_id, current_catalog=False):
    delivery = DeliveryService(c.db)
    with c.db.transaction(actor=c.reviewer) as conn:
        book = c.service.access.book(conn, c.reviewer, c.book)
        draft = conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (draft_id, c.book)).fetchone()
        source = conn.execute("SELECT * FROM sources WHERE id=? AND book_id=?", (draft["source_id"], c.book)).fetchone()
        catalog = [dict(row) for row in conn.execute("SELECT code,name,active,version,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code", (c.book,))] if current_catalog else None
        item = {"draft_id": draft_id, "source_id": source["id"], "approved_version": draft["version"] - 1, "exported_version": draft["version"], "date": source["date"], "description": draft["description"], "amount": "23.01", "currency": "CNY", "source_fingerprint": source["fingerprint"], "lines": delivery._lines(conn, c.book, draft_id)}
        return {"version": 1, "export_id": export_id, "book_id": c.book, "company_id": book["company_id"], "period": book["period"], "currency": "CNY", "catalog": catalog, "items": [item]}


@pytest.mark.parametrize("restore_without_audit", [False, True])
def test_native_forged_export_audit_cannot_replace_actual_csv_journal(business_case, restore_without_audit):
    c = business_case
    first, first_draft = uncaptured_export(c, "A")
    _, other_draft = uncaptured_export(c, "B")
    if restore_without_audit:
        # A trusted historical restore may have CSV but no audit. Runtime
        # fabricates its only audit: uniqueness alone must not prove membership.
        restored_id = uuid4().hex
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("INSERT INTO accounting.exports(id,book_id,idempotency_key,request_fingerprint,csv,count,filename,created_at,created_by) VALUES(%s,%s,%s,%s,%s,1,'合成历史恢复.csv','2026-10-01',%s)", (restored_id, c.book, uuid4().hex, "a" * 64, first["csv"], c.reviewer.user_id))
        first = {**first, "id": restored_id}
    forged = payload(c, first["id"], other_draft)
    with c.db.transaction(write=True, actor=c.reviewer) as conn:
        conn.execute("INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,'draft.export',?,?,?)", (c.reviewer.firm_id, c.book, c.reviewer.user_id, first["id"], json.dumps({"draft_ids": [other_draft], "count": 1}), "2026-10-12"))
    with pytest.raises(c.psycopg.errors.RaiseException) as failure:
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("SELECT accounting.capture_delivery_manifest(?,?,?)", (first["id"], json.dumps(forged, ensure_ascii=False), "legacy_audit"))
    if restore_without_audit:
        assert "actual original export CSV" in str(failure.value)
    with c.db.transaction(actor=c.reviewer) as conn:
        assert not conn.execute("SELECT 1 FROM delivery_manifests WHERE export_id=?", (first["id"],)).fetchone()
    assert DeliveryService(c.db).backfill_manifest(c.reviewer, c.book, first["id"])["status"] == "pending_legacy"
    assert first_draft != other_draft


def test_native_later_transaction_cannot_recapture_modified_catalog_as_current_export(business_case):
    c = business_case
    export, draft_id = uncaptured_export(c)
    catalog = CatalogService(c.db)
    with c.db.transaction(actor=c.reviewer) as conn:
        version = conn.execute("SELECT version FROM accounts WHERE book_id=? AND code='1002'", (c.book,)).fetchone()[0]
    catalog.update_account(c.reviewer, c.book, "1002", AccountUpdate(code="1002", name="后续才修改的银行科目", expected_version=version))
    proposed = payload(c, export["id"], draft_id, current_catalog=True)
    with pytest.raises(c.psycopg.errors.RaiseException):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("SELECT accounting.capture_delivery_manifest(?,?,?)", (export["id"], json.dumps(proposed, ensure_ascii=False), "current_export"))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(actor=c.reviewer) as conn:
            conn.execute("SELECT * FROM delivery_export_proofs")
    manifest = DeliveryService(c.db).backfill_manifest(c.reviewer, c.book, export["id"])
    assert manifest["origin"] == "legacy_audit" and manifest["catalog_fingerprint"] is None


@pytest.mark.parametrize("content,expected", [('a,b\r\n"x,y","引号""和换行\n文本"\r\n', [["a", "b"], ["x,y", '引号"和换行\n文本']]), ('a,b\n1,', [["a", "b"], ["1", ""]]), ('a,b\n"bad', None), ('a,b\n"x"garbage,z', None)])
def test_native_csv_parser_handles_rfc4180_and_rejects_skipped_quote_fragments(business_case, content, expected):
    with business_case.db.transaction(actor=business_case.reviewer) as conn:
        assert conn.execute("SELECT accounting.delivery_csv_rows(?)", (content,)).fetchone()[0] == expected


def test_legacy_csv_with_quoted_unicode_multiline_is_proved_from_original_rows(business_case):
    c = business_case
    export, _ = uncaptured_export(c, description='办公,"费用"\n第二行')
    manifest = DeliveryService(c.db).backfill_manifest(c.reviewer, c.book, export["id"])
    assert manifest["status"] == "captured" and manifest["origin"] == "legacy_audit"
