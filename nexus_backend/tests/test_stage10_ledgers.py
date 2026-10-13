import json

import pytest

from app.accounting.catalog import CatalogService
from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import ApprovalRequest, BookCreate, DraftUpdate, ImportRequest, SourceRow
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService
from app.ledgers.models import AliasCreate, AliasDecision, EntityCreate, EntityUpdate, InheritanceActivate, ManagerGrant, PeriodCreate, PeriodLink
from app.ledgers.service import LedgersService
from app.learning.models import RuleEvaluate, RulePublish
from test_catalog import template


@pytest.fixture
def ledger_case(tmp_path):
    db = Database(tmp_path / 'ledgers.db')
    accounting = AccountingService(db)
    bootstrap(accounting, seed_examples=False)
    book = accounting.create_book(LOCAL_ACTOR, BookCreate(name='长期账套验证', period='2026-10'))
    ledgers = LedgersService(db)
    with db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        ledgers.ensure_book_in_transaction(conn, LOCAL_ACTOR, book)
        for person, role, grant in [('reviewer', 'reviewer', 'reviewer'), ('viewer', 'viewer', 'viewer'), ('revoked', 'reviewer', 'reviewer')]:
            conn.execute('INSERT INTO users(id,name) VALUES(?,?)', (person, person))
            conn.execute('INSERT INTO memberships VALUES(?,?,?)', (LOCAL_ACTOR.firm_id, person, role))
            conn.execute('INSERT INTO book_access VALUES(?,?,?,?)', (book['id'], LOCAL_ACTOR.firm_id, person, grant))
    return accounting, ledgers, book


def create_next(case, **changes):
    _, ledgers, book = case
    return ledgers.create_period(LOCAL_ACTOR, book['id'], PeriodCreate(period='2026-11', name='次月', source_book_id=book['id'], idempotency_key='next-period-001', **changes))


def test_new_period_keeps_old_ids_and_explicitly_selected_current_members(ledger_case):
    accounting, ledgers, book = ledger_case
    catalog = CatalogService(accounting.db)
    old_template = catalog.save_template(LOCAL_ACTOR, book['id'], template())
    created = create_next(ledger_case, inherit_user_ids=['viewer'])
    assert created['book_id'] != book['id']
    detail = ledgers.overview(LOCAL_ACTOR, created['book_id'])
    assert detail['id'] == ledgers.overview(LOCAL_ACTOR, book['id'])['id']
    assert [row['period'] for row in detail['periods']] == ['2026-10', '2026-11']
    assert catalog.get(LOCAL_ACTOR, created['book_id'])['templates'][0]['id'] != old_template['id']
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert dict(conn.execute('SELECT id,period FROM books WHERE id=?', (book['id'],)).fetchone()) == {'id': book['id'], 'period': '2026-10'}
        grants = [row['user_id'] for row in conn.execute('SELECT * FROM book_access WHERE book_id=?', (created['book_id'],))]
        assert set(grants) == {LOCAL_ACTOR.user_id, 'viewer'}


def test_revoked_or_inactive_member_cannot_be_resurrected(ledger_case):
    accounting, _, book = ledger_case
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute('DELETE FROM memberships WHERE user_id=?', ('revoked',))
    with pytest.raises(AccountingError, match='来源账套权限'):
        create_next(ledger_case, inherit_user_ids=['revoked'])
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert conn.execute("SELECT count(*) FROM books WHERE company_id=? AND period='2026-11'", (book['company_id'],)).fetchone()[0] == 0


def test_parent_never_grants_access_to_historical_period(ledger_case):
    _, ledgers, book = ledger_case
    created = create_next(ledger_case)
    reviewer = Actor('reviewer', LOCAL_ACTOR.firm_id)
    with ledgers.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute('DELETE FROM book_access WHERE user_id=?', ('reviewer',))
        conn.execute("INSERT INTO book_access VALUES(?,?,?,'reviewer')", (created['book_id'], LOCAL_ACTOR.firm_id, reviewer.user_id))
    ledgers.grant_manager(LOCAL_ACTOR, created['book_id'], ManagerGrant(user_id=reviewer.user_id, reason='明确授予跨期管理责任'))
    detail = ledgers.overview(reviewer, created['book_id'])
    assert [row['book_id'] for row in detail['periods']] == [created['book_id']]
    with pytest.raises(AccountingError):
        ledgers.overview(reviewer, book['id'])


