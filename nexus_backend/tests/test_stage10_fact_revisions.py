"""真实事务中的事实替代；所有附件和模型输出为明确的合成测试资料。"""
import json
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, BookCreate, BookMemberGrant, DraftUpdate
from app.revisions.models import RevisionSubmit, RevisionReview
from app.revisions.service import FactRevisionService
from test_intelligence_workflow import ai_case, create_event, extract, handoff, import_bank


def prepare(c, amount='100.00'):
    event = create_event(c, amount=amount)
    mapping = handoff(c, event)
    c.revisions = FactRevisionService(c.db, c.business, c.ledger)
    return event, mapping


def propose(c, event_id, actor=None, **payload_changes):
    actor = actor or c.actors['finance']
    current = c.business.get_event(actor, event_id)
    mapping = current['mapping']
    draft = c.ledger.get_draft(actor, mapping['book_id'], mapping['draft_id'])
    payload = dict(current['payload'], amount='80.00', **payload_changes)
    request = RevisionSubmit(expected_event_version=current['version'], expected_fact_version=current['fact_version'],
        expected_draft_version=draft['version'], target_book_id=mapping['book_id'], payload=payload,
        reason='原始费用清单金额为80元，申请人误录100元', idempotency_key=uuid4().hex)
    return c.revisions.submit(actor, event_id, request), request


def review_request(revision, decision='accept'):
    return RevisionReview(expected_version=revision['version'], decision=decision,
        reason='已独立核对原件和审批事实，确认修订依据', idempotency_key=uuid4().hex)


def test_replace_appends_source_and_draft_preserves_submitted_snapshot_and_originals(ai_case):
    c = ai_case; event, original = prepare(c)
    revision, request = propose(c, event['id'])
    assert c.revisions.submit(c.actors['finance'], event['id'], request)['id'] == revision['id']
    callback = []
    c.revisions.after_replace = lambda conn, actor, e, b, d: callback.append((e['fact_version'], b['id'], d['id']))
    command = review_request(revision)
    final = c.revisions.review(c.actors['accountant'], revision['id'], command)
    assert final['state'] == 'replaced' and final['lineage']['old_draft_id'] == original['draft_id']
    assert c.revisions.review(c.actors['accountant'], revision['id'], command)['state'] == 'replaced'
    assert len(callback) == 1
    current = c.business.get_event(c.actors['employee'], event['id'])
    assert current['payload']['amount'] == '80.00'
    assert current['fact_version'] == revision['expected_fact_version'] + 1
    assert current['versions'][-1]['payload']['amount'] == '100.00'
    assert current['attachments'] == original['event']['attachments']
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        old = conn.execute('SELECT * FROM sources WHERE id=?', (original['source_id'],)).fetchone()
        assert old['amount_cents'] == 10000
        assert c.revisions.source_inactive_in_transaction(conn, c.actors['accountant'], old)
        assert conn.execute('SELECT amount_cents FROM sources WHERE id=?', (final['lineage']['new_source_id'],)).fetchone()[0] == 8000
        assert conn.execute('SELECT COUNT(*) FROM business_mapping_versions WHERE event_id=?', (event['id'],)).fetchone()[0] == 2
        assert conn.execute('SELECT draft_id FROM business_mappings WHERE event_id=?', (event['id'],)).fetchone()[0] == final['lineage']['new_draft_id']
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_independent_review_cannot_be_bypassed_by_same_person_or_other_company(ai_case):
    c = ai_case; event, _ = prepare(c)
    revision, _ = propose(c, event['id'], c.manager)
    with pytest.raises(AccountingError, match='独立复核'):
        c.revisions.review(c.manager, revision['id'], review_request(revision))
    for name in ('employee', 'outsider', 'peer', 'finance'):
        with pytest.raises(AccountingError):
            c.revisions.review(c.actors[name], revision['id'], review_request(revision))
    assert c.revisions.list(c.actors['outsider'])['items'] == []


def test_pending_revision_blocks_native_approval_and_stale_proposal_can_be_rejected(ai_case):
    c = ai_case; event, mapping = prepare(c)
    revision, _ = propose(c, event['id'])
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        source = conn.execute('SELECT * FROM sources WHERE id=?', (mapping['source_id'],)).fetchone()
        assert c.revisions.blocking_issues_in_transaction(conn, c.actors['accountant'], source)
    with pytest.raises(Exception, match='pending fact revision'):
        with c.db.transaction(write=True, actor=c.actors['accountant']) as conn:
            conn.execute("UPDATE drafts SET status='approved',version=version+1 WHERE id=?", (mapping['draft_id'],))
    # A manual correction makes the proposal stale; rejecting it must remain
    # possible, otherwise the pending guard could permanently deadlock a book.
    with c.db.transaction(write=True, actor=c.actors['accountant']) as conn:
        conn.execute('UPDATE drafts SET version=version+1 WHERE id=?', (mapping['draft_id'],))
    with pytest.raises(AccountingError, match='变化'):
        c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    assert c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision, 'reject'))['state'] == 'rejected'


