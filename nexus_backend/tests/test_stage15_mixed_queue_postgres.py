"""All native dispatcher entrypoints share chat capacity and company exclusion.

Three distinct companies distinguish the global cap from a company lock.
Only this fixture's immutable synthetic history is cancelled on teardown.
"""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.models import BookCreate,BookMemberGrant,CompanyCreate,CompanyMemberGrant,DepartmentCreate
from app.assistant.models import ConversationCreate,TurnCreate
from app.business.models import BusinessEventCreate,HandoffRequest
from app.business.storage import AttachmentStore
from app.intelligence.requests import OCRRequest
from app.intelligence.service import IntelligenceService
from app.jobs.requests import BatchRequest
from app.jobs.service import QueueService
from test_business_api import attachment_bytes
from test_business_postgres import ADMIN_URL,APP_URL,business_case,business_postgres
from test_stage15_conversations_postgres import WORKER_URL,chat_pg
from test_intelligence_workflow import FakeProvider
from pg_queue_clock import wait_until_due

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL,reason='需要专用 PostgreSQL 管理员、受限 API 和实际 worker 登录')


def raw_running(c):
    with c.raw.psycopg.connect(ADMIN_URL) as conn:
        rows=conn.execute("SELECT 'assistant',firm_id,company_id,id FROM accounting.assistant_turns WHERE status='running' UNION ALL SELECT 'ocr',firm_id,company_id,id FROM accounting.ocr_jobs WHERE status='running' UNION ALL SELECT 'proposal',firm_id,company_id,id FROM accounting.proposal_jobs WHERE status='running'").fetchall()
    return rows


@pytest.fixture
def mixed_pg(chat_pg,tmp_path):
    chat=chat_pg;raw=chat.source
    directory=DirectoryService(raw.db)
    storage=AttachmentStore(tmp_path/'mixed-private-originals')
    provider=FakeProvider()
    intelligence=IntelligenceService(raw.db,storage,provider)
    queue=QueueService(raw.db,intelligence)
    worker_intelligence=IntelligenceService(chat.worker.db,storage,provider)
    worker_queue=QueueService(chat.worker.db,worker_intelligence)
    companies=[]
    for number in range(3):
        if number==0:
            company,book=raw.company,raw.book
            applicant,approver,finance=raw.employee,raw.approver,raw.finance
        else:
            if number==1:
                company,book=raw.other_company,raw.other_book
            else:
                company=directory.create_company(raw.owner,CompanyCreate(name='混合队列第三合成企业'))['id']
                book=raw.service.create_book(raw.owner,BookCreate(name='混合队列第三账套',period='2026-10',company_id=company))['id']
            department=directory.create_department(raw.owner,company,DepartmentCreate(name='合成业务部门'))['id']
            actors=[]
            for role in ('employee','employee','finance'):
                identity=SimpleNamespace(user_id='mixed-'+uuid4().hex,display_name='混合队列合成人员',email=uuid4().hex+'@example.test')
                directory.register_identity(identity)
                directory.grant_company_member(raw.owner,company,CompanyMemberGrant(email=identity.email,role=role,department_id=department))
                from app.accounting.domain import Actor
                actors.append(Actor(identity.user_id,raw.owner.firm_id))
            applicant,approver,finance=actors
            directory.grant_book_member(raw.owner,book,finance.user_id,BookMemberGrant(role='editor'))
            intelligence.business.grant_department_approver(raw.owner,company,department,approver.user_id)
        intelligence.company_settings(raw.owner,company,enabled=True)
        companies.append(SimpleNamespace(company=company,book=book,applicant=applicant,approver=approver,finance=finance))
    c=SimpleNamespace(raw=raw,chat=chat,queue=queue,worker_queue=worker_queue,intelligence=intelligence,storage=storage,provider=provider,companies=companies)
    # A shared test database must have no unrelated active lease. Never clear it
    # here: otherwise a false pass could hide a global-cap regression.
    assert raw_running(c)==[], '专用测试库存在其他活动租约，请先完成对应测试任务再运行混合并发测试'
    try:
        yield c
    finally:
        with raw.psycopg.connect(ADMIN_URL) as conn:
            for table in ('ocr_attempts','proposal_attempts'):
                conn.execute('UPDATE accounting.'+table+" SET phase='finished',status='cancelled',finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status='running'",(raw.owner.firm_id,))
            conn.execute("UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN('queued','running','retry_wait')",(raw.owner.firm_id,))
            conn.execute("UPDATE accounting.proposal_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN('waiting','queued','running','retry_wait')",(raw.owner.firm_id,))
            conn.execute("UPDATE accounting.assistant_turns SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='test_cleanup',updated_at=accounting.ocr_utc_now(),finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN('queued','running')",(raw.owner.firm_id,))


def processing(c,company,kind):
    business=c.intelligence.business
    event=business.create(company.applicant,BusinessEventCreate(company_id=company.company,template_id='reimbursement',payload={'occurred_on':'2026-10-03','description':'合成混合队列办公费','counterparty':'合成供应商','amount':'0.30','direction':'expense','payment_status':'unpaid','category':'office'}))
    attachment=c.storage.put('mixed-synthetic.png','image/png',attachment_bytes())
    event=business.add_attachment(company.applicant,event['id'],event['version'],attachment)
    if kind=='ocr':
        result=c.queue.enqueue(company.applicant,BatchRequest(company_id=company.company,items=[{'event_id':event['id'],'attachment_id':attachment['id'],'expected_version':event['version']}],idempotency_key=uuid4().hex))
        task_id = result['jobs'][0]['id']
        wait_until_due(c.raw.psycopg.connect, ADMIN_URL, 'ocr_jobs', [task_id])
        return task_id
    # A real successful synthetic extraction avoids a waiting dependency. No
    # test-only status UPDATE or forged result bypasses the ordinary guards.
    run=c.intelligence.extract(company.applicant,event['id'],OCRRequest(attachment_id=attachment['id'],expected_version=event['version'],idempotency_key=uuid4().hex))
    assert run['status']=='succeeded',run
    event=business.submit(company.applicant,event['id'],event['version'])
    event=business.approve_department(company.approver,event['id'],event['version'])
    event=business.approve_finance(company.finance,event['id'],event['version'])
    result=business.handoff(company.finance,event['id'],HandoffRequest(expected_version=event['version'],book_id=company.book,idempotency_key=uuid4().hex))
    job=c.queue.proposals.get(company.finance,result['automatic_job']['id'])
    assert job['status']=='queued',job
    wait_until_due(c.raw.psycopg.connect, ADMIN_URL, 'proposal_jobs', [job['id']])
    return job['id']