def test_period_creation_is_atomic_and_idempotent(ledger_case):
    first = create_next(ledger_case)
    repeated = create_next(ledger_case)
    assert first['book_id'] == repeated['book_id'] and repeated['replayed']
    accounting, ledgers, book = ledger_case
    with pytest.raises(AccountingError, match='幂等键'):
        ledgers.create_period(LOCAL_ACTOR, book['id'], PeriodCreate(period='2026-12', name='次月', source_book_id=book['id'], idempotency_key='next-period-001'))
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert conn.execute('SELECT count(*) FROM books WHERE company_id=?', (book['company_id'],)).fetchone()[0] == 2


def test_link_requires_same_company_and_no_same_month_merge(ledger_case):
    accounting, ledgers, book = ledger_case
    other = accounting.create_book(LOCAL_ACTOR, BookCreate(name='另一个企业', period='2026-11'))
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        other_id = ledgers.ensure_book_in_transaction(conn, LOCAL_ACTOR, other)
    with pytest.raises(AccountingError, match='其他企业'):
        ledgers.link_period(LOCAL_ACTOR, book['id'], PeriodLink(source_book_id=other['id'], expected_ledger_id=other_id, reason='核对并关联旧期间'))
    old = accounting.create_book(LOCAL_ACTOR, BookCreate(name='旧月份', company_id=book['company_id'], period='2026-10'))
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        old_id = ledgers.ensure_book_in_transaction(conn, LOCAL_ACTOR, old)
    with pytest.raises(AccountingError, match='同月期间'):
        ledgers.link_period(LOCAL_ACTOR, book['id'], PeriodLink(source_book_id=old['id'], expected_ledger_id=old_id, reason='不可自动合并同月'))


def test_alias_needs_independent_review_and_resolution_preserves_original(ledger_case):
    _, ledgers, book = ledger_case
    entity = ledgers.create_entity(LOCAL_ACTOR, book['id'], EntityCreate(kind='counterparty', code='SUP-001', name='正式供应商有限公司'))
    alias = ledgers.create_alias(LOCAL_ACTOR, book['id'], AliasCreate(entity_id=entity['id'], original='供方简称', reason='合同编号核实为同一单位'))
    with ledgers.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert ledgers.resolve_in_transaction(conn, LOCAL_ACTOR, book['company_id'], 'counterparty', '供方简称', book['id']) is None
    decision = AliasDecision(expected_version=1, decision='approve', reason='独立核实原件主体名称')
    with pytest.raises(AccountingError, match='另一名会计'):
        ledgers.decide_alias(LOCAL_ACTOR, book['id'], alias['id'], decision)
    reviewer = Actor('reviewer', LOCAL_ACTOR.firm_id)
    ledgers.decide_alias(reviewer, book['id'], alias['id'], decision)
    with ledgers.db.transaction(actor=reviewer) as conn:
        value = ledgers.resolve_in_transaction(conn, reviewer, book['company_id'], 'counterparty', '供方简称', book['id'])
        assert value['original'] == '供方简称' and value['canonical_value'] == entity['name'] and value['entity_id'] == entity['id'] and value['version'] == '1:2'
    ledgers.update_entity(LOCAL_ACTOR, book['id'], entity['id'], EntityUpdate(expected_version=1, name=entity['name'], active=False, reason='停止使用并保留原处理依据'))
    with ledgers.db.transaction(actor=reviewer) as conn:
        assert ledgers.resolve_in_transaction(conn, reviewer, book['company_id'], 'counterparty', '供方简称', book['id']) is None


def test_alias_history_stale_version_and_foreign_entity_rejected(ledger_case):
    accounting, ledgers, book = ledger_case
    other = accounting.create_book(LOCAL_ACTOR, BookCreate(name='其他企业', period='2026-10'))
    entity = ledgers.create_entity(LOCAL_ACTOR, other['id'], EntityCreate(kind='project', code='PRJ', name='其他项目'))
    with pytest.raises(AccountingError, match='主数据不存在'):
        ledgers.create_alias(LOCAL_ACTOR, book['id'], AliasCreate(entity_id=entity['id'], original='同名项目', reason='不能跨企业猜测关联'))
    viewer = Actor('viewer', LOCAL_ACTOR.firm_id)
    with pytest.raises(AccountingError):
        ledgers.create_entity(viewer, book['id'], EntityCreate(kind='project', code='NO', name='越权项目'))


