"""Restricted PostgreSQL role and deferred journal proof, root runs this suite."""
import os
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.service import fingerprint, now
from app.legacy_migration.settlements import LegacyAllocationConfirm, LegacyAllocationReverse
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage13_legacy_settlements import bank, build_legacy_case, confirm, preview

WORKER_URL = os.getenv('ACCOUNTING_TEST_WORKER_DATABASE_URL')
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason='需要专用 PostgreSQL 受限 API 和独立 worker 连接')


@pytest.fixture
def legacy_pg_case(business_case, tmp_path, tmp_path_factory):
    base = business_case
    c = SimpleNamespace(db=base.db, ledger=base.service, manager=base.owner, actors={'accountant': base.reviewer, 'employee': base.employee, 'finance': base.finance}, book={'id': base.book}, company={'id': base.company}, psycopg=base.psycopg)
    try:
        yield build_legacy_case(c, tmp_path, Database(WORKER_URL), originals_root=tmp_path_factory.getbasetemp() / 'stage13-shared-originals')
    finally:
        # Only this fixture's generated synthetic firm. Keep immutable evidence,
        # but release pending leases even when setup/assertions fail midway.
        with base.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.migration_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now(),updated_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('queued','running','retry_wait')", (base.owner.firm_id,))


def test_native_tables_force_rls_and_worker_has_no_settlement_originals(business_case):
    c = business_case
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ('migration_open_items', 'migration_clearance_previews', 'migration_clearance_commands', 'migration_clearance_allocations', 'migration_clearance_reversals'):
            assert conn.execute('SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass', ('accounting.'+table,)).fetchone()[0]
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)', ('accounting_worker', 'accounting.'+table, 'SELECT')).fetchone()[0]
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)', ('accounting_app', 'accounting.'+table, 'UPDATE')).fetchone()[0]


def test_native_confirm_and_review_export_keep_old_history_separate(legacy_pg_case):
    c = legacy_pg_case
    source = bank(c)
    prepared = preview(c, source)
    receipt = confirm(c, prepared)
    draft = c.ledger.get_draft(c.actors['accountant'], c.book['id'], source['draft_id'])
    assert {line['account_code'] for line in draft['lines']} == {'2202.HIST', '1002'}
    approved = c.ledger.approve(c.actors['accountant'], c.book['id'], draft['id'], ApprovalRequest(expected_version=draft['version']))
    result = c.ledger.export(c.actors['accountant'], c.book['id'], ExportRequest(draft_ids=[approved['id']], idempotency_key=uuid4().hex))
    assert result['count'] == 1 and c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] == '900.00'
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('DELETE FROM migration_clearance_allocations WHERE command_id=?', (receipt['id'],))


def test_native_wrong_journal_and_duplicate_source_hiding_rejected(legacy_pg_case):
    c = legacy_pg_case
    source = bank(c)
    receipt = confirm(c, preview(c, source))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("UPDATE draft_lines SET account_code='6602.02' WHERE draft_id=? AND ordinal=0", (source['draft_id'],))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("UPDATE drafts SET description='任意覆盖',version=version+1 WHERE id=?", (source['draft_id'],))
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT accounting.legacy_clearance_source_locked(?,?)', (c.book['id'], source['id'])).fetchone()[0]
        assert conn.execute('SELECT accounting.legacy_open_item_used(?)', (c.item['id'],)).fetchone()[0] == 10000


def test_native_history_revocation_blocks_existing_preview_and_preserves_used(legacy_pg_case):
    c = legacy_pg_case
    source = bank(c)
    prepared = preview(c, source)
    c.migration.revoke_access(c.manager, c.book['id'], c.project, c.actors['accountant'].user_id, '撤销合成历史资料授权')
    with pytest.raises(AccountingError):
        confirm(c, prepared)
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT id FROM migration_open_items WHERE id=?', (c.item['id'],)).fetchall() == []
        assert conn.execute('SELECT accounting.legacy_open_item_used(?)', (c.item['id'],)).fetchone()[0] is None


