"""Legacy success proof cannot bypass current blockers; cleanup remains possible."""
from uuid import uuid4

import pytest

from app.accounting.domain import Actor
from app.cases.models import CaseCreate
from app.cases.service import CaseService
from test_intelligence_workflow import create_event,extract,handoff
from test_stage7_proposal_queue import template
from test_stage7_proposal_postgres import pg_stage7
from test_queue_worker_postgres import ADMIN_URL,APP_URL,WORKER_URL,business_case,business_postgres,pg_ai,pg_queue

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL,reason='需要专用PostgreSQL管理员/API/独立worker测试连接')


def native(case, job, function, *arguments):
    # Identical principal/role switch to QueueExecution._function, with a real
    # dedicated worker login and the canonical submitting actor's claims.
    actor=Actor(job['execution_user_id'],job['firm_id'])
    with case.worker.queue.db.transaction(write=True,actor=actor) as conn:
        conn.execute('SET LOCAL ROLE accounting_worker')
        return conn.execute('SELECT accounting.'+function+'('+','.join('?' for _ in arguments)+')',arguments).fetchone()[0]


def task(case,mapped):
    return case.queue.proposals.get(case.actors['accountant'],mapped['automatic_job']['id'])


def open_case(case,mapped):
    return CaseService(case.db,case.business).create(case.actors['accountant'],CaseCreate(
        event_id=mapped['event']['id'],title='当前业务原件仍需核验',reason='成功回执不能跳过当前未关闭调查',idempotency_key=uuid4().hex))


def test_pg_fixed_template_success_refuses_current_case_and_revoked_actor_can_cleanup(pg_stage7):
    case=pg_stage7
    configured=template(case)
    event=create_event(case);extract(case,event)
    mapped=handoff(case,event)
    claimed=case.worker.queue.claim('legacy-template-native-guard')
    assert claimed and claimed['id']==mapped['automatic_job']['id'] and claimed['job_kind']=='proposal'
    # Handoff already produced these exact template lines. Simulate a legal
    # untouched automatic adoption's version without fabricating any receipt.
    with case.db.transaction(write=True,actor=case.actors['finance']) as conn:
        conn.execute("UPDATE drafts SET version=version+1 WHERE id=? AND book_id=? AND status='draft'",(mapped['draft_id'],case.book['id']))
    open_case(case,mapped)
    assert native(case,claimed,'worker_finish_proposal_rule',claimed['id'],claimed['lease_token'],configured['id'],configured['version'],claimed['expected_draft_version']+1) is False
    current=task(case,mapped)
    assert current['status']=='running' and current['result_template_id'] is None
    case.directory.revoke_company_member(case.manager,case.company['id'],case.actors['finance'].user_id)
    # A terminal failure must be able to clean a lease after executor revocation.
    assert native(case,claimed,'worker_finish_proposal_job',claimed['id'],claimed['lease_token'],'failed','test_cleanup','失权后清理原任务',None,0) is True
    current=task(case,mapped)
    assert current['status']=='failed' and current['result_template_id'] is None


def test_pg_model_success_proof_does_not_bypass_new_case_on_recovered_live_lease(pg_stage7):
    case=pg_stage7
    event=create_event(case);extract(case,event)
    mapped=handoff(case,event)
    assert case.worker.run_once()
    succeeded=task(case,mapped)
    assert succeeded['status']=='succeeded' and succeeded['current_run_id'] and case.provider.proposal_calls==1
    token=uuid4().hex
    # A recovery fixture preserves the immutable run and its real application
    # proof, but starts another live attempt. No AI/application row is changed.
    with case.psycopg.connect(ADMIN_URL) as conn:
        row=conn.execute("UPDATE accounting.proposal_jobs SET status='running',attempt_count=attempt_count+1,lease_token=%s,lease_until=to_char((clock_timestamp()+interval '10 minutes') AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"'),worker_id='recovered-proof-guard',finished_at=NULL WHERE id=%s RETURNING firm_id,company_id,event_id,book_id,draft_id,attempt_count,execution_user_id,expected_fact_version,expected_draft_version,configuration_fingerprint,policy_fingerprint",(token,succeeded['id'])).fetchone()
        firm,company,event_id,book_id,draft_id,attempt_no,executor,fact_version,draft_version,configuration,policy=row
        conn.execute("INSERT INTO accounting.proposal_attempts(id,job_id,event_id,firm_id,company_id,book_id,draft_id,attempt_no,lease_token,execution_user_id,input_fact_version,input_draft_version,configuration_fingerprint,policy_fingerprint,ai_run_id,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,accounting.ocr_utc_now())",(uuid4().hex,succeeded['id'],event_id,firm,company,book_id,draft_id,attempt_no,token,executor,fact_version,draft_version,configuration,policy,succeeded['current_run_id']))
    open_case(case,mapped)
    job={'execution_user_id':executor,'firm_id':firm}
    assert native(case,job,'worker_finish_proposal_job',succeeded['id'],token,'succeeded','','',succeeded['current_run_id'],0) is False
    assert task(case,mapped)['status']=='running'
    assert native(case,job,'worker_finish_proposal_job',succeeded['id'],token,'interrupted','test_cleanup','保留旧成功事实，清理本次恢复任务',succeeded['current_run_id'],0) is True
    assert task(case,mapped)['status']=='interrupted'
    with case.db.transaction(actor=case.actors['accountant']) as conn:
        assert conn.execute("SELECT status FROM ai_runs WHERE id=?",(succeeded['current_run_id'],)).fetchone()[0]=='succeeded'
        assert conn.execute("SELECT count(*) FROM ai_proposal_applications WHERE run_id=?",(succeeded['current_run_id'],)).fetchone()[0]==1
