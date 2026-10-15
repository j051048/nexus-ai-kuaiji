from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.accounting.domain import Actor, AccountingError
from app.accounting.models import CompanyMemberGrant, ApprovalRequest, ExportRequest
from app.business.models import BusinessEventCreate, HandoffRequest, SubmissionPolicyUpdate
from app.business.service import BusinessService
from test_business import business, payload, attach


def setup_direct(case):
    actor = Actor('boss-' + uuid4().hex, case.manager.firm_id)
    case.directory.register_identity(SimpleNamespace(user_id=actor.user_id, display_name='联调老板', email=f'{actor.user_id}@example.test'))
    case.directory.grant_company_member(case.manager, case.company['id'], CompanyMemberGrant(email=f'{actor.user_id}@example.test', role='owner'))
    case.service.submission.update(case.manager, case.company['id'], SubmissionPolicyUpdate(mode='direct', expected_version=0, reason='老板集中提交给会计'))
    case.actors['boss'] = actor
    return actor


def test_direct_submit_without_department_receive_review_export_and_idempotence(business):
    case = business
    # This scenario verifies the manual direct-submission approval boundary;
    # current OCR evidence guards are covered by the enabled-AI suites.
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute('UPDATE ai_company_settings SET enabled=0 WHERE firm_id=? AND company_id=?',
                     (case.manager.firm_id, case.company['id']))
    boss = setup_direct(case)
    row = case.service.create(boss, BusinessEventCreate(company_id=case.company['id'], template_id='reimbursement', payload=payload()))
    assert row['submission_mode']=='direct' and row['department_id'] is None
    row = attach(case,row,'boss')
    row = case.service.submit(boss,row['id'],row['version'])
    assert row['status']=='submitted' and not row['permissions']['department_approve']
    with pytest.raises(AccountingError):
        case.service.approve_department(case.actors['approver'],row['id'],row['version'])
    with pytest.raises(AccountingError):
        case.service.handoff(boss,row['id'],HandoffRequest(expected_version=row['version'],book_id=case.book['id'],idempotency_key='self-direct-key'))
    reviewer = case.actors['accountant']
    assert case.service.list_events(reviewer,scope='accounting')['total']==1
    request = HandoffRequest(expected_version=row['version'],book_id=case.book['id'],idempotency_key='direct-'+row['id'])
    result = case.service.handoff(reviewer,row['id'],request)
    assert result['draft_id']==case.service.handoff(reviewer,row['id'],request)['draft_id']
    draft = case.ledger.get_draft(reviewer,case.book['id'],result['draft_id'])
    with pytest.raises(AccountingError):
        case.ledger.export(reviewer,case.book['id'],ExportRequest(draft_ids=[draft['id']],idempotency_key=uuid4().hex))
    draft = case.ledger.approve(reviewer,case.book['id'],draft['id'],ApprovalRequest(expected_version=draft['version']))
    result = case.ledger.export(reviewer,case.book['id'],ExportRequest(draft_ids=[draft['id']],idempotency_key=uuid4().hex))
    assert result['count']==1


def test_policy_is_versioned_and_only_affects_new_events(business):
    case = business
    old = case.service.create(case.actors['employee'],BusinessEventCreate(company_id=case.company['id'],template_id='reimbursement',payload=payload()))
    setup_direct(case)
    assert case.service.get_event(case.actors['employee'],old['id'])['submission_mode']=='department'
    with pytest.raises(AccountingError):
        case.service.create(case.actors['employee'],BusinessEventCreate(company_id=case.company['id'],template_id='reimbursement',payload=payload()))
    with pytest.raises(AccountingError):
        case.service.submission.update(case.actors['employee'],case.company['id'],SubmissionPolicyUpdate(mode='department',expected_version=1,reason='无权变更'))
    with pytest.raises(AccountingError,match='已变化'):
        case.service.submission.update(case.manager,case.company['id'],SubmissionPolicyUpdate(mode='department',expected_version=0,reason='旧版本'))


def test_direct_pending_is_actionable_and_tenant_scoped(business):
    from app.workspace.service import WorkspaceService
    from app.accounting.review import ReviewService
    from app.cases.service import CaseService
    case = business
    boss = setup_direct(case)
    event = case.service.create(boss,BusinessEventCreate(company_id=case.company['id'],template_id='reimbursement',payload=payload()))
    event = attach(case,event,'boss')
    event = case.service.submit(boss,event['id'],event['version'])
    workspace = WorkspaceService(case.db,case.ledger,case.service,ReviewService(case.db,case.ledger),CaseService(case.db,case.service))
    tasks = workspace.list(case.actors['accountant'])['tasks']
    assert any(item['kind']=='handoff' and item['event_id']==event['id'] for item in tasks)
    case.directory.revoke_book_member(case.manager,case.book['id'],case.actors['accountant'].user_id)
    assert not any(item['event_id']==event['id'] for item in workspace.list(case.actors['accountant'])['tasks'])
    with pytest.raises(AccountingError):
        case.service.get_event(case.actors['accountant'],event['id'])


def test_direct_role_revocation_blocks_new_submission(business):
    case=business
    boss=setup_direct(case)
    event=case.service.create(boss,BusinessEventCreate(company_id=case.company['id'],template_id='reimbursement',payload=payload()))
    event=attach(case,event,'boss')
    case.directory.grant_company_member(case.manager,case.company['id'],CompanyMemberGrant(email=f'{boss.user_id}@example.test',role='employee',department_id=case.department['id']))
    assert not case.service.get_event(boss,event['id'])['permissions']['submit']
    with pytest.raises(AccountingError):
        case.service.submit(boss,event['id'],event['version'])