def question(c,company):
    conversation=c.chat.service.create(c.raw.owner,ConversationCreate(company_id=company.company,book_ids=[company.book],title='合成混合队列问题',idempotency_key=uuid4().hex))['conversation']
    task_id = c.chat.service.enqueue(c.raw.owner,conversation['id'],TurnCreate(question='只核对合成企业的当前范围',expected_scope_version=1,idempotency_key=uuid4().hex))['turn']['id']
    wait_until_due(c.raw.psycopg.connect, ADMIN_URL, 'assistant_turns', [task_id])
    return task_id


def claim_processing(c,kind,entry):
    with c.worker_queue.worker_transaction() as conn:
        if entry=='combined':
            row=conn.execute('SELECT accounting.claim_processing_job(?,180,2)',('mixed-native-'+uuid4().hex,)).fetchone()[0]
            return row if row else None
        fn='claim_ocr_job' if kind=='ocr' else 'claim_proposal_job'
        row=conn.execute('SELECT row_to_json(j)::text FROM accounting.'+fn+'(?,180,2) j',('mixed-direct-'+uuid4().hex,)).fetchone()
        return (json.loads(row[0])|{'job_kind':kind}) if row else None


def finish_processing(c,job):
    fn='worker_finish_ocr_job' if job['job_kind']=='ocr' else 'worker_finish_proposal_job'
    with c.worker_queue.worker_transaction() as conn:
        assert conn.execute('SELECT accounting.'+fn+"(?,?,'failed','synthetic_complete','合成并发验证已完成',NULL,0)",(job['id'],job['lease_token'])).fetchone()[0]


def finish_chat(c,turn):
    assert c.chat.worker.finish(turn,'failed',code='synthetic_complete',detail='合成并发验证已完成')


@pytest.mark.parametrize('kind',['ocr','proposal'])
@pytest.mark.parametrize('entry',['direct','combined'])
def test_pg_two_running_chats_block_processing_in_distinct_third_company(mixed_pg,kind,entry):
    c=mixed_pg;a,b,third=c.companies
    first_id,second_id=question(c,a),question(c,b)
    first,second=c.chat.worker.claim(),c.chat.worker.claim()
    assert {first['id'],second['id']}=={first_id,second_id}
    assert first['company_id']!=second['company_id']
    pending_id=processing(c,third,kind)
    assert third.company not in {first['company_id'],second['company_id']}
    assert len(raw_running(c))==2
    assert claim_processing(c,kind,entry) is None
    finish_chat(c,first)
    job=claim_processing(c,kind,entry)
    assert job and job['id']==pending_id and job['company_id']==third.company
    assert len(raw_running(c))==2
    finish_processing(c,job);finish_chat(c,second)
    assert raw_running(c)==[]


@pytest.mark.parametrize('kind',['ocr','proposal'])
@pytest.mark.parametrize('entry',['direct','combined'])
def test_pg_two_processing_jobs_block_chat_in_distinct_third_company(mixed_pg,kind,entry):
    c=mixed_pg;a,b,third=c.companies
    first_id,second_id=processing(c,a,kind),processing(c,b,kind)
    first,second=claim_processing(c,kind,entry),claim_processing(c,kind,entry)
    assert {first['id'],second['id']}=={first_id,second_id}
    assert first['company_id']!=second['company_id']
    pending_id=question(c,third)
    assert third.company not in {first['company_id'],second['company_id']}
    assert len(raw_running(c))==2
    assert c.chat.worker.claim() is None
    finish_processing(c,first)
    turn=c.chat.worker.claim()
    assert turn and turn['id']==pending_id and turn['company_id']==third.company
    assert len(raw_running(c))==2
    finish_chat(c,turn);finish_processing(c,second)
    assert raw_running(c)==[]


@pytest.mark.parametrize('kind',['ocr','proposal'])
@pytest.mark.parametrize('entry',['direct','combined'])
def test_pg_company_mutex_is_bidirectional_with_one_free_global_slot(mixed_pg,kind,entry):
    c=mixed_pg;company=c.companies[0]
    pending_id=processing(c,company,kind)
    chat_id=question(c,company)
    turn=c.chat.worker.claim()
    assert turn and turn['id']==chat_id
    assert len(raw_running(c))==1  # This denial must be company exclusion, not cap.
    assert claim_processing(c,kind,entry) is None
    finish_chat(c,turn)
    job=claim_processing(c,kind,entry)
    assert job and job['id']==pending_id
    second_chat_id=question(c,company)
    assert len(raw_running(c))==1
    assert c.chat.worker.claim() is None
    finish_processing(c,job)
    next_turn=c.chat.worker.claim()
    assert next_turn and next_turn['id']==second_chat_id
    finish_chat(c,next_turn)
    assert raw_running(c)==[]
