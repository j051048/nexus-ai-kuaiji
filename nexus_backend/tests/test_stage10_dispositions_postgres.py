from uuid import uuid4
import pytest
from app.accounting.bank_imports import BankImportService
from app.accounting.models import ImportRequest, SourceRow
from app.dispositions.models import SameTransaction
from app.dispositions.service import SourceDispositionService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限 API 测试连接')


def prepare(c):
    for namespace, tx in (('statement', 'tx1'), ('feed', 'tx2')):
        c.service.import_sources(c.owner, c.book, ImportRequest(source_name='合成银行回单', namespace=namespace, bank_account='合成账户尾号4321',
            rows=[SourceRow(external_id=tx, stable_transaction_id=tx, date='2026-10-03', amount='80.00', direction='expense', counterparty='合成供应商', description='办公用品支出')]))
    c.service.suggestions(c.owner, c.book)
    draft = c.service.get_book(c.owner, c.book)['drafts'][0]
    view = BankImportService(c.db).overview(c.reviewer, c.book, draft['source_id'])
    other = view['candidates'][0]
    body = SameTransaction(expected_version=draft['version'], source_fingerprint=view['fingerprint'], canonical_fingerprint=other['fingerprint'],
        reason='核对银行原交易号与回单，两份导入实为同一支出', idempotency_key=uuid4().hex)
    return draft, other, body


def test_pg_same_transaction_isolation_unblocks_canonical_only_and_preserves_history(business_case):
    c = business_case; draft, other, body = prepare(c); service = SourceDispositionService(c.db)
    result = service.confirm(c.reviewer, c.book, draft['source_id'], other['id'], body)
    assert result['inactive'] and service.confirm(c.reviewer, c.book, draft['source_id'], other['id'], body)['duplicate']
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT accounting.source_inactive(?,?)', (c.book, draft['source_id'])).fetchone()[0]
        assert not conn.execute('SELECT accounting.source_inactive(?,?)', (c.book, other['id'])).fetchone()[0]
        assert conn.execute('SELECT accounting.bank_source_import_blocked(?,?)', (c.book, draft['source_id'])).fetchone()[0]
        assert not conn.execute('SELECT accounting.bank_source_import_blocked(?,?)', (c.book, other['id'])).fetchone()[0]
        assert conn.execute('SELECT COUNT(*) FROM sources WHERE book_id=?', (c.book,)).fetchone()[0] == 2
    for sql in ('UPDATE drafts SET version=version+1 WHERE id=?', 'DELETE FROM draft_lines WHERE draft_id=?'):
        with pytest.raises(c.psycopg.errors.CheckViolation):
            with c.db.transaction(write=True, actor=c.reviewer) as conn:
                conn.execute(sql, (draft['id'],))


def test_pg_native_isolation_requires_current_review_scope_and_raw_insert_is_denied(business_case):
    c = business_case; draft, other, body = prepare(c)
    for actor in (c.employee, c.finance, c.viewer):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            with c.db.transaction(write=True, actor=actor) as conn:
                conn.execute('SELECT accounting.confirm_same_bank_transaction(?,?,?,?,?,?,?,?,?)',
                    (c.book, draft['source_id'], other['id'], body.expected_version, body.source_fingerprint, body.canonical_fingerprint, body.reason, body.idempotency_key, 'a'*64))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute('INSERT INTO bank_source_dispositions(id) VALUES(?)', (uuid4().hex,))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(actor=c.employee) as conn:
            conn.execute('SELECT accounting.source_inactive(?,?)', (c.book, draft['source_id']))


def test_pg_native_wrong_source_fingerprint_cannot_publish_or_bump_version(business_case):
    c = business_case; draft, other, body = prepare(c)
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute('SELECT accounting.confirm_same_bank_transaction(?,?,?,?,?,?,?,?,?)',
                (c.book, draft['source_id'], other['id'], body.expected_version, '0'*64, body.canonical_fingerprint, body.reason, body.idempotency_key, 'a'*64))
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT version FROM drafts WHERE id=?', (draft['id'],)).fetchone()[0] == draft['version']
        assert conn.execute('SELECT * FROM bank_source_dispositions WHERE source_id=?', (draft['source_id'],)).fetchall() == []


def test_pg_native_isolation_with_original_active_case_rolls_back_and_then_can_finish(business_case):
    from app.business.service import BusinessService
    from app.cases.service import CaseService
    from app.cases.models import CaseCreate, CaseCommand
    c=business_case;draft,other,body=prepare(c)
    cases=CaseService(c.db,BusinessService(c.db))
    case=cases.create(c.reviewer,CaseCreate(book_id=c.book,draft_id=draft['id'],kind='exception',
        title='原回单异常还须完成',reason='重复隔离不能取消核验任务',idempotency_key=uuid4().hex))
    with pytest.raises(c.psycopg.errors.CheckViolation,match='Original active'):
        with c.db.transaction(write=True,actor=c.reviewer) as conn:
            conn.execute('SELECT accounting.confirm_same_bank_transaction(?,?,?,?,?,?,?,?,?)',
                (c.book,draft['source_id'],other['id'],body.expected_version,body.source_fingerprint,body.canonical_fingerprint,body.reason,body.idempotency_key,'e'*64))
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT version FROM drafts WHERE id=?',(draft['id'],)).fetchone()[0]==body.expected_version
        assert conn.execute('SELECT * FROM bank_source_dispositions WHERE source_id=?',(draft['source_id'],)).fetchall()==[]
    cases.command(c.reviewer,case['id'],'close',CaseCommand(expected_version=case['version'],reason='已完成原核验并人工确认重复',idempotency_key=uuid4().hex))
    assert SourceDispositionService(c.db).confirm(c.reviewer,c.book,draft['source_id'],other['id'],body)['inactive']
