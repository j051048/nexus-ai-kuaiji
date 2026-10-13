"""Fixed independent answers, archived replay comparison and honest upgrade gates."""
from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.accounting.catalog_models import AccountUpdate
from app.accounting.domain import AccountingError, Actor
from app.accounting.seed import LOCAL_ACTOR
from app.quality.benchmarks import BenchmarkService, compare_reports
from app.quality.models import BenchmarkCreate, BenchmarkCompare, RunCreate, StandardCreate, StandardConfirm
from app.quality.router import build_quality_router
from test_catalog import case
from test_stage8_learning import ready, evaluate_publish
from test_stage11_quality import standard, ai_standard
from test_intelligence_workflow import ai_case
from test_model_cost_workflow import cost_case


def benchmark_request(ids, **changes):
    return BenchmarkCreate(name='固定回归样本', standard_ids=ids, reason='两位会计分别核对的合成原件与分录', idempotency_key=uuid4().hex, **changes)


def replay(quality, actor, book, ids, mode='rules', **settings):
    return quality.create_run(actor, book, RunCreate(standard_ids=ids, mode=mode, idempotency_key=uuid4().hex, **settings))


def comparison(service, actor, book, benchmark, baseline, candidate, **policy):
    return service.compare(actor, book, benchmark['id'], BenchmarkCompare(baseline_run_id=baseline['id'], candidate_run_id=candidate['id'], **policy))


def report(states, *, mode='rules', key='baseline', stale=False):
    items = [{'standard_id': name, 'status': state, 'differences': [{'field':'amount'}] if state=='different' else []} for name,state in states.items()]
    return {'id':key, 'stale':stale, 'settings':{'mode':mode}, 'report':{'mode':mode,'engine_version':1,'items':items,'counts':{state:sum(row['status']==state for row in items) for state in ('passed','different','uncovered','stale')}}}


@pytest.mark.parametrize('state', ['uncovered','stale'])
def test_comparison_missing_or_stale_is_never_passed(state):
    result = compare_reports(report({'a':'passed'}), report({'a':state}, key='candidate'), ['a'])
    assert not result['eligible'] and result['counts'][state]==1 and result['regressions']==1


def test_comparison_journal_modes_share_domain_but_ocr_cannot_be_compared():
    left = report({'a':'passed'}, mode='templates')
    result = compare_reports(left, report({'a':'passed'}, mode='stored_model',key='candidate'), ['a'])
    assert result['eligible'] and result['domain']=='journal'
    assert not compare_reports(left, report({'a':'passed'}, mode='stored_ocr',key='candidate'), ['a'])['eligible']


def test_comparison_enforces_exact_sets_minimum_regressions_and_tolerance():
    left = report({'a':'passed','b':'passed'})
    assert not compare_reports(left, report({'a':'passed'},key='candidate'), ['a','b'])['eligible']
    assert not compare_reports(left, report({'a':'passed','b':'passed','c':'passed'},key='candidate'), ['a','b'])['eligible']
    assert not compare_reports(left, report({'a':'passed','b':'passed'},key='candidate'), ['a','b'],minimum_samples=3)['eligible']
    assert not compare_reports(left, report({'a':'different','b':'passed'},key='candidate'), ['a','b'],allowed_differences=1)['eligible']
    both = report({'a':'different'})
    assert compare_reports(both, report({'a':'different'},key='candidate'), ['a'],allowed_differences=1)['eligible']
    assert not compare_reports(both, report({'a':'different'},key='candidate'), ['a'])['eligible']


@pytest.mark.parametrize('fault', ['unknown_status','counts','duplicate','passed_with_difference','missing_engine','settings'])
def test_report_corruption_or_duplicates_cannot_become_fake_eligible(fault):
    candidate = report({'a':'passed'},key='candidate')
    if fault=='unknown_status': candidate['report']['items'][0]['status']='ignored'
    elif fault=='counts': candidate['report']['counts']['passed']=100
    elif fault=='duplicate': candidate['report']['items'].append(deepcopy(candidate['report']['items'][0]))
    elif fault=='passed_with_difference': candidate['report']['items'][0]['differences']=[{'field':'amount'}]
    elif fault=='missing_engine': candidate['report'].pop('engine_version')
    elif fault=='settings': candidate['settings']['mode']='stored_ocr'
    assert not compare_reports(report({'a':'passed'}),candidate,['a'])['eligible']


