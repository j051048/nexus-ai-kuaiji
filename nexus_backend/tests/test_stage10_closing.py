import json
import sqlite3
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, ImportRequest, SourceRow
from app.accounting.seed import LOCAL_ACTOR
from app.closing.models import CloseRequest, PreflightRequest, SnapshotReview
from app.closing.queries import issues_sql, receipt_valid_sql, sqlite_issue_view_sql
from app.closing.service import ClosingService
from app.delivery.models import ReceiptCreate
from test_catalog import case
from test_stage9_delivery import create, exported, facts
from test_business import business, create as create_business, approved, handoff


def close_ready(service, actor, book):
    snapshot = service.preflight(actor, book, PreflightRequest(idempotency_key=uuid4().hex))
    assert snapshot['report']['passed'], snapshot['report']
    service.review(actor, book, snapshot['id'], SnapshotReview(reason='核实本期资料与银行流水完整并复核通过', materials_confirmed=True))
    return snapshot, service.close(actor, book, CloseRequest(snapshot_id=snapshot['id'], reason='依据已复核快照完成月结'))


def test_empty_book_close_has_immutable_snapshot_and_explicit_coverage(case):
    accounting, _, book = case
    closing = ClosingService(accounting.db)
    snapshot = closing.preflight(LOCAL_ACTOR, book, PreflightRequest(idempotency_key=uuid4().hex))
    assert snapshot['report']['passed']
    with pytest.raises(AccountingError, match='本人复核'):
        closing.close(LOCAL_ACTOR, book, CloseRequest(snapshot_id=snapshot['id'], reason='不得直接关期'))
    with pytest.raises(ValidationError):
        SnapshotReview(reason='完整性未明确确认', materials_confirmed=False)
    closing.review(LOCAL_ACTOR, book, snapshot['id'], SnapshotReview(reason='没有本期业务，银行账户及资料已核实', materials_confirmed=True))
    request = CloseRequest(snapshot_id=snapshot['id'], reason='已核对全部当前资料及完整性')
    assert closing.close(LOCAL_ACTOR, book, request)['status'] == 'closed'
    assert closing.close(LOCAL_ACTOR, book, request)['replayed']
    assert closing.overview(LOCAL_ACTOR, book)['snapshots'][0]['historical']
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE closing_snapshots SET report_json='{}' WHERE id=?", (snapshot['id'],))
        with pytest.raises(sqlite3.IntegrityError, match='reopen'):
            conn.execute("UPDATE periods SET status='open' WHERE book_id=?", (book,))
        with pytest.raises(sqlite3.IntegrityError, match='deleted'):
            conn.execute('DELETE FROM periods WHERE book_id=?', (book,))


def test_unprocessed_sources_and_missing_evidence_block_close(case):
    accounting, _, book = case
    accounting.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name='虚构流水', rows=[SourceRow(external_id='unprocessed-1', date='2026-10-01', description='办公支出', counterparty='虚构供方', amount='13.00', direction='expense')]))
    closing = ClosingService(accounting.db)
    snapshot = closing.preflight(LOCAL_ACTOR, book, PreflightRequest(idempotency_key=uuid4().hex))
    assert {'unprocessed', 'missing_evidence', 'unmatched_bank'} <= set(snapshot['report']['counts'])
    with pytest.raises(AccountingError, match='阻断'):
        closing.review(LOCAL_ACTOR, book, snapshot['id'], SnapshotReview(reason='不能忽略已存在的阻断', materials_confirmed=True))
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='reviewed close'):
            conn.execute("UPDATE periods SET status='closed' WHERE book_id=?", (book,))


def test_exported_bank_with_complete_receipt_can_close_without_business_match(case):
    accounting, delivery, book, export, _ = exported(case)
    closing = ClosingService(accounting.db)
    initial = closing.preflight(LOCAL_ACTOR, book, PreflightRequest(idempotency_key=uuid4().hex))
    assert set(initial['report']['counts']) == {'delivery_pending'}
    create(delivery, book, export['id'], facts(delivery, book, export['id']))
    _, result = close_ready(closing, LOCAL_ACTOR, book)
    assert result['status'] == 'closed'
    # Receipts and explanatory notes remain append-only after locking.
    create(delivery, book, export['id'], facts(delivery, book, export['id']))
    assert closing.overview(LOCAL_ACTOR, book)['period_status'] == 'closed'


@pytest.mark.parametrize('change', ['amount', 'auxiliary', 'missing_lines', 'unknown', 'external_collision'])
def test_close_recomputes_receipt_contents_instead_of_trusting_accepted(case, change):
    accounting, delivery, book, export, _ = exported(case)
    body = facts(delivery, book, export['id'])
    if change == 'amount':
        body['items'][0]['amount'] = '113.01'
    elif change == 'auxiliary':
        body['items'][0]['lines'][0]['auxiliary_values'] = {'project': '未经授权的新项目'}
    elif change == 'missing_lines':
        body['items'][0]['lines'] = None
    elif change == 'unknown':
        body['items'][0]['status'] = 'unknown'
    else:
        prior = deepcopy(body)
        prior['items'][0]['external_voucher_id'] = '另一外部凭证号'
        create(delivery, book, export['id'], prior)
    create(delivery, book, export['id'], body)
    report = ClosingService(accounting.db).overview(LOCAL_ACTOR, book)['current_report']
    assert not report['passed'] and report['counts']['delivery_pending'] == 1


