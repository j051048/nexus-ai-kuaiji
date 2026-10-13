"""Actual app role cannot forge assistant previews, confirmation or receipts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import EvidenceRequest
from app.assistant.actions import ActionConfirm,ActionPrepare,AssistantActionService,encode
from app.assistant.models import ConversationCreate
from app.assistant.service import AssistantService
from app.jobs.service import QueueService
from test_business_postgres import ADMIN_URL,APP_URL,business_case,business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event,handoff
from test_stage15_assistant_actions import import_bank

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason='需要专用 PostgreSQL 管理员和受限 API 测试连接')


@pytest.fixture
def pg_action(pg_ai):
    c=pg_ai;c.queue=QueueService(c.db,c.intelligence)
    c.assistant=AssistantService(c.db,c.ledger,c.business)
    c.actions=AssistantActionService(c.db,c.assistant,c.queue,c.ledger)
    yield c
    # Cancel only this synthetic fixture's pending jobs, preserving all records.
    stamp=datetime.now(timezone.utc).isoformat()
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=%s WHERE firm_id=%s AND status IN('queued','retry_wait')",(stamp,c.manager.firm_id))
        conn.execute("UPDATE accounting.proposal_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=%s WHERE firm_id=%s AND status IN('waiting','queued','retry_wait')",(stamp,c.manager.firm_id))


def conv(c,actor='employee',books=(),events=(),draft=None):
    return c.assistant.create(c.actors[actor],ConversationCreate(company_id=c.company['id'],book_ids=list(books),event_ids=list(events),draft_id=draft,title='合成PG助手',idempotency_key=uuid4().hex))['conversation']


def plan(c,conversation,kind='ocr_batch',actor='employee'):
    return c.actions.prepare(c.actors[actor],conversation['id'],ActionPrepare(kind=kind,reason='PG独立人工核对范围',idempotency_key=uuid4().hex))


def command(p,key=None):
    return ActionConfirm(fingerprint=p['fingerprint'],confirmed=True,idempotency_key=key or uuid4().hex)


def test_pg_confirm_queues_once_and_preserves_human_review(pg_action):
    c=pg_action;event=create_event(c);conversation=conv(c,events=[event['id']]);p=plan(c,conversation);body=command(p)
    result=c.actions.confirm(c.actors['employee'],conversation['id'],p['id'],body)
    assert result['jobs'][0]['status']=='queued' and result['progress']['completed']==0
    assert c.actions.confirm(c.actors['employee'],conversation['id'],p['id'],body)['id']==result['id']
    with c.db.transaction(actor=c.actors['employee']) as conn:
        assert conn.execute('SELECT count(*) FROM assistant_action_receipts WHERE plan_id=?',(p['id'],)).fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM ocr_jobs WHERE batch_id=?',(result['batch_id'],)).fetchone()[0]==1
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM assistant_action_receipts WHERE plan_id=?',(p['id'],)).fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM drafts WHERE book_id=?',(c.book['id'],)).fetchone()[0]==0
    assert c.provider.extract_calls==c.provider.proposal_calls==0


def test_pg_native_receipt_without_private_confirmation_is_rejected(pg_action):
    c=pg_action;event=create_event(c);conversation=conv(c,events=[event['id']]);p=plan(c,conversation)
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            conn.execute('INSERT INTO assistant_action_receipts(id,plan_id,conversation_id,firm_id,company_id,created_by,request_key,request_fingerprint,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,p['id'],conversation['id'],c.manager.firm_id,c.company['id'],c.actors['employee'].user_id,uuid4().hex,'a'*64,encode({'kind':'ocr_batch','count':1,'jobs':[{'id':'fake'}],'draft_ids':[],'batch_id':'fake'}),c.queue.database_timestamp(conn)))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            conn.execute('SELECT * FROM assistant_action_execution_claims')


def test_pg_native_begin_without_receipt_cannot_commit(pg_action):
    c=pg_action;event=create_event(c);conversation=conv(c,events=[event['id']]);p=plan(c,conversation)
    with pytest.raises(c.psycopg.Error,match='atomically'):
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            assert conn.execute('SELECT accounting.assistant_begin_action(?,?,?)',(p['id'],p['fingerprint'],uuid4().hex)).fetchone()[0]
    assert c.actions.get(c.actors['employee'],conversation['id'],p['id'])['state']=='ready'


def test_pg_native_stale_fact_begin_is_rejected_after_extra_original(pg_action):
    c=pg_action;event=create_event(c);conversation=conv(c,events=[event['id']]);p=plan(c,conversation)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute('UPDATE accounting.business_events SET version=version+1 WHERE id=%s',(event['id'],))
    with pytest.raises(c.psycopg.Error,match='stale'):
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            conn.execute('SELECT accounting.assistant_begin_action(?,?,?)',(p['id'],p['fingerprint'],uuid4().hex))


def test_pg_native_grant_revocation_hides_history_before_count(pg_action):
    c=pg_action;import_bank(c);conversation=conv(c,'accountant',books=[c.book['id']]);p=plan(c,conversation,'bank_drafts','accountant')
    assert c.actions.list(c.actors['accountant'],conversation['id'])['pagination']['total']==1
    with c.db.transaction(actor=c.actors['employee']) as conn:
        assert conn.execute('SELECT count(*) FROM assistant_action_plans WHERE id=?',(p['id'],)).fetchone()[0]==0
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute('DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s',(c.book['id'],c.actors['accountant'].user_id))
    with pytest.raises(AccountingError) as exc:c.actions.list(c.actors['accountant'],conversation['id'])
    assert exc.value.status==404


def test_pg_native_bank_receipt_proves_exact_untouched_drafts(pg_action):
    c=pg_action;import_bank(c,number=2);conversation=conv(c,'accountant',books=[c.book['id']]);p=plan(c,conversation,'bank_drafts','accountant')
    result=c.actions.confirm(c.actors['accountant'],conversation['id'],p['id'],command(p))
    assert result['created']==2 and len(result['draft_ids'])==2
    for draft_id in result['draft_ids']:
        assert c.ledger.get_draft(c.actors['accountant'],c.book['id'],draft_id)['status']=='draft'
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute('SELECT count(*) FROM exports WHERE book_id=?',(c.book['id'],)).fetchone()[0]==0


def test_pg_native_chart_change_blocks_confirmation_even_without_python(pg_action):
    c=pg_action;import_bank(c);conversation=conv(c,'accountant',books=[c.book['id']]);p=plan(c,conversation,'bank_drafts','accountant')
    with c.db.transaction(write=True,actor=c.actors['accountant']) as conn:
        conn.execute("UPDATE accounts SET name='变更科目名称',version=version+1 WHERE book_id=? AND code='1002'",(c.book['id'],))
    with pytest.raises(c.psycopg.Error,match='stale'):
        with c.db.transaction(write=True,actor=c.actors['accountant']) as conn:
            conn.execute('SELECT accounting.assistant_begin_action(?,?,?)',(p['id'],p['fingerprint'],uuid4().hex))


def test_pg_native_proposal_receipt_proves_current_scope_and_real_dependencies(pg_action):
    c=pg_action;c.business.proposal_queue=None
    result=handoff(c,create_event(c));conversation=conv(c,'accountant',books=[c.book['id']],draft=result['draft_id']);p=plan(c,conversation,'proposal_batch','accountant')
    assert p['count']==1,p
    receipt=c.actions.confirm(c.actors['accountant'],conversation['id'],p['id'],command(p))
    assert receipt['jobs'][0]['draft_id']==result['draft_id'] and receipt['jobs'][0]['status']=='waiting'
    assert c.ledger.get_draft(c.actors['accountant'],c.book['id'],result['draft_id'])['status']=='draft'
    assert c.provider.proposal_calls==c.provider.extract_calls==0


def test_pg_concurrent_confirmation_has_single_claim_and_receipt(pg_action):
    c=pg_action;event=create_event(c);conversation=conv(c,events=[event['id']]);p=plan(c,conversation);body=command(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.actions.confirm(c.actors['employee'],conversation['id'],p['id'],body),range(2)))
    assert results[0]['id']==results[1]['id']


def test_pg_company_only_conversation_never_selects_unrequested_records(pg_action):
    c=pg_action;create_event(c);conversation=conv(c);p=plan(c,conversation)
    assert p['state']=='empty' and p['count']==0 and p['blocked'][0]['code']=='scope_required'
    with pytest.raises(AccountingError,match='没有可执行'):
        c.actions.confirm(c.actors['employee'],conversation['id'],p['id'],command(p))
