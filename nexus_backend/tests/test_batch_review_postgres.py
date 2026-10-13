"""Batch review RLS and receipts on a disposable PostgreSQL instance."""
import json

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ImportRequest, SourceRow
from app.accounting.review import ReviewService
from app.accounting.review_models import ReviewConfirmRequest, ReviewPreviewRequest
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员和受限运行测试连接")


def bank_draft(case, book_id, external_id):
    case.service.import_sources(case.owner, book_id, ImportRequest(source_name="批量复核合成测试", rows=[SourceRow(external_id=external_id, date="2026-10-01", description="办公费", counterparty="供应商", amount="100.00", direction="expense", evidence_ref="原件引用")]))
    case.service.suggestions(case.owner, book_id)
    with case.db.transaction(actor=case.owner) as conn:
        row = conn.execute("SELECT d.id,d.version FROM drafts d JOIN sources s ON s.id=d.source_id WHERE d.book_id=? AND s.external_id=?", (book_id, external_id)).fetchone()
    return {"book_id": book_id, "draft_id": row["id"], "expected_version": row["version"]}


def test_review_tables_force_rls_and_no_direct_worker_read(business_case):
    case = business_case
    with case.psycopg.connect(ADMIN_URL) as conn:
        tables = ['review_previews', 'review_confirmations', 'review_book_receipts']
        rows = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND relname=ANY(%s)", (tables,)).fetchall()
        assert len(rows) == 3 and all(enabled and forced for _, enabled, forced in rows)
        for table in tables:
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + table,)).fetchone()[0]


def test_sql_preview_hidden_from_other_creator_employee_and_unassigned_book(business_case):
    case = business_case
    service = ReviewService(case.db, case.service)
    item = bank_draft(case, case.book, 'review-scope')
    value = service.preview(case.reviewer, ReviewPreviewRequest(items=[item]))
    for actor in (case.owner, case.employee, case.unassigned, case.viewer):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT * FROM review_previews WHERE id=?", (value['id'],)).fetchall() == []
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT id FROM review_previews WHERE id=?", (value['id'],)).fetchone()


def test_pg_cross_company_confirmation_and_stable_receipts(business_case):
    case = business_case
    service = ReviewService(case.db, case.service)
    items = [bank_draft(case, case.book, 'review-primary'), bank_draft(case, case.other_book, 'review-other')]
    value = service.preview(case.owner, ReviewPreviewRequest(items=items))
    request = ReviewConfirmRequest(idempotency_key='pg-review-confirm-001')
    result = service.confirm(case.owner, value['id'], request)
    assert result['approved_count'] == 2 and result['status'] == 'completed'
    assert service.confirm(case.owner, value['id'], request) == result
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute("SELECT count(*) FROM review_book_receipts WHERE confirmation_id=?", (result['id'],)).fetchone()[0] == 2


def test_pg_whole_request_authorization_rejects_other_company_before_preview(business_case):
    case = business_case
    service = ReviewService(case.db, case.service)
    items = [bank_draft(case, case.book, 'first'), bank_draft(case, case.other_book, 'second')]
    with pytest.raises(AccountingError):
        service.preview(case.reviewer, ReviewPreviewRequest(items=items))
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT * FROM review_previews").fetchall() == []


def test_pg_fake_success_receipt_and_update_privileges_rejected(business_case):
    case = business_case
    service = ReviewService(case.db, case.service)
    item = bank_draft(case, case.book, 'receipt-source')
    value = service.preview(case.owner, ReviewPreviewRequest(items=[item]))
    command_id, _ = service._command(case.owner, value['id'], ReviewConfirmRequest(idempotency_key='pg-receipt-test'))
    with pytest.raises(case.psycopg.Error, match='approved'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("INSERT INTO review_book_receipts VALUES(?,?,?,'now')", (command_id, case.book, json.dumps({'book_id': case.book, 'status': 'succeeded', 'approved_ids': [item['draft_id']]})))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("DELETE FROM review_previews WHERE id=?", (value['id'],))


def test_pg_revoke_hides_preview_and_command_receipts(business_case):
    case = business_case
    service = ReviewService(case.db, case.service)
    item = bank_draft(case, case.book, 'receipt-revoke')
    value = service.preview(case.reviewer, ReviewPreviewRequest(items=[item]))
    result = service.confirm(case.reviewer, value['id'], ReviewConfirmRequest(idempotency_key='pg-revoke-confirm'))
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE user_id=%s AND book_id=%s", (case.reviewer.user_id, case.book))
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT * FROM review_previews WHERE id=?", (value['id'],)).fetchall() == []
        assert conn.execute("SELECT * FROM review_confirmations WHERE id=?", (result['id'],)).fetchall() == []
        assert conn.execute("SELECT * FROM review_book_receipts WHERE confirmation_id=?", (result['id'],)).fetchall() == []