def test_change_after_review_invalidates_snapshot_atomically(case):
    accounting, _, book = case
    closing = ClosingService(accounting.db)
    snapshot = closing.preflight(LOCAL_ACTOR, book, PreflightRequest(idempotency_key=uuid4().hex))
    closing.review(LOCAL_ACTOR, book, snapshot['id'], SnapshotReview(reason='预检时全部资料完整', materials_confirmed=True))
    accounting.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name='新增流水', rows=[SourceRow(external_id='late-source', date='2026-10-02', description='晚到资料', counterparty='晚到供方', amount='1.00', direction='expense', evidence_ref='后到原件')]))
    with pytest.raises(AccountingError, match='当前通过预检'):
        closing.close(LOCAL_ACTOR, book, CloseRequest(snapshot_id=snapshot['id'], reason='旧快照不可继续关闭'))
    assert closing.get_snapshot(LOCAL_ACTOR, book, snapshot['id'])['stale']
    assert closing.overview(LOCAL_ACTOR, book)['period_status'] == 'open'


def test_snapshot_key_and_ids_cannot_cross_company(case):
    accounting, _, book = case
    closing = ClosingService(accounting.db)
    key = uuid4().hex
    snapshot = closing.preflight(LOCAL_ACTOR, book, PreflightRequest(idempotency_key=key))
    other = accounting.create_book(LOCAL_ACTOR, BookCreate(name='另一企业', period='2026-10'))['id']
    with pytest.raises(AccountingError, match='另一账套'):
        closing.preflight(LOCAL_ACTOR, other, PreflightRequest(idempotency_key=key))
    with pytest.raises(AccountingError, match='不存在'):
        closing.get_snapshot(LOCAL_ACTOR, other, snapshot['id'])


def test_sql_report_and_shared_definition_are_identical(case):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sqlite = (root / 'migrations/sqlite/037_closing_receipt_shape.sql').read_text(encoding='utf-8')
    postgres = (root / 'migrations/postgres/052_closing_receipt_shape.sql').read_text(encoding='utf-8')
    assert sqlite_issue_view_sql().strip() in sqlite
    assert issues_sql('postgres', 'target_book') in postgres
    accounting, _, book = case
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        native = [dict(row) for row in conn.execute('SELECT category,object_type,object_id,detail FROM closing_issue_rows WHERE book_id=? ORDER BY category,object_type,object_id', (book,))]
        api = [dict(row) for row in conn.execute(issues_sql('sqlite'), (book,))]
        assert native == api


def test_pending_private_business_is_generic_and_does_not_block_other_company(business):
    c = business
    event = create_business(c, description='员工私有业务描述不得泄露')
    closing = ClosingService(c.db)
    own = closing.overview(c.actors['accountant'], c.book['id'])['current_report']
    assert own['counts'] == {'business_pending': 1}
    assert own['issues'][0]['object_type'] == 'book' and own['issues'][0]['object_id'] == c.book['id']
    assert event['id'] not in json.dumps(own) and event['payload']['description'] not in json.dumps(own, ensure_ascii=False)
    assert closing.overview(c.manager, c.other_book['id'])['current_report']['passed']


def test_paid_business_remains_blocked_until_bank_match(business):
    c = business
    event = approved(c, template='procurement_expense', payment_status='paid')
    mapped = handoff(c, event)
    report = ClosingService(c.db).overview(c.actors['accountant'], c.book['id'])['current_report']
    assert report['counts']['unmatched_business'] == 1 and not report['passed']
    assert any(issue['object_id'] == mapped['draft_id'] for issue in report['issues'])


@pytest.mark.parametrize('bad_lines', [None, 'plain text', 1, {}, [None], ['plain text'], [1], [{'ordinal': 0, 'auxiliary_values': None}]])
def test_receipt_predicate_handles_scalar_null_and_missing_shapes_without_exception(case, bad_lines):
    accounting, delivery, book, export, _ = exported(case)
    manifest = delivery.manifest(LOCAL_ACTOR, book, export['id'])
    body = facts(delivery, book, export['id'])
    body['items'][0]['lines'] = bad_lines
    # CTE fixtures deliberately bypass the immutable receipt storage boundary
    # to prove the query itself remains safe for every hostile JSON shape.
    query = """WITH exports AS (SELECT ? AS id,? AS book_id,1 AS count),
      delivery_manifests AS (SELECT ? AS export_id,? AS book_id,? AS payload_json),
      delivery_receipts AS (SELECT 'fixture' AS id,? AS export_id,? AS book_id,? AS body_json,'now' AS created_at),
      delivery_receipt_items AS (SELECT '' AS receipt_id,'' AS book_id,'' AS claimed_draft_id,'' AS external_voucher_id WHERE 0)
      SELECT """ + receipt_valid_sql('sqlite') + ' AS matches FROM exports x'
    params = (export['id'], book, export['id'], book, json.dumps(manifest), export['id'], book, json.dumps(body))
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert not conn.execute(query, params).fetchone()['matches']
