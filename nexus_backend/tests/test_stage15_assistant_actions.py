"""Real assistant previews cannot enlarge a scope or approve accounting work."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import io
import json
import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError
from PIL import Image

from app.accounting.domain import AccountingError
from app.accounting.models import BookMemberGrant, EvidenceRequest, ImportRequest, SourceRow
from app.assistant.actions import ActionConfirm, ActionPrepare, AssistantActionService, encode
from app.assistant.models import ConversationCreate
from app.assistant.service import AssistantService
from app.jobs.service import QueueService
from test_intelligence_workflow import ai_case, create_event, extract, handoff
from test_queue_workflow import TestClock


@pytest.fixture
def action_case(ai_case):
    case = ai_case
    case.clock = TestClock()
    case.queue = QueueService(case.db, case.intelligence, clock=case.clock)
    case.assistant = AssistantService(case.db, case.ledger, case.business)
    case.actions = AssistantActionService(case.db, case.assistant, case.queue, case.ledger)
    return case


def conversation(case, actor='employee', books=(), events=(), draft_id=None):
    return case.assistant.create(case.actors[actor], ConversationCreate(company_id=case.company['id'], book_ids=list(books), event_ids=list(events), draft_id=draft_id, title='合成会计任务对话', idempotency_key=uuid4().hex))['conversation']


def prepare(case, conv, kind='ocr_batch', actor='employee'):
    return case.actions.prepare(case.actors[actor], conv['id'], ActionPrepare(kind=kind, reason='用户核对当前明确范围', idempotency_key=uuid4().hex))


def confirm(case, conv, plan, actor='employee', key=None):
    return case.actions.confirm(case.actors[actor], conv['id'], plan['id'], ActionConfirm(fingerprint=plan['fingerprint'], confirmed=True, idempotency_key=key or uuid4().hex))


def counts(case):
    with case.db.transaction() as conn:
        return {table: conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('ocr_batches', 'ocr_jobs', 'proposal_jobs', 'drafts', 'exports', 'assistant_action_plans', 'assistant_action_receipts')}


def import_bank(case, book=None, number=1):
    book = book or case.book
    case.ledger.import_sources(case.actors['accountant'], book['id'], ImportRequest(source_name='合成银行流水', rows=[SourceRow(external_id='synthetic-'+str(i)+'-'+uuid4().hex, date='2026-10-03', description='办公耗材付款'+str(i), counterparty='合成供应商', amount='0.30', direction='expense', evidence_ref='原始银行回单已保留') for i in range(number)]))


def test_preview_no_mutation_model_call_or_approval(action_case):
    c = action_case; event = create_event(c); conv = conversation(c, events=[event['id']])
    before = counts(c); plan = prepare(c, conv)
    assert plan['state'] == 'ready' and plan['count'] == 1
    assert plan['items'][0]['attachment_id'] == event['attachments'][0]['id']
    assert set(plan['items'][0]) <= {'title','book_id','event_id','attachment_id'}
    assert counts(c) == {**before, 'assistant_action_plans': before['assistant_action_plans']+1}
    assert c.provider.extract_calls == c.provider.proposal_calls == 0
    assert 'sha256' not in json.dumps(plan) and 'event_payload' not in json.dumps(plan)


def test_confirm_is_fixed_atomic_idempotent_and_progress_is_real(action_case):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']]); plan=prepare(c,conv); key=uuid4().hex
    receipt=confirm(c,conv,plan,key=key)
    assert receipt['count']==1 and receipt['jobs'][0]['status']=='queued'
    assert receipt['progress']['completed']==0 and receipt['progress']['total']==1
    assert confirm(c,conv,plan,key=key)['id']==receipt['id']
    assert c.actions.get(c.actors['employee'],conv['id'],plan['id'])['state']=='executed'
    assert counts(c)['ocr_jobs']==counts(c)['assistant_action_receipts']==1
    assert counts(c)['drafts']==counts(c)['exports']==0
    with pytest.raises(AccountingError,match='已经执行'):
        confirm(c,conv,plan)
    assert c.provider.extract_calls==0


def test_same_preview_key_reuses_exact_plan_and_rejects_different_kind(action_case):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']])
    request=ActionPrepare(kind='ocr_batch',reason='首次计划核对依据',idempotency_key=uuid4().hex)
    plan=c.actions.prepare(c.actors['employee'],conv['id'],request)
    assert c.actions.prepare(c.actors['employee'],conv['id'],request)['id']==plan['id']
    with pytest.raises(AccountingError,match='不同操作'):
        c.actions.prepare(c.actors['employee'],conv['id'],request.model_copy(update={'kind':'bank_drafts'}))


@pytest.mark.parametrize('change',['fact','config','disabled','attachment','active_job'])
def test_changed_inputs_force_new_preview_and_no_partial_enqueue(action_case,change):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']]); plan=prepare(c,conv)
    if change=='fact':
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            conn.execute('UPDATE business_events SET version=version+1,fact_version=fact_version+1 WHERE id=?',(event['id'],))
    elif change=='config':
        c.provider.settings=replace(c.provider.settings,vision_model='changed-vision')
    elif change=='disabled':
        c.intelligence.company_settings(c.manager,c.company['id'],enabled=False)
    elif change=='attachment':
        pixels=io.BytesIO();Image.new('RGB',(40,20),'blue').save(pixels,'PNG')
        additional=c.storage.put('补充票据.png','image/png',pixels.getvalue())
        c.business.add_attachment(c.actors['employee'],event['id'],event['version'],additional)
    else:
        from app.jobs.requests import BatchItem,BatchRequest
        c.queue.enqueue(c.actors['employee'],BatchRequest(company_id=c.company['id'],items=[BatchItem(event_id=event['id'],attachment_id=event['attachments'][0]['id'],expected_version=event['version'])],idempotency_key=uuid4().hex))
    before=counts(c)
    with pytest.raises(AccountingError,match='已改变'):
        confirm(c,conv,plan)
    assert counts(c)==before


def test_expiration_and_fingerprint_cannot_be_bypassed(action_case):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']]); plan=prepare(c,conv)
    with pytest.raises(AccountingError,match='指纹'):
        c.actions.confirm(c.actors['employee'],conv['id'],plan['id'],ActionConfirm(fingerprint='0'*64,confirmed=True,idempotency_key=uuid4().hex))
    c.clock.advance(1801)
    assert c.actions.get(c.actors['employee'],conv['id'],plan['id'])['state']=='expired'
    with pytest.raises(AccountingError,match='30 分钟'):
        confirm(c,conv,plan)
    assert counts(c)['ocr_jobs']==0


def test_empty_plan_explains_no_work_and_no_silent_scope_switch(action_case):
    c=action_case; conv=conversation(c); plan=prepare(c,conv)
    assert plan['state']=='empty' and any(row['code']=='scope_required' for row in plan['blocked'])
    bank=prepare(c,conv,'bank_drafts')
    assert bank['state']=='empty' and any(row['code']=='book_required' for row in bank['blocked'])
    with pytest.raises(AccountingError,match='没有可执行'):
        confirm(c,conv,plan)
    assert counts(c)['ocr_jobs']==0


def test_current_successful_ocr_is_reused_and_active_job_explained(action_case):
    c=action_case; event=create_event(c); extract(c,event); conv=conversation(c,events=[event['id']])
    plan=prepare(c,conv)
    assert plan['count']==0 and c.provider.extract_calls==1
    assert counts(c)['ocr_jobs']==0


def test_conversation_owner_company_and_revocation_gate_plan_and_receipt(action_case):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']]); plan=prepare(c,conv); confirm(c,conv,plan)
    for actor in ['peer','outsider','accountant']:
        with pytest.raises(AccountingError) as exc:
            c.actions.get(c.actors[actor],conv['id'],plan['id'])
        assert exc.value.status==404
    with c.db.transaction(write=True,actor=c.manager) as conn:
        conn.execute('DELETE FROM company_memberships WHERE company_id=? AND user_id=?',(c.company['id'],c.actors['employee'].user_id))
    with pytest.raises(AccountingError) as exc:
        c.actions.receipt(c.actors['employee'],conv['id'],plan['id'])
    assert exc.value.status==404


def test_scope_does_not_include_peer_events_or_other_company(action_case):
    c=action_case; own=create_event(c); peer=create_event(c,'peer'); conv=conversation(c,events=[own['id']])
    plan=prepare(c,conv)
    assert [item['event_id'] for item in plan['items']]==[own['id']]
    result=confirm(c,conv,plan)
    assert [item['event_id'] for item in result['jobs']]==[own['id']]
    assert peer['id'] not in json.dumps(result)


def test_exact_bank_sources_make_drafts_only_and_plan_cannot_expand(action_case):
    c=action_case; import_bank(c,number=2); conv=conversation(c,'accountant',books=[c.book['id']]); plan=prepare(c,conv,'bank_drafts','accountant')
    assert plan['count']==2
    receipt=confirm(c,conv,plan,'accountant')
    assert receipt['created']==2 and len(receipt['draft_ids'])==2
    assert counts(c)['exports']==0
    for draft_id in receipt['draft_ids']:
        assert c.ledger.get_draft(c.actors['accountant'],c.book['id'],draft_id)['status']=='draft'


def test_large_bank_scope_confirms_only_fixed_first_hundred(action_case):
    c=action_case;import_bank(c,number=101);conv=conversation(c,'accountant',books=[c.book['id']]);p=prepare(c,conv,'bank_drafts','accountant')
    assert p['count']==100 and any(row['code']=='item_limit' for row in p['blocked'])
    receipt=confirm(c,conv,p,'accountant')
    assert receipt['created']==100 and len(receipt['draft_ids'])==100
    remaining=prepare(c,conv,'bank_drafts','accountant')
    assert remaining['count']==1


def test_bank_evidence_and_readonly_grant_changes_invalidate_preview(action_case):
    c=action_case; import_bank(c); conv=conversation(c,'accountant',books=[c.book['id']]); plan=prepare(c,conv,'bank_drafts','accountant')
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        source=conn.execute('SELECT id FROM sources WHERE book_id=?',(c.book['id'],)).fetchone()[0]
    c.ledger.add_evidence(c.actors['accountant'],c.book['id'],source,EvidenceRequest(evidence_ref='新增银行凭据'))
    with pytest.raises(AccountingError,match='已改变'):
        confirm(c,conv,plan,'accountant')
    plan=prepare(c,conv,'bank_drafts','accountant')
    c.directory.grant_book_member(c.manager,c.book['id'],c.actors['accountant'].user_id,BookMemberGrant(role='viewer'))
    with pytest.raises(AccountingError,match='已改变'):
        confirm(c,conv,plan,'accountant')
    assert counts(c)['drafts']==0


def test_current_draft_scope_does_not_expand_to_other_events(action_case):
    c=action_case; c.business.proposal_queue=None
    first=handoff(c,create_event(c)); second=handoff(c,create_event(c))
    conv=conversation(c,'accountant',books=[c.book['id']],draft_id=first['draft_id'])
    ocr=prepare(c,conv,'ocr_batch','accountant')
    assert len(ocr['items'])==1 and ocr['items'][0]['event_id']==first['event']['id']
    bank=prepare(c,conv,'bank_drafts','accountant')
    assert bank['count']==0 and bank['blocked'][0]['code']=='bank_scope_required'
    proposals=prepare(c,conv,'proposal_batch','accountant')
    assert len(proposals['items'])==1 and proposals['items'][0]['draft_id']==first['draft_id']
    result=confirm(c,conv,proposals,'accountant')
    assert len(result['jobs'])==1 and result['jobs'][0]['draft_id']==first['draft_id']
    assert second['draft_id'] not in json.dumps(result)
    assert c.provider.proposal_calls==c.provider.extract_calls==0


def test_batch_rolls_back_first_job_when_second_enqueue_fails(action_case,monkeypatch):
    c=action_case; c.business.proposal_queue=None
    first=handoff(c,create_event(c)); second=handoff(c,create_event(c)); conv=conversation(c,'accountant',books=[c.book['id']]); plan=prepare(c,conv,'proposal_batch','accountant')
    assert plan['count']==2
    original=c.queue.proposals.enqueue_in_transaction; calls=[]
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise AccountingError('合成第二项容量冲突',409)
        return original(*args,**kwargs)
    monkeypatch.setattr(c.queue.proposals,'enqueue_in_transaction',fail_second)
    before=counts(c)
    with pytest.raises(AccountingError,match='第二项'):
        confirm(c,conv,plan,'accountant')
    assert counts(c)==before and counts(c)['ocr_jobs']==0


def test_concurrent_same_confirmation_yields_one_receipt_and_batch(action_case):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']]); plan=prepare(c,conv); key=uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:confirm(c,conv,plan,key=key),range(2)))
    assert results[0]['id']==results[1]['id'] and counts(c)['ocr_jobs']==1


def test_native_immutable_preview_and_fake_receipt_rejected(action_case):
    c=action_case; event=create_event(c); conv=conversation(c,events=[event['id']]); plan=prepare(c,conv)
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            conn.execute("UPDATE assistant_action_plans SET item_count=0 WHERE id=?",(plan['id'],))
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True,actor=c.actors['employee']) as conn:
            conn.execute('INSERT INTO assistant_action_receipts(id,plan_id,conversation_id,firm_id,company_id,created_by,request_key,request_fingerprint,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(uuid4().hex,plan['id'],conv['id'],c.actors['employee'].firm_id,c.company['id'],c.actors['employee'].user_id,uuid4().hex,'a'*64,encode({'kind':'ocr_batch','count':1,'batch_id':'fake','jobs':[{'id':'fake'}],'draft_ids':[]} ),c.queue.timestamp()))
    assert counts(c)['assistant_action_receipts']==0


@pytest.mark.parametrize('bad',[{'kind':'export'},{'items':['arbitrary-id']},{'company_id':'other'},{'reason':'   '},{'idempotency_key':'     key   '}])
def test_request_model_rejects_arbitrary_tools_ids_or_scope(bad):
    with pytest.raises(ValidationError):
        ActionPrepare(**({'kind':'ocr_batch','reason':'需要处理当前资料','idempotency_key':'synthetic-key'}|bad))


@pytest.mark.parametrize('confirmed',[False,1,'true',None])
def test_confirmation_must_be_explicit_json_boolean(confirmed):
    with pytest.raises(ValidationError):
        ActionConfirm(fingerprint='a'*64,confirmed=confirmed,idempotency_key='synthetic-confirm-key')


def test_plan_history_restores_real_receipts_and_paging(action_case):
    c=action_case;event=create_event(c);conv=conversation(c,events=[event['id']]);p=prepare(c,conv);receipt=confirm(c,conv,p)
    history=c.actions.list(c.actors['employee'],conv['id'],limit=1)
    assert history['pagination']['total']==1 and history['plans'][0]['state']=='executed'
    assert history['plans'][0]['receipt']['id']==receipt['id']
    assert c.actions.list(c.actors['employee'],conv['id'],offset=1)['plans']==[]
