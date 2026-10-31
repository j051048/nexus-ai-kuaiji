"""Role-aware tasks are projected after current grants, before pagination."""
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, BookMemberGrant, ExportRequest, ImportRequest, SourceRow
from app.accounting.review import ReviewService
from app.cases.models import CaseAssign, CaseCommand, CaseCreate, CaseRespond
from app.cases.service import CaseService
from app.delivery.models import ReceiptCreate
from app.delivery.service import DeliveryService
from app.workspace.service import WorkspaceService
from test_business import business, approved, create, handoff, submitted
from test_intelligence_workflow import ai_case, create_event
from test_queue_workflow import queue_case, enqueue


def workspace(c):
    return WorkspaceService(c.db, c.ledger, c.service, ReviewService(c.db, c.ledger), CaseService(c.db, c.service))


def bank(c, actor=None, book=None):
    actor, book = actor or c.actors['accountant'], book or c.book['id']
    c.ledger.import_sources(actor, book, ImportRequest(source_name='合成工作空间', rows=[SourceRow(external_id=uuid4().hex, date='2026-10-03', description='办公用品', counterparty='合成供应商', amount='113.00', direction='expense', evidence_ref='合成归档资料')]))
    c.ledger.suggestions(actor, book)
    return c.ledger.get_book(actor, book)['drafts'][0]


def command(row, cls=CaseCommand, **changes):
    return cls(expected_version=row['version'], idempotency_key=uuid4().hex, reason='已核对当前合成业务', **changes)


def test_workspace_profile_roles_do_not_inherit_other_departments_or_ledger(business):
    c, service = business, workspace(business)
    for name, expected in [('employee', 'employee'), ('approver', 'approver'), ('finance', 'finance'), ('accountant', 'accountant')]:
        value = service.profile(c.actors[name])
        assert value['role'] == expected
        assert [row['id'] for row in value['companies']] == [c.company['id']]
        assert value['can_review'] == (name == 'accountant')
    assert service.profile(c.actors['employee'])['book_count'] == 0
    assert service.profile(c.actors['approver'])['can_approve'] is True
    assert service.profile(c.actors['finance'])['can_finance'] is True


def test_workspace_person_department_company_independent_approval(business):
    c, service = business, workspace(business)
    own = submitted(c)
    peer = submitted(c, actor='peer')
    outside = submitted(c, actor='other_employee')
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute('DELETE FROM department_approvers WHERE department_id=?', (c.other_department['id'],))
    employee = service.list(c.actors['employee'], bucket='all')
    assert {(row['kind'], row['ref']) for row in employee['tasks']} == {('business', own['id'])}
    assert employee['tasks'][0]['bucket'] == 'waiting'
    approvals = service.list(c.actors['approver'], kind='approval')['tasks']
    assert {row['ref'] for row in approvals} == {own['id'], peer['id']}
    assert {row['title'] for row in approvals} == {own['payload']['description']}
    assert outside['id'] not in {row['ref'] for row in approvals}
    c.service.grant_department_approver(c.manager, c.company['id'], c.department['id'], 'peer')
    self_event = submitted(c, actor='approver')
    assert self_event['id'] not in {row['ref'] for row in service.list(c.actors['approver'], kind='approval')['tasks']}
    c.service.approve_department(c.actors['approver'], own['id'], own['version'])
    assert service.get(c.actors['finance'], 'finance', own['id'])['action_label'] == '核验资料'
    with pytest.raises(AccountingError) as unavailable:
        service.get(c.actors['approver'], 'approval', own['id'])
    assert unavailable.value.status == 404
    assert service.list(c.manager, kind='business', bucket='all')['pagination']['total'] == 0