def test_candidate_freshness_and_historical_baseline_are_explicit():
    left = report({'a':'passed'},stale=True)
    right = report({'a':'passed'},key='candidate')
    result = compare_reports(left,right,['a'])
    assert result['eligible'] and result['baseline_archived']
    right['stale']=True
    assert not compare_reports(left,right,['a'])['eligible']
    right['stale']=False
    assert not compare_reports(left,right,['a'],stale=True)['eligible']
    assert not compare_reports(left,left,['a'])['eligible']


def test_fixed_set_versions_idempotency_immutable_and_pagination(ready):
    quality, reviewer, row = standard(ready)
    service, book = BenchmarkService(quality), ready[3]
    request = benchmark_request([row['id']])
    first = service.create(reviewer,book,request)
    assert first['version']==1 and not first['stale'] and first['sample_count']==1
    assert service.create(reviewer,book,request)['id']==first['id']
    second = service.create(reviewer,book,benchmark_request([row['id']]))
    assert second['version']==2 and second['id']!=first['id']
    request.reason='不同依据'
    with pytest.raises(AccountingError,match='幂等'):
        service.create(reviewer,book,request)
    assert service.list(reviewer,book,limit=1)['total']==2
    assert len(service.list(reviewer,book,limit=1,offset=1)['benchmarks'])==1
    with pytest.raises(AccountingError): service.list(reviewer,book,limit=101)
    with pytest.raises(sqlite3.IntegrityError,match='immutable'):
        with quality.db.transaction(write=True,actor=reviewer) as conn:
            conn.execute("UPDATE quality_benchmarks SET reason='替换依据' WHERE id=?",(first['id'],))


