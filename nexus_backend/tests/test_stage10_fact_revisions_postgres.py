"""原生 PostgreSQL 权限、历史外键、独立审批与不可变事实边界。"""
import json
from uuid import uuid4
import pytest
from app.accounting.models import ApprovalRequest, BookCreate, BookMemberGrant, ExportRequest
from app.accounting.domain import AccountingError
from app.revisions.models import RevisionSubmit
from app.revisions.service import FactRevisionService
from app.intelligence.requests import BankLinkRequest
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event, extract, handoff, import_bank, invoice_document
from test_stage10_fact_revisions import prepare, propose, review_request

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限 API 测试连接')


def test_pg_fact_replacement_keeps_mapping_registry_originals_and_forces_new_draft(pg_ai):
    c = pg_ai; event, original = prepare(c)
    revision, _ = propose(c, event['id'])
    command = review_request(revision)
    final = c.revisions.review(c.actors['accountant'], revision['id'], command)
    assert final['state'] == 'replaced' and final['lineage']['new_draft_id'] != original['draft_id']
    assert c.revisions.review(c.actors['accountant'], revision['id'], command)['id'] == revision['id']
    current = c.business.get_event(c.actors['finance'], event['id'])
    assert current['payload']['amount'] == '80.00' and current['versions'][-1]['payload']['amount'] == '100.00'
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT COUNT(*) FROM business_mapping_versions WHERE event_id=?', (event['id'],)).fetchone()[0] == 2
        old = conn.execute('SELECT * FROM sources WHERE id=?', (original['source_id'],)).fetchone()
        assert old['amount_cents'] == 10000 and c.revisions.is_source_inactive(conn, c.actors['accountant'], old)
        assert conn.execute('SELECT amount_cents FROM sources WHERE id=?', (final['lineage']['new_source_id'],)).fetchone()[0] == 8000


def test_pg_raw_insert_and_updates_cannot_forge_revision_or_independent_review(pg_ai):
    c = pg_ai; event, mapping = prepare(c)
    revision, _ = propose(c, event['id'], c.manager)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('UPDATE fact_revisions SET proposed_payload=? WHERE id=?', (json.dumps({'amount': '0.01'}), revision['id']))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute('SELECT accounting.review_fact_revision(?,?,?,?,?,?)', (revision['id'], 1, 'accept', '试图自行核验修订', uuid4().hex, 'a'*64))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.actors['finance']) as conn:
            conn.execute('UPDATE business_mappings SET source_id=? WHERE event_id=?', (mapping['source_id'], event['id']))
    with c.psycopg.connect(ADMIN_URL) as conn:
        for name in ('fact_revisions', 'fact_revision_decisions', 'fact_revision_lineage', 'business_mapping_versions'):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ('accounting.'+name,)).fetchone() == (True, True)
            assert not conn.execute('SELECT has_table_privilege(%s,%s,%s)', ('accounting_worker', 'accounting.'+name, 'SELECT')).fetchone()[0]


def test_pg_current_permission_revocation_hides_receipts_and_native_scope_is_not_oracle(pg_ai):
    c = pg_ai; event, mapping = prepare(c)
    revision, _ = propose(c, event['id'])
    with c.db.transaction(actor=c.actors['outsider']) as conn:
        assert conn.execute('SELECT * FROM fact_revisions WHERE id=?', (revision['id'],)).fetchall() == []
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(actor=c.actors['outsider']) as conn:
            conn.execute('SELECT accounting.revision_source_inactive(?,?)', (mapping['book_id'], mapping['source_id']))
    c.directory.revoke_book_member(c.manager, c.book['id'], c.actors['accountant'].user_id)
    with pytest.raises(AccountingError):
        c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))


@pytest.mark.parametrize('changes', [{'amount': 80}, {'direction': 'income'}, {'occurred_on': '2026-02-31'}, {'project': 'x'*201}, {'hidden': 'ignored'}])
def test_pg_native_submission_rejects_invalid_business_facts_beyond_python_validation(pg_ai, changes):
    c = pg_ai; event, _ = prepare(c)
    current = c.business.get_event(c.actors['finance'], event['id'])
    payload = {**current['payload'], 'amount': '80.00', **changes}
    with pytest.raises((c.psycopg.errors.CheckViolation, c.psycopg.errors.DatetimeFieldOverflow)):
        with c.db.transaction(write=True, actor=c.actors['finance']) as conn:
            conn.execute('SELECT accounting.submit_fact_revision(?,?,?,?,?,?,?,?,?,?)', (event['id'], c.book['id'], current['version'], current['fact_version'], 1,
                json.dumps(payload), '数据库还须校验精确事实契约', uuid4().hex, 'b'*64, uuid4().hex))