def test_old_books_with_same_company_are_not_automatically_merged(ledger_case):
    accounting, ledgers, book = ledger_case
    old = accounting.create_book(LOCAL_ACTOR, BookCreate(name='历史单独账套', company_id=book['company_id'], period='2026-09'))
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        ledgers.ensure_book_in_transaction(conn, LOCAL_ACTOR, old)
    assert ledgers.overview(LOCAL_ACTOR, book['id'])['id'] != ledgers.overview(LOCAL_ACTOR, old['id'])['id']
    old_ledger = ledgers.overview(LOCAL_ACTOR, old['id'])['id']
    ledgers.link_period(LOCAL_ACTOR, book['id'], PeriodLink(source_book_id=old['id'], expected_ledger_id=old_ledger, reason='逐一核对客户账套后显式关联'))
    assert len(ledgers.overview(LOCAL_ACTOR, book['id'])['periods']) == 2


def approved_sample(accounting, learning, book, month, external, counterparty, corrected=False):
    accounting.learning = learning
    accounting.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name='跨月测试', rows=[SourceRow(external_id=external, date=month + '-03', description='办公耗材', counterparty=counterparty, amount='13.00', direction='expense', evidence_ref='虚构已核验原件')]))
    accounting.suggestions(LOCAL_ACTOR, book)
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        draft_id = conn.execute('SELECT d.id FROM drafts d JOIN sources s ON s.id=d.source_id WHERE s.book_id=? AND s.external_id=?', (book, external)).fetchone()[0]
    draft = accounting.get_draft(LOCAL_ACTOR, book, draft_id)
    if corrected:
        lines = [{**line, 'account_code': '6602.99' if line['account_code'] != '1002' else '1002'} for line in draft['lines']]
        draft = accounting.update_draft(LOCAL_ACTOR, book, draft_id, DraftUpdate(expected_version=draft['version'], description='合同核实用途', lines=lines, correction_reason='真实业务合同用途核实'))
    accounting.approve(LOCAL_ACTOR, book, draft_id, ApprovalRequest(expected_version=draft['version']))
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        return conn.execute('SELECT id FROM learning_samples WHERE draft_id=?', (draft_id,)).fetchone()[0]


def test_cross_period_formula_is_candidate_only_and_requires_target_samples(ledger_case):
    accounting, ledgers, book = ledger_case
    learning = ledgers.learning
    positive = approved_sample(accounting, learning, book['id'], '2026-10', 'old-positive', '供方甲', True)
    negative = approved_sample(accounting, learning, book['id'], '2026-10', 'old-negative', '供方乙')
    rule = learning.overview(LOCAL_ACTOR, book['id'])['rules'][0]
    evaluation = learning.evaluate(LOCAL_ACTOR, book['id'], rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative]))
    learning.publish(LOCAL_ACTOR, book['id'], rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluation['id'], reason='本期真实正反例已核对'))
    target = create_next(ledger_case, inherit_rule_ids=[rule['id']])['book_id']
    candidate = ledgers.overview(LOCAL_ACTOR, target)['candidates'][0]
    assert candidate['status'] == 'pending' and candidate['target_rule_id'] is None
    assert learning.overview(LOCAL_ACTOR, target)['samples'] == [] and learning.overview(LOCAL_ACTOR, target)['rules'] == []
    with pytest.raises(AccountingError, match='样例不存在'):
        ledgers.activate_candidate(LOCAL_ACTOR, target, candidate['id'], InheritanceActivate(sample_ids=[positive], negative_sample_ids=[negative], reason='不能沿用旧期私有样例'))
    new_positive = approved_sample(accounting, learning, target, '2026-11', 'new-positive', '供方甲', True)
    new_negative = approved_sample(accounting, learning, target, '2026-11', 'new-negative', '供方乙')
    result = ledgers.activate_candidate(LOCAL_ACTOR, target, candidate['id'], InheritanceActivate(sample_ids=[new_positive], negative_sample_ids=[new_negative], reason='本期独立真实正反例已核实'))
    assert result['report']['passed'] and result['rule']['status'] == 'draft' and not result['published']
    assert ledgers.overview(LOCAL_ACTOR, target)['candidates'][0]['status'] == 'evaluated'


def test_master_history_cannot_be_overwritten_or_deleted(ledger_case):
    import sqlite3

    _, ledgers, book = ledger_case
    entity = ledgers.create_entity(LOCAL_ACTOR, book['id'], EntityCreate(kind='bank_account', code='BANK-001', name='企业主账户'))
    with ledgers.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE company_master_entities SET code='new',version=2 WHERE id=?", (entity['id'],))
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM company_master_history WHERE object_id=?', (entity['id'],))