def test_unconfirmed_or_duplicate_versions_of_one_sample_not_fixed_set(ready):
    quality, reviewer, row = standard(ready)
    book, service = ready[3], BenchmarkService(quality)
    other = quality.create_standard(LOCAL_ACTOR,book,StandardCreate(sample_id=row['sample_id'],reason='同一凭证另一标签版本',idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError,match='独立确认'):
        service.create(reviewer,book,benchmark_request([other['id']]))
    quality.confirm(reviewer,book,other['id'],StandardConfirm(evidence_checked=True,reason='独立复核另一标签'))
    with pytest.raises(AccountingError,match='同一审核样例'):
        service.create(reviewer,book,benchmark_request([row['id'],other['id']]))
    with pytest.raises(sqlite3.IntegrityError,match='confirmed standards'):
        with quality.db.transaction(write=True,actor=reviewer) as conn:
            conn.execute('INSERT INTO quality_benchmarks(id,firm_id,company_id,book_id,name,version,standard_ids_json,reason,created_by,created_at,request_key,request_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (uuid4().hex,reviewer.firm_id,conn.execute('SELECT company_id FROM books WHERE id=?',(book,)).fetchone()[0],book,'绕过重复计数',1,json.dumps([row['id'],other['id']]),'恶意增加样本数',reviewer.user_id,'now',uuid4().hex,'a'*64))


def test_real_rule_replays_compare_without_mutating_rules_or_ledger(ready):
    evaluate_publish(ready)
    quality, reviewer, row = standard(ready)
    book, service = ready[3], BenchmarkService(quality)
    bench = service.create(reviewer,book,benchmark_request([row['id']]))
    baseline = replay(quality,reviewer,book,[row['id']])
    candidate = replay(quality,reviewer,book,[row['id']])
    with quality.db.transaction(actor=reviewer) as conn:
        before = dict(conn.execute('SELECT status,version FROM drafts WHERE id=?',(row['draft_id'],)).fetchone())
        rules = [tuple(item) for item in conn.execute('SELECT id,current_version,status FROM learning_rules WHERE book_id=? ORDER BY id',(book,))]
    result = comparison(service,reviewer,book,bench,baseline,candidate)
    assert result['report']['eligible'] and result['report']['counts']['passed']==1
    assert result['candidate']['costs']['measurement']=='deterministic_replay_no_model_call'
    with quality.db.transaction(actor=reviewer) as conn:
        assert dict(conn.execute('SELECT status,version FROM drafts WHERE id=?',(row['draft_id'],)).fetchone())==before
        assert [tuple(item) for item in conn.execute('SELECT id,current_version,status FROM learning_rules WHERE book_id=? ORDER BY id',(book,))]==rules


def test_configuration_drift_requires_current_candidate_and_retains_old_baseline(ready):
    evaluate_publish(ready)
    quality, reviewer, row = standard(ready)
    book, service = ready[3], BenchmarkService(quality)
    bench = service.create(reviewer,book,benchmark_request([row['id']]))
    old, candidate = replay(quality,reviewer,book,[row['id']]), replay(quality,reviewer,book,[row['id']])
    ready[1].update_account(LOCAL_ACTOR,book,'1002',AccountUpdate(code='1002',name='核对后的账户名称',expected_version=1))
    assert not comparison(service,reviewer,book,bench,old,candidate)['report']['eligible']
    current = replay(quality,reviewer,book,[row['id']])
    result = comparison(service,reviewer,book,bench,old,current)
    assert result['report']['eligible'] and result['baseline']['stale'] and result['report']['baseline_archived']


def test_evidence_drift_and_revocation_block_current_checks(ai_case):
    c=ai_case
    quality,row,ocr,_,_,_=ai_standard(c)
    service,book=BenchmarkService(quality),c.book['id']
    bench=service.create(c.manager,book,benchmark_request([row['id']]))
    args={'model':ocr['model'],'prompt_version':ocr['prompt_version']}
    baseline=replay(quality,c.manager,book,[row['id']],mode='stored_ocr',**args)
    candidate=replay(quality,c.manager,book,[row['id']],mode='stored_ocr',**args)
    with c.db.transaction(write=True,actor=c.manager) as conn:
        conn.execute('INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_by,created_at) VALUES(?,?,?,?,?)',(row['source_id'],book,'additional-reconciled-evidence',c.manager.user_id,'now'))
    assert service.get(c.manager,book,bench['id'])['stale']
    assert not comparison(service,c.manager,book,bench,baseline,candidate)['report']['eligible']
    for actor in (c.actors['employee'],c.actors['outsider']):
        with pytest.raises(AccountingError): service.get(actor,book,bench['id'])
    c.directory.revoke_book_member(c.manager,book,c.actors['accountant'].user_id)
    with pytest.raises(AccountingError): service.get(c.actors['accountant'],book,bench['id'])


def test_model_replay_missing_costs_are_unknown_and_reviewer_cannot_see_budget(ai_case):
    c=ai_case
    quality,row,_,proposal,extract_calls,proposal_calls=ai_standard(c)
    service,book=BenchmarkService(quality),c.book['id']
    bench=service.create(c.manager,book,benchmark_request([row['id']]))
    args={'model':proposal['model'],'prompt_version':proposal['prompt_version']}
    baseline=replay(quality,c.manager,book,[row['id']],mode='stored_model',**args)
    candidate=replay(quality,c.manager,book,[row['id']],mode='stored_model',**args)
    result=comparison(service,c.manager,book,bench,baseline,candidate)
    assert result['report']['eligible']
    assert result['candidate']['costs']['known_micro_cny'] is None
    assert result['candidate']['costs']['unknown_calls']==1
    reviewer=comparison(service,c.actors['accountant'],book,bench,baseline,candidate)
    assert reviewer['candidate']['costs']['measurement']=='not_authorized'
    assert reviewer['candidate']['costs']['known_micro_cny'] is None
    assert c.provider.extract_calls==extract_calls and c.provider.proposal_calls==proposal_calls


def test_http_limits_validation_scope_and_same_run_gate(ready):
    quality,reviewer,row=standard(ready)
    book=ready[3]
    app=FastAPI(); app.state.quality=quality; actor=reviewer
    app.include_router(build_quality_router(lambda: actor))
    @app.exception_handler(AccountingError)
    def failure(_:Request,error:AccountingError):
        return JSONResponse({'detail':str(error)},status_code=error.status)
    with TestClient(app) as client:
        url=f'/api/books/{book}/quality/benchmarks'
        assert client.get(url+'?limit=101').status_code==422
        request=benchmark_request([row['id']])
        created=client.post(url,json=request.model_dump())
        assert created.status_code==201
        assert client.post(url,json={**request.model_dump(),'standard_ids':[row['id'],row['id']]}).status_code==422
        run=replay(quality,reviewer,book,[row['id']],mode='templates')
        result=client.post(url+'/'+created.json()['id']+'/compare',json={'baseline_run_id':run['id'],'candidate_run_id':run['id']})
        assert result.status_code==200 and not result.json()['report']['eligible']
        actor=Actor('no-quality-access',reviewer.firm_id)
        assert client.get(url+'/'+created.json()['id']).status_code in (403,404)


def test_cost_estimates_and_manual_confirmation_are_distinct_without_new_charge(cost_case):
    from app.operations.requests import ReconcileRequest
    c=cost_case
    quality,row,_,proposal,extract_calls,proposal_calls=ai_standard(c)
    service,book=BenchmarkService(quality),c.book['id']
    bench=service.create(c.manager,book,benchmark_request([row['id']]))
    args={'model':proposal['model'],'prompt_version':proposal['prompt_version']}
    baseline=replay(quality,c.manager,book,[row['id']],mode='stored_model',**args)
    candidate=replay(quality,c.manager,book,[row['id']],mode='stored_model',**args)
    result=comparison(service,c.manager,book,bench,baseline,candidate)
    costs=result['candidate']['costs']
    assert costs['known_micro_cny']==costs['estimated_micro_cny']==11
    assert costs['confirmed_micro_cny'] is None and costs['unknown_calls']==0
    with c.db.transaction(actor=c.manager) as conn:
        current=conn.execute('SELECT version FROM model_costs WHERE id=?',(proposal['id'],)).fetchone()[0]
        cost_count=conn.execute('SELECT count(*) FROM model_costs WHERE company_id=?',(c.company['id'],)).fetchone()[0]
    c.operations.reconcile(c.manager,proposal['id'],ReconcileRequest(expected_version=current,confirmed_cost='0.01',reason='按虚构供应商结算单核定本次费用'))
    measured=comparison(service,c.manager,book,bench,baseline,candidate)['candidate']['costs']
    assert measured['confirmed_micro_cny']==measured['known_micro_cny']==10_000
    assert measured['estimated_micro_cny'] is None
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute('SELECT count(*) FROM model_costs WHERE company_id=?',(c.company['id'],)).fetchone()[0]==cost_count
    assert c.provider.extract_calls==extract_calls and c.provider.proposal_calls==proposal_calls


def test_confirmed_local_xml_and_cache_reuse_are_explicit_zero_send_not_unknown(ai_case):
    from test_stage16_structured_invoice import xml_event
    from test_intelligence_workflow import extract
    from app.quality.service import QualityService
    c=ai_case
    event=xml_event(c)
    local=extract(c,event)
    service=BenchmarkService(QualityService(c.db))
    run={'settings':{'mode':'stored_ocr'},'report':{'items':[{'provenance':{'run_ids':[local['id']]}}]}}
    with c.db.transaction(actor=c.manager) as conn:
        measured=service._costs(conn,c.manager,c.book['id'],run)
    assert measured['observed_calls']==0 and measured['no_send_records']==1
    assert measured['known_micro_cny']==0 and measured['unknown_calls']==0 and measured['complete']
