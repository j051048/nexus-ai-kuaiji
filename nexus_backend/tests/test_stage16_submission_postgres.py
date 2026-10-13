from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventCreate, HandoffRequest, SubmissionPolicyUpdate
from app.business.service import BusinessService
from test_business import payload
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason='需要隔离 PostgreSQL')


def direct_event(case):
    service = BusinessService(case.db)
    boss = case.person(company_role='owner')
    service.submission.update(case.owner,case.company,SubmissionPolicyUpdate(mode='direct',expected_version=0,reason='老板直接提供资料'))
    event = service.create(boss,BusinessEventCreate(company_id=case.company,template_id='reimbursement',payload=payload()))
    event = service.add_attachment(boss,event['id'],event['version'],{'id':uuid4().hex,'storage_key':uuid4().hex,'filename':'合成.pdf','media_type':'application/pdf','sha256':'a'*64,'size_bytes':40})
    return service,boss,service.submit(boss,event['id'],event['version'])


def test_pg_direct_receiver_mapping_and_raw_sql_self_bypass_are_guarded(business_case):
    case=business_case
    service,boss,event=direct_event(case)
    assert event['department_id'] is None
    assert service.get_event(case.reviewer,event['id'])['permissions']['handoff']
    with case.db.transaction(write=True,actor=boss) as conn:
        assert conn.execute("UPDATE business_events SET status='finance_approved',version=version+1 WHERE id=?",(event['id'],)).rowcount==0
    with pytest.raises(Exception):
        with case.db.transaction(write=True,actor=case.reviewer) as conn:
            conn.execute("UPDATE business_events SET submission_mode='department' WHERE id=?",(event['id'],))
    with pytest.raises(Exception):
        with case.db.transaction(write=True,actor=case.reviewer) as conn:
            conn.execute("UPDATE business_events SET payload=jsonb_set(payload::jsonb,'{amount}','\"999.99\"')::text WHERE id=?",(event['id'],))
    with case.db.transaction(actor=case.unassigned) as conn:
        assert conn.execute('SELECT count(*) FROM business_events WHERE id=?',(event['id'],)).fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM business_attachments WHERE event_id=?',(event['id'],)).fetchone()[0]==0
    result=service.handoff(case.reviewer,event['id'],HandoffRequest(expected_version=event['version'],book_id=case.book,idempotency_key=uuid4().hex))
    assert result['event']['status']=='handed_off'
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute('SELECT count(*) FROM business_actions WHERE event_id=? AND stage=?',(event['id'],'department')).fetchone()[0]==0


def test_pg_submission_policy_cannot_cross_firm_or_grant_employee_direct_entry(business_case):
    case=business_case
    service,boss,event=direct_event(case)
    with pytest.raises(AccountingError):
        service.submission.update(case.employee,case.company,SubmissionPolicyUpdate(mode='department',expected_version=1,reason='无权变更'))
    with pytest.raises(AccountingError):
        service.create(case.employee,BusinessEventCreate(company_id=case.company,template_id='reimbursement',payload=payload()))
    with pytest.raises(AccountingError):
        service.handoff(case.reviewer,event['id'],HandoffRequest(expected_version=event['version'],book_id=case.other_book,idempotency_key=uuid4().hex))
