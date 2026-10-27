import pytest
from app.accounting.bank_imports import BankImportService, DuplicateDistinction
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, ApprovalRequest
from test_business_postgres import ADMIN_URL, APP_URL, business_postgres, business_case
from test_stage9_bank_imports import statement

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员及受限运行连接')


def prepare(c):
    c.service.import_sources(c.owner, c.book, statement())
    c.service.import_sources(c.owner, c.book, statement(stable='second', external='second', namespace='another'))
    c.service.suggestions(c.owner, c.book)
    draft = c.service.get_book(c.owner, c.book)['drafts'][0]
    checks = BankImportService(c.db)
    view = checks.overview(c.reviewer, c.book, draft['source_id'])
    other = view['candidates'][0]
    request = DuplicateDistinction(source_fingerprint=view['fingerprint'], other_fingerprint=other['fingerprint'], expected_version=draft['version'], reason='已对照两笔不同原始交易及业务凭据')
    return checks, draft, view, other, request


def test_pg_stable_bank_identity_and_duplicate_guard_use_canonical_facts(business_case):
    c = business_case
    checks, draft, view, other, request = prepare(c)
    assert c.service.import_sources(c.owner, c.book, statement(external='new-display'))['duplicates'] == 1
    assert any('独立交易' in issue for issue in draft['issues'])
    assert checks.distinguish(c.reviewer, c.book, view['source_id'], other['id'], request)['distinct']
    refreshed = c.service.get_draft(c.reviewer, c.book, draft['id'])
    assert refreshed['version'] == draft['version'] + 1 and not refreshed['issues']
    assert c.service.approve(c.reviewer, c.book, draft['id'], ApprovalRequest(expected_version=refreshed['version']))['status'] == 'approved'


def test_pg_bank_identity_and_decisions_force_rls_and_no_worker_access(business_case):
    c = business_case
    checks, _, view, _, _ = prepare(c)
    with c.psycopg.connect(ADMIN_URL) as conn:
        rows = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND relname IN ('bank_import_keys','bank_duplicate_decisions')").fetchall()
        assert len(rows) == 2 and all(enabled and forced for _, enabled, forced in rows)
        for name, _, _ in rows:
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + name,)).fetchone()[0]
    for actor in (c.employee, c.unassigned):
        with c.db.transaction(actor=actor) as conn:
            assert not conn.execute('SELECT * FROM bank_import_keys').fetchall()
            assert not conn.execute('SELECT * FROM bank_duplicate_decisions').fetchall()
        with pytest.raises(AccountingError):
            checks.overview(actor, c.book, view['source_id'])


def test_pg_sql_editor_cannot_manufacture_duplicate_clearance(business_case):
    c = business_case
    _, _, view, other, request = prepare(c)
    low, high = sorted((view['source_id'], other['id']))
    prints = {view['source_id']: view['fingerprint'], other['id']: other['fingerprint']}
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute('INSERT INTO bank_duplicate_decisions VALUES(?,?,?,?,?,?,?,?,?,?)', (low, high, c.book, c.owner.firm_id, prints[low], prints[high], request.expected_version, request.reason, c.finance.user_id, '2026-10-12T01:00:00+00:00'))
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute('INSERT INTO bank_duplicate_decisions VALUES(?,?,?,?,?,?,?,?,?,?)', (low, high, c.book, c.owner.firm_id, '0' * 64, prints[high], request.expected_version, request.reason, c.reviewer.user_id, '2026-10-12T01:00:00+00:00'))


def test_pg_hidden_same_company_book_cannot_duplicate_stable_identity(business_case):
    c = business_case
    c.service.import_sources(c.owner, c.book, statement())
    target = c.service.create_book(c.owner, BookCreate(name='同公司其他账套', period='2026-10', company_id=c.company))['id']
    # This actor has the new book only; existence guard reveals no hidden IDs.
    from app.accounting.models import BookMemberGrant
    from app.accounting.directory import DirectoryService
    DirectoryService(c.db).grant_book_member(c.owner, target, c.unassigned.user_id, BookMemberGrant(role='reviewer'))
    with pytest.raises(AccountingError, match='该企业同一银行交易已有记录') as failure:
        c.service.import_sources(c.unassigned, target, statement())
    assert c.book not in str(failure.value)
    assert not c.service.get_book(c.unassigned, target)['sources']