def test_native_independent_reversal_proves_atomic_original_journal_restoration(legacy_pg_case):
    c = legacy_pg_case
    source = bank(c)
    before = c.ledger.get_draft(c.manager, c.book['id'], source['draft_id'])
    receipt = confirm(c, preview(c, source))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('INSERT INTO migration_clearance_reversals(id,command_id,project_id,bank_book_id,firm_id,company_id,created_by,created_at,idempotency_key,reason,expected_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (uuid4().hex,receipt['id'],c.project,c.book['id'],c.manager.firm_id,c.company['id'],c.manager.user_id,'2026-10-13T01:00:00+00:00',uuid4().hex,'只插撤销却不还原草稿必须拒绝',receipt['fingerprint']))
    request = LegacyAllocationReverse(expected_fingerprint=receipt['fingerprint'], idempotency_key=uuid4().hex, reason='另一会计核实错选历史付款')
    c.settlements.reverse(c.manager, c.book['id'], c.project, receipt['id'], request)
    after = c.ledger.get_draft(c.manager, c.book['id'], source['draft_id'])
    assert after['lines'] == before['lines'] and after['description'] == before['description']
    assert c.settlements.list(c.manager, c.book['id'], c.project)['items'][0]['remaining'] == '1000.00'


def test_native_hidden_current_period_allocations_still_reduce_authorized_history_balance(legacy_pg_case):
    c = legacy_pg_case
    source = bank(c)
    confirm(c, preview(c, source))
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE book_access SET role='viewer' WHERE book_id=? AND user_id=?", (c.book['id'], c.actors['accountant'].user_id))
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT id FROM migration_clearance_allocations WHERE item_id=?', (c.item['id'],)).fetchall() == []
        assert conn.execute('SELECT accounting.legacy_open_item_used(?)', (c.item['id'],)).fetchone()[0] == 10000
    value = c.settlements.list(c.actors['accountant'], c.book['id'], c.project)
    assert value['items'][0]['remaining'] == '900.00'
    assert value['commands'] == []


def test_native_missing_integer_part_amount_cannot_bypass_null_checks(legacy_pg_case):
    c = legacy_pg_case
    source = bank(c)
    prepared = preview(c, source)
    plan = dict(prepared['plan'])
    plan['parts'] = [dict(part) for part in plan['parts']]
    plan['parts'][0].pop('amount_cents')
    evil = uuid4().hex
    actor = c.actors['accountant']
    with c.db.transaction(write=True, actor=actor) as conn:
        original = conn.execute('SELECT * FROM migration_clearance_previews WHERE id=?', (prepared['id'],)).fetchone()
        conn.execute('INSERT INTO migration_clearance_previews(id,project_id,book_id,bank_book_id,firm_id,company_id,created_by,request_json,plan_json,fingerprint,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (evil,c.project,c.book['id'],c.book['id'],actor.firm_id,c.company['id'],actor.user_id,original['request_json'],json.dumps(plan),fingerprint(plan),now(),(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()))
    previous = c.ledger.get_draft(actor, c.book['id'], source['draft_id'])
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=actor) as conn:
            conn.execute('INSERT INTO migration_clearance_commands(id,project_id,book_id,bank_book_id,firm_id,company_id,preview_id,bank_source_id,bank_draft_id,bank_account_code,expected_draft_version,applied_draft_version,previous_draft_json,plan_json,created_by,idempotency_key,request_fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (uuid4().hex,c.project,c.book['id'],c.book['id'],actor.firm_id,c.company['id'],evil,source['id'],source['draft_id'],'1002',source['draft_version'],source['draft_version']+1,json.dumps({'description':previous['description'],'lines':previous['lines']}),json.dumps(plan),actor.user_id,uuid4().hex,'a'*64,now()))
    assert c.settlements.list(c.manager,c.book['id'],c.project)['items'][0]['remaining']=='1000.00'
