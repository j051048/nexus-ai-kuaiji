"""Native role and provenance checks independent of HTTP validation."""
import json

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import AliasCreate, AliasDecision, EntityCreate, ManagerGrant, PeriodCreate
from app.ledgers.service import LedgersService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限运行账号')


def mapped(case):
    service = LedgersService(case.db)
    with case.db.transaction(write=True, actor=case.owner) as conn:
        book = case.service.access.book(conn, case.owner, case.book)
        service.ensure_book_in_transaction(conn, case.owner, book)
    return service


def test_native_longterm_tables_force_rls_without_worker_access(business_case):
    case = business_case
    tables = ['ledgers', 'ledger_periods', 'ledger_managers', 'ledger_commands', 'ledger_rule_candidates', 'company_master_entities', 'company_master_aliases', 'company_master_history']
    with case.psycopg.connect(ADMIN_URL) as conn:
        rows = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND relname=ANY(%s)", (tables,)).fetchall()
        assert len(rows) == 8 and all(enabled and forced for _, enabled, forced in rows)
        for table in tables:
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + table,)).fetchone()[0]
        assert not conn.execute("SELECT has_function_privilege('accounting_worker','accounting.ledger_create_period(text,text,text,text,text,jsonb)','EXECUTE')").fetchone()[0]


def test_parent_manager_does_not_see_unassigned_previous_month_sql(business_case):
    case = business_case
    service = mapped(case)
    target = service.create_period(case.owner, case.book, PeriodCreate(period='2026-11', name='独立次月', source_book_id=case.book, idempotency_key='pg-longterm-next-1'))['book_id']
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("INSERT INTO book_access VALUES(?,?,?,'reviewer')", (target, case.owner.firm_id, case.reviewer.user_id))
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?', (case.book, case.reviewer.user_id))
    service.grant_manager(case.owner, target, ManagerGrant(user_id=case.reviewer.user_id, reason='只管理明确授权的新月份'))
    assert [item['book_id'] for item in service.overview(case.reviewer, target)['periods']] == [target]
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute('SELECT * FROM ledger_periods WHERE book_id=?', (case.book,)).fetchall() == []
        assert conn.execute('SELECT * FROM sources WHERE book_id=?', (case.book,)).fetchall() == []


def test_native_period_creation_rejects_missing_explicit_manager(business_case):
    case = business_case
    service = mapped(case)
    ledger = service.overview(case.owner, case.book)['id']
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            conn.execute("SELECT accounting.ledger_create_period(?,? ,?,'2026-11','越权创建','[]'::jsonb)", (ledger, 'forged-period-' + case.book, case.book))


def test_native_period_creation_cannot_resurrect_revoked_user(business_case):
    case = business_case
    service = mapped(case)
    ledger = service.overview(case.owner, case.book)['id']
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute('DELETE FROM book_access WHERE book_id=? AND user_id=?', (case.book, case.viewer.user_id))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege, match='current source access'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("SELECT accounting.ledger_create_period(?,? ,?,'2026-11','撤权不可恢复',?::jsonb)", (ledger, 'revoked-period-' + case.book, case.book, json.dumps([case.viewer.user_id])))


def test_employee_cannot_enumerate_master_records_but_can_resolve_exact_approved_name(business_case):
    case = business_case
    service = mapped(case)
    entity = service.create_entity(case.owner, case.book, EntityCreate(kind='counterparty', code='SUP-1', name='主数据供方'))
    alias = service.create_alias(case.owner, case.book, AliasCreate(entity_id=entity['id'], original='简称供方', reason='合同及纳税主体核实'))
    service.decide_alias(case.reviewer, case.book, alias['id'], AliasDecision(expected_version=1, decision='approve', reason='独立核验票据及合同'))
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute('SELECT * FROM company_master_entities').fetchall() == []
        assert conn.execute('SELECT * FROM company_master_aliases').fetchall() == []
        result = service.resolve_in_transaction(conn, case.employee, case.company, 'counterparty', '简称供方')
        assert result['entity_id'] == entity['id'] and result['original'] == '简称供方'
        assert conn.execute('SELECT * FROM accounting.master_resolve(?,?,?)', (case.other_company, 'counterparty', '简称供方')).fetchall() == []


def test_sql_alias_self_approval_and_preapproved_insert_rejected(business_case):
    case = business_case
    service = mapped(case)
    entity = service.create_entity(case.owner, case.book, EntityCreate(kind='project', code='PRJ-1', name='真实项目'))
    alias = service.create_alias(case.owner, case.book, AliasCreate(entity_id=entity['id'], original='项目别名', reason='项目合同主体核实'))
    with pytest.raises(case.psycopg.Error):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE company_master_aliases SET status='approved',version=2,reviewed_by=?,review_reason='伪造审批',reviewed_at='now' WHERE id=?", (case.owner.user_id, alias['id']))
    with pytest.raises(case.psycopg.Error, match='independent review'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("INSERT INTO company_master_aliases(id,firm_id,company_id,kind,entity_id,original,reason,recorded_book_id,created_by,created_at,status,reviewed_by) VALUES(?,?,?,'project',?,'跳过待审','伪造历史',?,?,'now','approved',?)", ('fake-alias-' + case.book, case.owner.firm_id, case.company, entity['id'], case.book, case.owner.user_id, case.reviewer.user_id))


def test_master_identity_history_and_cross_company_members_are_sql_protected(business_case):
    case = business_case
    service = mapped(case)
    entity = service.create_entity(case.owner, case.book, EntityCreate(kind='bank_account', code='BANK-1', name='企业账户'))
    with pytest.raises(case.psycopg.Error, match='immutable'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE company_master_entities SET company_id=?,version=2 WHERE id=?", (case.other_company, entity['id']))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.employee) as conn:
            conn.execute("INSERT INTO company_master_entities(id,firm_id,company_id,kind,code,name,recorded_book_id,created_by,created_at) VALUES(?,?,?,'project','NO','越权',?,?,'now')", ('forged-master-' + case.book, case.owner.firm_id, case.company, case.book, case.employee.user_id))
