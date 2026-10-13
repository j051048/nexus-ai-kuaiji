"""Synthetic end-to-end legacy balance to current reviewed bank draft."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from types import SimpleNamespace
from time import monotonic, sleep
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest, ImportRequest
from app.business.storage import AttachmentStore
from app.legacy_migration.models import MappingSave, MigrationConfirm, ProjectCreate, VersionCommand
from app.legacy_migration.service import LegacyMigrationService
from app.legacy_migration.settlements import (LegacyAllocationConfirm, LegacyAllocationPreview,
                                             LegacyAllocationReverse, LegacySettlementService)
from app.legacy_migration.worker import MigrationWorker
from app.ledgers.models import PeriodCreate
from app.ledgers.service import LedgersService
from test_business import business
from test_stage13_normalization import settlement_package


def command(version, reason='仅用于合成试验'):
    return VersionCommand(expected_version=version, reason=reason, idempotency_key=uuid4().hex)


def drain_project_phase(c, worker, phase, expected_version, timeout_seconds=15):
    """Wait for this exact generation, without assuming a shared queue is empty."""
    deadline = monotonic() + timeout_seconds
    last = []
    while monotonic() < deadline:
        with c.db.transaction(actor=c.manager) as conn:
            last = [dict(row) for row in conn.execute('SELECT id,status,error_code,error_detail FROM migration_jobs WHERE project_id=? AND phase=? AND expected_version=? ORDER BY created_at,id', (c.project, phase, expected_version))]
        assert last, f'Synthetic project has no {phase} job'
        if all(row['status'] == 'succeeded' for row in last):
            return
        failed = [row for row in last if row['status'] in {'failed', 'stale', 'cancelled'}]
        assert not failed, f'Synthetic {phase} generation failed: {failed}'
        if worker.run_once() is None:
            # Capacity/fairness may temporarily yield no lease; it is not completion.
            sleep(0.05)
    raise AssertionError(f'Synthetic {phase} generation did not finish before timeout: {last}')


def build_legacy_case(c, tmp_path, worker_db=None, cutover_date='2026-10-01', source_system_key=None, reuse_master_id=None, originals_root=None):
    c.migration = LegacyMigrationService(c.db, AttachmentStore(originals_root or tmp_path / 'legacy-originals'))
    c.settlements = LegacySettlementService(c.db, c.migration, c.ledger)
    c.migration.settlements = c.settlements
    c.ledger.legacy_settlements = c.settlements
    package, mapping = settlement_package()
    history_end = date.fromisoformat(cutover_date) - timedelta(days=1)
    period = history_end.isoformat()[:7]
    package['context'].update(history_start=history_end.replace(day=1).isoformat(), history_end=history_end.isoformat(), cutover_date=cutover_date)
    for row in package['records']:
        row['period'] = period
        if row['kind'] == 'voucher':
            row['payload']['period'] = period
            row['payload']['date'] = period + '-05'
    # Bind historical liability to the current payable account without creating sources.
    for entry in mapping['accounts']:
        if entry['source_code'] == '2001':
            entry['target_code'] = '2202.HIST'
            entry['action'] = 'create'
        elif entry['source_code'] == '1001':
            entry['action'] = 'create'
        else:
            entry['target_code'] = '6602.02'
        if reuse_master_id:
            entry['action'] = 'use_existing'
    if reuse_master_id:
        mapping['auxiliaries'][0].update(action='use_existing', target_id=reuse_master_id)
    c.project = c.migration.create(c.manager, c.book['id'], ProjectCreate(target_book_id=c.book['id'], source_product='generic', product_version='SYNTHETIC-neutral-v1', external_book_id=package['context']['external_book_id'], source_system_key=source_system_key or 'SYNTHETIC-'+uuid4().hex, **{key: package['context'][key] for key in ('history_start', 'history_end', 'cutover_date')}, idempotency_key=uuid4().hex, reason='仅用于合成历史接管'))['project']['id']
    detail = c.migration.get(c.manager, c.book['id'], c.project)
    c.migration.upload(c.manager, c.book['id'], c.project, 'SYNTHETIC-未结包.json', json.dumps(package, ensure_ascii=False).encode(), 'package', {}, detail['project']['version'], uuid4().hex)
    detail = c.migration.get(c.manager, c.book['id'], c.project)
    c.migration.enqueue(c.manager, c.book['id'], c.project, 'parse', command(detail['project']['version']))
    worker = MigrationWorker(c.migration if worker_db is None else LegacyMigrationService(worker_db, c.migration.storage))
    drain_project_phase(c, worker, 'parse', detail['project']['version'])
    detail = c.migration.get(c.manager, c.book['id'], c.project)
    c.migration.save_mapping(c.manager, c.book['id'], c.project, MappingSave(expected_version=detail['project']['version'], reason='确认模拟科目及往来方', idempotency_key=uuid4().hex, **mapping))
    detail = c.migration.get(c.manager, c.book['id'], c.project)
    c.migration.enqueue(c.manager, c.book['id'], c.project, 'validate', command(detail['project']['version']))
    drain_project_phase(c, worker, 'validate', detail['project']['version'])
    c.migration.grant_access(c.manager, c.book['id'], c.project, c.actors['accountant'].user_id, 'reviewer', '独立核验合成历史资料')
    detail = c.migration.get(c.actors['accountant'], c.book['id'], c.project)
    report = c.migration.report(c.actors['accountant'], c.book['id'], c.project)
    assert report['report']['blocking_count'] == 0, report
    confirmation = dict(expected_version=detail['project']['version'], validation_id=report['validation_id'], basis_token=report['basis_token'], confirmed=True, reason='核对合成原件、期初期末及往来明细', idempotency_key=uuid4().hex)
    c.migration.accept(c.actors['accountant'], c.book['id'], c.project, MigrationConfirm(**confirmation))
    c.migration.activate(c.manager, c.book['id'], c.project, MigrationConfirm(**dict(confirmation, idempotency_key=uuid4().hex)))
    c.item = c.settlements.list(c.actors['accountant'], c.book['id'], c.project)['items'][0]
    return c


@pytest.fixture
def legacy_case(business, tmp_path):
    return build_legacy_case(business, tmp_path)


def bank(c, amount='100.00', direction='expense'):
    c.ledger.import_sources(c.manager, c.book['id'], ImportRequest(source_name='合成历史清偿流水', rows=[{'external_id': 'SYNTHETIC-BANK-'+uuid4().hex, 'date': '2026-10-05', 'description': '偿付接管前历史欠款', 'counterparty': '模拟往来方', 'amount': amount, 'direction': direction, 'evidence_ref': 'SYNTHETIC-回单'}]))
    c.ledger.suggestions(c.manager, c.book['id'])
    return c.settlements.eligible_current_bank_sources(c.actors['accountant'], c.book['id'], c.project, c.book['id'])['banks'][-1]


def preview(c, source, amount=None):
    return c.settlements.preview(c.actors['accountant'], c.book['id'], c.project, LegacyAllocationPreview(bank_book_id=c.book['id'], bank_source_id=source['id'], bank_account_code='1002', parts=[{'item_id': c.item['id'], 'amount': amount or source['amount']}], reason='人工确认合成回单与历史往来'))


def confirm(c, prepared, actor=None):
    return c.settlements.confirm(actor or c.actors['accountant'], c.book['id'], c.project, prepared['id'], LegacyAllocationConfirm(expected_fingerprint=prepared['fingerprint'], idempotency_key=uuid4().hex))


def test_cutover_has_immutable_history_item_and_no_fake_business_sources(legacy_case):
    c = legacy_case
    assert c.item['amount'] == c.item['remaining'] == '1000.00'
    assert c.item['target_account_code'] == '2202.HIST'
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM sources WHERE book_id=?', (c.book['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM business_events WHERE company_id=?', (c.company['id'],)).fetchone()[0] == 0
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('UPDATE migration_open_items SET amount_cents=1 WHERE id=?', (c.item['id'],))


def test_clearing_replaces_current_draft_then_human_review_and_export(legacy_case):
    c = legacy_case
    source = bank(c)
    prepared = preview(c, source)
    receipt = confirm(c, prepared)
    draft = c.ledger.get_draft(c.actors['accountant'], c.book['id'], receipt['bank_draft_id'])
    assert draft['status'] == 'draft' and draft['version'] == source['draft_version'] + 1
    assert {line['account_code'] for line in draft['lines']} == {'2202.HIST', '1002'}
    assert c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] == '900.00'
    approved = c.ledger.approve(c.actors['accountant'], c.book['id'], draft['id'], ApprovalRequest(expected_version=draft['version']))
    result = c.ledger.export(c.actors['accountant'], c.book['id'], ExportRequest(draft_ids=[approved['id']], idempotency_key=uuid4().hex))
    assert result['count'] == 1
    assert '2202' in result['csv'] and '6602' not in result['csv']
    with pytest.raises(AccountingError):
        c.settlements.reverse(c.manager, c.book['id'], c.project, receipt['id'], LegacyAllocationReverse(expected_fingerprint=receipt['fingerprint'], idempotency_key=uuid4().hex, reason='已审核后禁止撤销'))


def test_partial_bank_amount_is_not_silently_left_for_expense(legacy_case):
    c = legacy_case
    source = bank(c)
    with pytest.raises(AccountingError, match='完整分配'):
        preview(c, source, '99.99')


def test_previews_private_idempotent_and_stale_on_changed_draft(legacy_case):
    c = legacy_case
    source = bank(c)
    prepared = preview(c, source)
    request = LegacyAllocationConfirm(expected_fingerprint=prepared['fingerprint'], idempotency_key=uuid4().hex)
    with pytest.raises(AccountingError):
        c.settlements.confirm(c.manager, c.book['id'], c.project, prepared['id'], request)
    first = c.settlements.confirm(c.actors['accountant'], c.book['id'], c.project, prepared['id'], request)
    assert c.settlements.confirm(c.actors['accountant'], c.book['id'], c.project, prepared['id'], request) == first
    with pytest.raises(Exception):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('UPDATE draft_lines SET account_code=\'6602\' WHERE draft_id=? AND ordinal=0', (source['draft_id'],))


def test_independent_reversal_restores_previous_draft_and_reference_remaining(legacy_case):
    c = legacy_case
    source = bank(c)
    previous = c.ledger.get_draft(c.manager, c.book['id'], source['draft_id'])
    receipt = confirm(c, preview(c, source))
    request = LegacyAllocationReverse(expected_fingerprint=receipt['fingerprint'], idempotency_key=uuid4().hex, reason='另一会计核实所选旧欠款错误')
    with pytest.raises(AccountingError, match='另一名'):
        c.settlements.reverse(c.actors['accountant'], c.book['id'], c.project, receipt['id'], request)
    result = c.settlements.reverse(c.manager, c.book['id'], c.project, receipt['id'], request)
    assert c.settlements.reverse(c.manager, c.book['id'], c.project, receipt['id'], request) == result
    restored = c.ledger.get_draft(c.manager, c.book['id'], source['draft_id'])
    assert restored['lines'] == previous['lines'] and restored['description'] == previous['description']
    assert c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] == '1000.00'


def test_enterprise_and_role_revocation_cannot_consume_history(legacy_case):
    c = legacy_case
    for actor in (c.actors['employee'], c.actors['finance']):
        with pytest.raises(AccountingError):
            c.settlements.list(actor, c.book['id'], c.project)
    source = bank(c)
    prepared = preview(c, source)
    c.migration.revoke_access(c.manager, c.book['id'], c.project, c.actors['accountant'].user_id, '模拟历史资料撤权')
    with pytest.raises(AccountingError):
        confirm(c, prepared)


def test_concurrent_different_bank_previews_cannot_overspend_reference_remaining(legacy_case):
    c = legacy_case
    first = bank(c, '600.00')
    second = bank(c, '700.00')
    # Locate by source id: no fallback to an unrelated first record.
    prepared = [preview(c, first), preview(c, second)]
    def invoke(value):
        try:
            return confirm(c, value)
        except AccountingError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, prepared))
    assert sum(result is not None for result in results) == 1
    assert c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] in {'300.00', '400.00'}


@pytest.mark.parametrize('amount', ['0', '-1', '1e2', '1.001', 1.0, True])
def test_preview_money_is_strict_and_positive(amount):
    with pytest.raises(ValidationError):
        LegacyAllocationPreview(bank_book_id='b', bank_source_id='s', bank_account_code='1002', parts=[{'item_id': 'i', 'amount': amount}], reason='人工确认')


def test_future_period_of_same_ledger_can_clear_history_without_copying_debt(legacy_case):
    c = legacy_case
    future = LedgersService(c.db).create_period(c.manager, c.book['id'], PeriodCreate(period='2026-11', name='模拟下月', source_book_id=c.book['id'], inherit_user_ids=[c.actors['accountant'].user_id], idempotency_key=uuid4().hex))['book_id']
    c.ledger.import_sources(c.manager, future, ImportRequest(source_name='模拟次月偿付', rows=[{'external_id': uuid4().hex, 'date': '2026-11-05', 'description': '偿付接管前历史欠款', 'counterparty': '模拟往来方', 'amount': '100.00', 'direction': 'expense', 'evidence_ref': 'SYNTHETIC-次月回单'}]))
    c.ledger.suggestions(c.manager, future)
    banks = c.settlements.eligible_current_bank_sources(c.actors['accountant'], c.book['id'], c.project, future)['banks']
    prepared = c.settlements.preview(c.actors['accountant'], c.book['id'], c.project, LegacyAllocationPreview(bank_book_id=future, bank_source_id=banks[0]['id'], bank_account_code='1002', parts=[{'item_id': c.item['id'], 'amount': '100.00'}], reason='下一月清偿同一历史义务'))
    receipt = confirm(c, prepared)
    assert receipt['bank_book_id'] == future
    assert c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] == '900.00'
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT COUNT(*) FROM migration_open_items WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)', (future,)).fetchone()[0] == 1


def test_stale_current_draft_rejects_old_preview_before_any_allocation(legacy_case):
    c = legacy_case
    source = bank(c)
    prepared = preview(c, source)
    draft = c.ledger.get_draft(c.manager, c.book['id'], source['draft_id'])
    c.ledger.update_draft(c.manager, c.book['id'], draft['id'], DraftUpdate(expected_version=draft['version'], description='人工补充回单摘要', lines=draft['lines']))
    with pytest.raises(AccountingError, match='已变化'):
        confirm(c, prepared)
    assert c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] == '1000.00'


def test_receipt_direction_cannot_be_net_settled_against_payable(legacy_case):
    c = legacy_case
    source = bank(c, direction='income')
    with pytest.raises(AccountingError, match='应付须对应支出'):
        preview(c, source)


def test_new_period_cutover_cannot_reinitialize_same_historical_obligation(legacy_case, tmp_path):
    c = legacy_case
    future = LedgersService(c.db).create_period(c.manager, c.book['id'], PeriodCreate(period='2026-11', name='模拟第二次接管', source_book_id=c.book['id'], inherit_user_ids=[c.actors['accountant'].user_id], idempotency_key=uuid4().hex))['book_id']
    with c.db.transaction(actor=c.manager) as conn:
        source_key = conn.execute('SELECT source_system_key FROM migration_projects WHERE id=?', (c.project,)).fetchone()[0]
    secondary = SimpleNamespace(db=c.db, ledger=c.ledger, manager=c.manager, actors=c.actors, book={'id': future}, company=c.company)
    with pytest.raises(AccountingError, match='不能按新期间'):
        build_legacy_case(secondary, tmp_path / 'second', cutover_date='2026-11-01', source_system_key=source_key, reuse_master_id=c.item['counterparty_id'])
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM migration_cutovers WHERE book_id=?', (future,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM migration_open_items WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)', (future,)).fetchone()[0] == 1
        assert conn.execute('SELECT status FROM migration_projects WHERE id=?', (secondary.project,)).fetchone()[0] == 'accepted'


def test_accounting_guard_accepts_real_database_lines_and_requires_actor(legacy_case):
    c = legacy_case
    source = bank(c)
    confirm(c, preview(c, source))
    with c.db.transaction(actor=c.manager) as conn:
        raw_source = conn.execute('SELECT * FROM sources WHERE id=?', (source['id'],)).fetchone()
        raw_lines = conn.execute('SELECT * FROM draft_lines WHERE draft_id=? ORDER BY ordinal', (source['draft_id'],)).fetchall()
        assert c.settlements.blocking_issues_in_transaction(conn, c.manager, raw_source, raw_lines) == []
        assert c.settlements.blocking_issues_in_transaction(conn, None, raw_source, raw_lines)