def test_workspace_authorizes_before_count_and_limit_and_filters_private_rows(business):
    c, service = business, workspace(business)
    hidden = [create(c, actor='peer', description=f'私密业务-{i}') for i in range(6)]
    visible = [create(c, description=f'本人业务-{i}') for i in range(3)]
    observed = []
    for offset in range(3):
        result = service.list(c.actors['employee'], bucket='all', limit=1, offset=offset)
        assert result['pagination']['total'] == 3
        assert result['pagination']['has_more'] is (offset < 2)
        observed.extend(row['ref'] for row in result['tasks'])
        assert not any(row['id'] in json.dumps(result) for row in hidden)
        assert '私密业务' not in json.dumps(result, ensure_ascii=False)
    assert set(observed) == {row['id'] for row in visible}
    assert service.list(c.actors['employee'], company_id=c.other['id'], bucket='all')['pagination']['total'] == 0
    assert service.list(c.actors['employee'], offset=100, bucket='all')['tasks'] == []


def test_workspace_private_supplement_recipient_never_gets_ledger_or_internal_title(business):
    c, service = business, workspace(business)
    event = approved(c); mapping = handoff(c, event)
    task = service.cases.create(c.actors['accountant'], CaseCreate(event_id=event['id'], kind='supplement', title='绝不可泄漏的内部处理标签', reason='请补充业务用途', idempotency_key=uuid4().hex))
    result = service.get(c.actors['employee'], 'case', task['id'])
    assert result['title'] == '补充业务资料' and result['bucket'] == 'action'
    assert all(result[key] is None for key in ('book_id', 'draft_id', 'event_id', 'batch_id'))
    assert not any(value in json.dumps(result) for value in (mapping['book_id'], mapping['draft_id'], mapping['source_id'], '绝不可泄漏的内部处理标签'))
    for name in ('peer', 'approver', 'other_employee'):
        with pytest.raises(AccountingError) as hidden:
            service.get(c.actors[name], 'case', task['id'])
        assert hidden.value.status == 404
    assert service.list(c.actors['accountant'], kind='review')['tasks'] == []
    task = service.cases.command(c.actors['employee'], task['id'], 'respond', command(task, CaseRespond))
    assert service.get(c.actors['employee'], 'case', task['id'])['bucket'] == 'waiting'
    assert service.get(c.actors['accountant'], 'case', task['id'])['bucket'] == 'action'


def test_workspace_responder_cannot_be_assigned_own_response_review(business):
    c, service = business, workspace(business)
    draft = bank(c)
    task = service.cases.create(c.actors['accountant'], CaseCreate(book_id=c.book['id'], draft_id=draft['id'], title='内部银行异常', reason='请财务补充支付用途', idempotency_key=uuid4().hex))
    task = service.cases.command(c.actors['accountant'], task['id'], 'assign', command(task, CaseAssign, assignee_id='finance'))
    task = service.cases.command(c.actors['finance'], task['id'], 'respond', command(task, CaseRespond))
    assert service.get(c.actors['finance'], 'case', task['id'])['bucket'] == 'waiting'


@pytest.mark.parametrize('change', ['employee_membership', 'reviewer_book', 'suspended_client', 'inactive_user'])
def test_workspace_revocation_and_suspension_are_immediate(business, change):
    c, service = business, workspace(business)
    event, draft = create(c), bank(c)
    actor, kind, ref = (c.actors['employee'], 'business', event['id']) if change == 'employee_membership' else (c.actors['accountant'], 'review', draft['id'])
    assert service.get(actor, kind, ref)['ref'] == ref
    with c.db.transaction(write=True, actor=c.manager) as conn:
        if change == 'employee_membership':
            conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id='employee'", (c.company['id'],))
        elif change == 'reviewer_book':
            conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id='accountant'", (c.book['id'],))
        elif change == 'suspended_client':
            conn.execute("UPDATE firm_clients SET status='suspended' WHERE firm_id=? AND company_id=?", (actor.firm_id, c.company['id']))
        else:
            conn.execute('UPDATE users SET active=0 WHERE id=?', (actor.user_id,))
    with pytest.raises(AccountingError) as blocked:
        service.get(actor, kind, ref)
    assert blocked.value.status == (403 if change == 'inactive_user' else 404)


