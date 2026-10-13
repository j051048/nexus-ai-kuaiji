import json
from copy import deepcopy
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.closing.models import CloseRequest, PreflightRequest, SnapshotReview
from app.closing.service import ClosingService
from app.delivery.models import ReceiptCreate
from app.delivery.service import DeliveryService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_postgres_integration import request

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限运行测试账号')


def ready_export(case):
    case.service.import_sources(case.owner, case.book, request())
    case.service.suggestions(case.owner, case.book)
    draft = case.service.get_book(case.owner, case.book)['drafts'][0]
    case.service.approve(case.owner, case.book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    export = case.service.export(case.owner, case.book, ExportRequest(draft_ids=[draft['id']], idempotency_key=uuid4().hex))
    delivery = DeliveryService(case.db)
    body = json.loads(delivery.template(case.owner, case.book, export['id']))
    body['items'][0].update(status='accepted', external_voucher_id='测试客户软件凭证-001')
    return delivery, export, body


def test_native_month_close_tables_and_functions_deny_worker_and_private_scope(business_case):
    case = business_case
    with case.psycopg.connect(ADMIN_URL) as conn:
        for table in ('closing_snapshots', 'closing_reviews', 'closing_receipts'):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ('accounting.' + table,)).fetchone() == (True, True)
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + table,)).fetchone()[0]
        assert not conn.execute("SELECT has_function_privilege('accounting_worker','accounting.close_with_snapshot(text,text,text)','EXECUTE')").fetchone()[0]
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(actor=case.employee) as conn:
            conn.execute('SELECT accounting.closing_fingerprint(?)', (case.book,))


def test_native_direct_close_and_reopen_are_unavailable(business_case):
    case = business_case
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege, match='reviewed close'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE periods SET status='closed' WHERE book_id=?", (case.book,))
    closing = ClosingService(case.db)
    snapshot = closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))
    closing.review(case.owner, case.book, snapshot['id'], SnapshotReview(reason='空账套本期没有业务，已核实银行资料完整', materials_confirmed=True))
    closing.close(case.owner, case.book, CloseRequest(snapshot_id=snapshot['id'], reason='依据当前复核快照锁期'))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege, match='reopen'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE periods SET status='open' WHERE book_id=?", (case.book,))


def test_native_cannot_forge_passing_preflight_or_review(business_case):
    case = business_case
    case.service.import_sources(case.owner, case.book, request())
    with case.db.transaction(write=True, actor=case.owner) as conn:
        snapshot_id = conn.execute('SELECT accounting.create_closing_snapshot(?,?)', (case.book, uuid4().hex)).fetchone()[0]
    with pytest.raises(case.psycopg.Error, match='blocked or stale'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("SELECT accounting.review_closing_snapshot(?,?,'试图绕过未审事实',true)", (case.book, snapshot_id))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE closing_snapshots SET report_json='{\"passed\":true}' WHERE id=?", (snapshot_id,))


@pytest.mark.parametrize('change', ['amount', 'auxiliary', 'unknown', 'missing_lines'])
def test_pg_and_sqlite_predicates_reject_accepted_but_different_receipt(business_case, change):
    case = business_case
    delivery, export, body = ready_export(case)
    if change == 'amount':
        body['items'][0]['amount'] = '0.31'
    elif change == 'auxiliary':
        body['items'][0]['lines'][0]['auxiliary_values'] = {'project': '附加错误项目'}
    elif change == 'unknown':
        body['items'][0]['status'] = 'unknown'
    else:
        body['items'][0]['lines'] = None
    delivery.create_receipt(case.owner, case.book, export['id'], ReceiptCreate(**body, idempotency_key=uuid4().hex, reason='合成错误回执验证'))
    report = ClosingService(case.db).overview(case.owner, case.book)['current_report']
    assert report['counts'] == {'delivery_pending': 1} and not report['passed']


def test_matching_manual_receipt_allows_explicit_snapshot_close_and_append_after_lock(business_case):
    case = business_case
    delivery, export, body = ready_export(case)
    delivery.create_receipt(case.owner, case.book, export['id'], ReceiptCreate(**body, idempotency_key=uuid4().hex, reason='人工提供匹配回执，未实连外部软件'))
    closing = ClosingService(case.db)
    snapshot = closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))
    assert snapshot['report']['passed']
    closing.review(case.owner, case.book, snapshot['id'], SnapshotReview(reason='核实本期资料完整且客户软件回执逐行一致', materials_confirmed=True))
    result = closing.close(case.owner, case.book, CloseRequest(snapshot_id=snapshot['id'], reason='月结核对完成'))
    assert result['status'] == 'closed'
    delivery.create_receipt(case.owner, case.book, export['id'], ReceiptCreate(**body, idempotency_key=uuid4().hex, reason='关闭后追加回执，不修改原账'))
    assert closing.overview(case.owner, case.book)['period_status'] == 'closed'


def test_native_close_rechecks_facts_after_snapshot_review(business_case):
    case = business_case
    closing = ClosingService(case.db)
    snapshot = closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))
    closing.review(case.owner, case.book, snapshot['id'], SnapshotReview(reason='核实空期间当前资料完整', materials_confirmed=True))
    case.service.import_sources(case.owner, case.book, request())
    with pytest.raises(case.psycopg.Error, match='current passed'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("SELECT accounting.close_with_snapshot(?,?,'旧快照不可关闭')", (case.book, snapshot['id']))
    assert closing.overview(case.owner, case.book)['period_status'] == 'open'