def test_pg_cross_period_old_book_only_gets_pinned_history_after_new_scope_revoked(pg_ai):
    c = pg_ai; event, original = prepare(c)
    new = c.ledger.create_book(c.manager, BookCreate(name='真实迁移测试次月', company_id=c.company['id'], period='2026-11'))
    c.directory.grant_book_member(c.manager, new['id'], c.actors['finance'].user_id, BookMemberGrant(role='editor'))
    c.directory.grant_book_member(c.manager, new['id'], c.actors['accountant'].user_id, BookMemberGrant(role='reviewer'))
    current = c.business.get_event(c.actors['finance'], event['id'])
    request = RevisionSubmit(expected_event_version=current['version'], expected_fact_version=current['fact_version'], expected_draft_version=c.ledger.get_draft(c.actors['finance'], c.book['id'], original['draft_id'])['version'],
        target_book_id=new['id'], payload={**current['payload'], 'occurred_on':'2026-11-03'}, reason='确认原票据属于次月，保留旧账套追溯', idempotency_key=uuid4().hex)
    revision = c.revisions.submit(c.actors['finance'], event['id'], request)
    c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    c.directory.revoke_book_member(c.manager, new['id'], c.actors['accountant'].user_id)
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT id FROM business_attachments WHERE event_id=?', (event['id'],)).fetchall() == []
        assert conn.execute('SELECT * FROM business_mappings WHERE event_id=?', (event['id'],)).fetchall() == []
        assert conn.execute('SELECT book_id FROM business_mapping_versions WHERE event_id=?', (event['id'],)).fetchone()[0] == original['book_id']
        pinned = json.loads(conn.execute('SELECT accounting.revision_source_history(?,?)', (original['book_id'], original['source_id'])).fetchone()[0])
        assert pinned['payload']['occurred_on'] == '2026-10-03'
        assert pinned['attachments'][0]['id'] == event['attachments'][0]['id']


def test_pg_existing_bank_match_must_not_survive_revised_business_amount(pg_ai):
    c = pg_ai; event = create_event(c, template='daily_cashflow', payment_status='paid'); handoff(c, event)
    c.revisions = FactRevisionService(c.db, c.business)
    bank = import_bank(c)
    c.intelligence.link_bank(c.actors['accountant'], event['id'], BankLinkRequest(bank_source_ids=[bank['id']], reason='已核对银行业务相同交易'))
    revision, _ = propose(c, event['id'])
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.actors['accountant']) as conn:
            conn.execute('SELECT accounting.review_fact_revision(?,?,?,?,?,?)', (revision['id'], 1, 'accept', '仍有旧流水关联不得接受', uuid4().hex, 'c'*64))


def test_pg_exported_revision_produces_adjustment_and_leaves_ledger_frozen(pg_ai):
    c = pg_ai; c.provider.document = invoice_document(amount='100.00', net_amount='100.00', buyer_tax_id=c.provider.document['buyer_tax_id'])
    event = create_event(c, amount='100.00'); extract(c, event); mapping = handoff(c, event)
    c.revisions = FactRevisionService(c.db, c.business)
    c.ledger.approve(c.actors['accountant'], c.book['id'], mapping['draft_id'], ApprovalRequest(expected_version=c.ledger.get_draft(c.actors['accountant'], c.book['id'], mapping['draft_id'])['version']))
    c.ledger.export(c.actors['accountant'], c.book['id'], ExportRequest(draft_ids=[mapping['draft_id']], idempotency_key=uuid4().hex))
    revision, _ = propose(c, event['id'])
    final = c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    assert final['state'] == 'adjustment_proposed' and final['lineage'] is None
    current = c.business.get_event(c.actors['finance'], event['id'])
    assert current['payload']['amount'] == '100.00' and current['mapping']['draft_id'] == mapping['draft_id']


def test_pg_native_case_guard_rolls_back_replacement_until_original_exception_closed(pg_ai):
    from app.cases.models import CaseCreate, CaseCommand
    from app.cases.service import CaseService
    c = pg_ai; event, original = prepare(c)
    cases = CaseService(c.db, c.business)
    task = cases.create(c.actors['accountant'], CaseCreate(event_id=event['id'], kind='exception',
        title='原账套异常须先处理', reason='固定原期间来源不能静默迁移', idempotency_key=uuid4().hex))
    revision, _ = propose(c, event['id'])
    with pytest.raises(c.psycopg.errors.CheckViolation, match='Original active'):
        with c.db.transaction(write=True, actor=c.actors['accountant']) as conn:
            conn.execute('SELECT accounting.review_fact_revision(?,?,?,?,?,?)', (revision['id'],1,'accept','须先关闭原异常任务',uuid4().hex,'d'*64))
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT state FROM fact_revisions WHERE id=?',(revision['id'],)).fetchone()[0]=='pending'
        assert conn.execute('SELECT COUNT(*) FROM sources WHERE book_id=?',(original['book_id'],)).fetchone()[0]==1
        assert conn.execute('SELECT * FROM fact_revision_decisions WHERE revision_id=?',(revision['id'],)).fetchall()==[]
    cases.command(c.actors['accountant'],task['id'],'close',CaseCommand(expected_version=task['version'],
        reason='核实异常后交由事实修订流程独立审批',idempotency_key=uuid4().hex))
    assert c.revisions.review(c.actors['accountant'],revision['id'],review_request(revision))['state']=='replaced'