def test_approved_draft_produces_adjustment_without_changing_frozen_facts(ai_case):
    c = ai_case; event, mapping = prepare(c)
    # This isolates the revision boundary; financial evidence validation belongs
    # to the existing approval tests. A frozen reviewed draft is pre-existing.
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("UPDATE drafts SET status='approved',version=version+1,approved_by=?,approved_at='2026-10-12' WHERE id=?", (c.actors['accountant'].user_id, mapping['draft_id']))
    before = c.business.get_event(c.actors['finance'], event['id'])
    revision, _ = propose(c, event['id'])
    result = c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    assert result['state'] == 'adjustment_proposed' and result['lineage'] is None
    after = c.business.get_event(c.actors['finance'], event['id'])
    assert after['mapping'] == before['mapping'] and after['payload'] == before['payload']
    assert after['fact_version'] == before['fact_version']


def test_same_company_cross_period_replacement_needs_both_current_scopes(ai_case):
    c = ai_case; event, old = prepare(c)
    new = c.ledger.create_book(c.manager, BookCreate(name='同企业下月账套', company_id=c.company['id'], period='2026-11'))
    c.directory.grant_book_member(c.manager, new['id'], c.actors['finance'].user_id, BookMemberGrant(role='editor'))
    current = c.business.get_event(c.actors['finance'], event['id'])
    request = RevisionSubmit(expected_event_version=current['version'], expected_fact_version=current['fact_version'], expected_draft_version=1,
        target_book_id=new['id'], payload={**current['payload'], 'occurred_on': '2026-11-03'}, reason='票据日期实际属于次月，原月份录入错误', idempotency_key=uuid4().hex)
    revision = c.revisions.submit(c.actors['finance'], event['id'], request)
    with pytest.raises(AccountingError):
        c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    c.directory.grant_book_member(c.manager, new['id'], c.actors['accountant'].user_id, BookMemberGrant(role='reviewer'))
    final = c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    assert final['lineage']['new_book_id'] == new['id']
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert old['book_id'] in c.revisions.historical_books_in_transaction(conn, c.actors['accountant'], event['id'])


def test_linked_business_must_be_manually_unlinked_before_replacement(ai_case):
    c = ai_case; event = create_event(c, template='daily_cashflow', payment_status='paid')
    mapping = handoff(c, event); c.revisions = FactRevisionService(c.db, c.business)
    bank = import_bank(c)
    from app.intelligence.requests import BankLinkRequest
    c.intelligence.link_bank(c.actors['accountant'], event['id'], BankLinkRequest(bank_source_ids=[bank['id']], reason='已核对回单和业务原件，关联同一支出'))
    revision, _ = propose(c, event['id'])
    with pytest.raises(AccountingError, match='先人工解除'):
        c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))


def test_replaced_old_draft_and_lines_cannot_be_written_by_sql(ai_case):
    c = ai_case; event, old = prepare(c)
    revision, _ = propose(c, event['id'])
    c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    for sql in ('UPDATE drafts SET description=\'隐藏原事实\' WHERE id=?', 'DELETE FROM draft_lines WHERE draft_id=?'):
        with pytest.raises(Exception, match='superseded'):
            with c.db.transaction(write=True, actor=c.manager) as conn:
                conn.execute(sql, (old['draft_id'],))


def test_no_change_and_cross_company_and_forged_fact_versions_rejected(ai_case):
    c = ai_case; event, _ = prepare(c)
    current = c.business.get_event(c.actors['finance'], event['id'])
    base = RevisionSubmit(expected_event_version=current['version'], expected_fact_version=current['fact_version'], expected_draft_version=1,
        target_book_id=c.book['id'], payload=current['payload'], reason='真实事实未发生变化的申请必须拒绝', idempotency_key=uuid4().hex)
    with pytest.raises(AccountingError, match='相同'):
        c.revisions.submit(c.actors['finance'], event['id'], base)
    with pytest.raises(AccountingError):
        c.revisions.submit(c.manager, event['id'], base.model_copy(update={'target_book_id': c.other_book['id']}))
    with pytest.raises(AccountingError, match='版本'):
        c.revisions.submit(c.actors['finance'], event['id'], base.model_copy(update={'expected_fact_version': current['fact_version'] + 1}))


def test_original_case_must_finish_before_replacement_and_is_never_migrated(ai_case):
    from app.cases.models import CaseCreate, CaseCommand
    from app.cases.service import CaseService
    c = ai_case; event, original = prepare(c)
    cases = CaseService(c.db, c.business)
    task = cases.create(c.actors['accountant'], CaseCreate(event_id=event['id'], kind='exception',
        title='原期间金额异常', reason='先核查原票据和业务用途', idempotency_key=uuid4().hex))
    revision, _ = propose(c, event['id'])
    with pytest.raises(AccountingError, match='未关闭'):
        c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    assert c.revisions.get(c.actors['accountant'], revision['id'])['state'] == 'pending'
    cases.command(c.actors['accountant'], task['id'], 'close', CaseCommand(expected_version=task['version'],
        reason='已核实原异常，金额纠错交由独立事实修订审批', idempotency_key=uuid4().hex))
    final = c.revisions.review(c.actors['accountant'], revision['id'], review_request(revision))
    assert final['state'] == 'replaced'
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        old_task = conn.execute('SELECT * FROM case_tasks WHERE id=?', (task['id'],)).fetchone()
        assert old_task['state'] == 'closed' and old_task['book_id'] == original['book_id'] and old_task['draft_id'] == original['draft_id']