def test_workspace_review_does_not_export_and_delivery_requires_complete_receipt(business):
    c, service = business, workspace(business)
    actor, book = c.actors['accountant'], c.book['id']
    draft = bank(c, actor=c.manager)
    c.ledger.approve(actor, book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    with pytest.raises(AccountingError) as completed:
        service.get(actor, 'review', draft['id'])
    assert completed.value.status == 404
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute('SELECT count(*) FROM exports WHERE book_id=?', (book,)).fetchone()[0] == 0
    assert service.get(actor, 'export', book)['title'].endswith('2026-10 · 待导出凭证 · 1 张')
    assert service.list(c.actors['employee'], kind='export')['tasks'] == []
    exported = c.ledger.export(actor, book, ExportRequest(draft_ids=[draft['id']], idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError) as delivered:
        service.get(actor, 'export', book)
    assert delivered.value.status == 404
    assert service.get(actor, 'delivery', exported['id'])['state'] == 'unverified'
    delivery = DeliveryService(c.db)
    facts = json.loads(delivery.template(actor, book, exported['id']))
    facts['items'][0].update(status='unknown', external_voucher_id=None)
    delivery.create_receipt(actor, book, exported['id'], ReceiptCreate(**facts, reason='仅收到客户未知结果', idempotency_key=uuid4().hex))
    assert service.get(actor, 'delivery', exported['id'])['state'] == 'unverified'
    facts['items'][0].update(status='accepted', external_voucher_id='虚构客户凭证001')
    delivery.create_receipt(actor, book, exported['id'], ReceiptCreate(**facts, reason='合成明细对照核验', idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError) as verified:
        service.get(actor, 'delivery', exported['id'])
    assert verified.value.status == 404


@pytest.mark.parametrize('changes', [{'bucket': 'bad'}, {'kind': 'bad'}, {'limit': 0}, {'offset': -1}])
def test_workspace_rejects_unknown_filters(business, changes):
    with pytest.raises(AccountingError):
        workspace(business).list(business.actors['employee'], **changes)


def test_workspace_ocr_follows_fact_version_across_approval_status_changes(queue_case):
    from app.business.models import BusinessEventUpdate, ReturnRequest
    c = queue_case
    service = WorkspaceService(c.db, c.ledger, c.business, ReviewService(c.db, c.ledger), CaseService(c.db, c.business))
    event = create_event(c)
    batch = enqueue(c, [event])
    job = batch['jobs'][0]
    original_version = event['version']
    event = c.business.submit(c.actors['employee'], event['id'], event['version'])
    assert event['version'] > original_version
    assert service.get(c.actors['employee'], 'ocr', job['id'])['bucket'] == 'waiting'
    event = c.business.return_event(c.actors['approver'], event['id'], ReturnRequest(expected_version=event['version'], reason='请核对实际报销金额'))
    changed = {**event['payload'], 'amount': '0.31'}
    c.business.update(c.actors['employee'], event['id'], BusinessEventUpdate(expected_version=event['version'], payload=changed))
    with pytest.raises(AccountingError) as outdated:
        service.get(c.actors['employee'], 'ocr', job['id'])
    assert outdated.value.status == 404 and c.provider.extract_calls == 0


def test_company_finance_keeps_primary_workflow_with_additional_book_review(business):
    c, service = business, workspace(business)
    actor = c.actors['finance']
    with pytest.raises(AccountingError):
        c.directory.grant_book_member(c.manager, c.book['id'], actor.user_id, BookMemberGrant(role='reviewer'))
    profile = service.profile(actor)
    assert profile['role'] == 'finance'
    assert profile['can_finance'] and not profile['can_review'] and profile['can_accounting']


def test_legacy_closed_approved_record_is_not_an_actionable_export(business):
    from period_fixtures import restore_period_state
    c, service = business, workspace(business)
    actor, book = c.actors['accountant'], c.book['id']
    draft = bank(c, actor=c.manager)
    c.ledger.approve(actor, book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    assert service.get(actor, 'export', book)['bucket'] == 'action'
    # Explicit test-only historical restoration, not a production close bypass.
    restore_period_state(c.db, book)
    assert service.list(actor, kind='export')['tasks'] == []
    assert c.ledger.get_draft(actor, book, draft['id'])['status'] == 'approved'
