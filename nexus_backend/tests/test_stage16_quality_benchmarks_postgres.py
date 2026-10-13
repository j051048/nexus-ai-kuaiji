"""Benchmark creation and read-only gates use restricted native PostgreSQL roles."""
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.catalog_models import AccountUpdate
from app.accounting.catalog import CatalogService
from app.quality.benchmarks import BenchmarkService
from app.quality.models import StandardCreate, StandardConfirm
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_stage11_quality_postgres import quality_case
from test_stage11_quality import ai_standard
from test_stage16_quality_benchmarks import benchmark_request, replay, comparison

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason='需要专用 PostgreSQL 管理及受限运行连接')


def test_pg_fixed_set_idempotency_and_journal_replay_preserve_production(quality_case):
    c,quality,row=quality_case
    service=BenchmarkService(quality)
    request=benchmark_request([row['id']])
    bench=service.create(c.reviewer,c.book,request)
    assert service.create(c.reviewer,c.book,request)['id']==bench['id']
    assert service.create(c.reviewer,c.book,benchmark_request([row['id']]))['version']==2
    baseline,candidate=replay(quality,c.reviewer,c.book,[row['id']]),replay(quality,c.reviewer,c.book,[row['id']])
    result=comparison(service,c.reviewer,c.book,bench,baseline,candidate)
    assert result['report']['eligible'] and result['report']['counts']['passed']==1
    assert result['candidate']['costs']['known_micro_cny']==0
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT status FROM drafts WHERE id=?',(row['draft_id'],)).fetchone()[0]=='approved'
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.owner) as conn:
            conn.execute("UPDATE quality_benchmarks SET reason='抹除历史' WHERE id=?",(bench['id'],))


def test_pg_native_benchmark_guard_rejects_duplicate_sample_unknown_and_unconfirmed(quality_case):
    c,quality,row=quality_case
    service=BenchmarkService(quality)
    bench=service.create(c.owner,c.book,benchmark_request([row['id']]))
    pending=quality.create_standard(c.owner,c.book,StandardCreate(sample_id=row['sample_id'],reason='尚未确认的新标签',idempotency_key=uuid4().hex))
    def insert(ids):
        with c.db.transaction(write=True,actor=c.owner) as conn:
            conn.execute('INSERT INTO quality_benchmarks SELECT ?,firm_id,company_id,book_id,?,1,?,reason,created_by,created_at,?,request_fingerprint FROM quality_benchmarks WHERE id=?',
                (uuid4().hex,uuid4().hex,json.dumps(ids),uuid4().hex,bench['id']))
    for ids in ([row['id'],row['id']],[pending['id']],['missing-id'],[12]):
        with pytest.raises(c.psycopg.Error,match='independently confirmed standards'):
            insert(ids)
    quality.confirm(c.reviewer,c.book,pending['id'],StandardConfirm(evidence_checked=True,reason='另一会计确认标签版本'))
    with pytest.raises(c.psycopg.Error,match='independently confirmed standards'):
        insert([row['id'],pending['id']])


def test_pg_benchmark_force_rls_unassigned_editor_viewer_and_worker_denied(quality_case):
    c,quality,row=quality_case
    service=BenchmarkService(quality)
    bench=service.create(c.owner,c.book,benchmark_request([row['id']]))
    for actor in (c.viewer,c.finance):
        with c.db.transaction(actor=actor) as conn:
            assert conn.execute('SELECT * FROM quality_benchmarks WHERE id=?',(bench['id'],)).fetchall()==[]
        with pytest.raises(AccountingError): service.get(actor,c.book,bench['id'])
    with pytest.raises(AccountingError): service.get(c.reviewer,c.other_book,bench['id'])
    with c.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='accounting.quality_benchmarks'::regclass").fetchone()[0]
        assert not conn.execute("SELECT has_table_privilege('accounting_worker','accounting.quality_benchmarks','SELECT')").fetchone()[0]
        conn.execute('DELETE FROM accounting.book_access WHERE user_id=%s AND book_id=%s',(c.reviewer.user_id,c.book))
    with pytest.raises(AccountingError): service.get(c.reviewer,c.book,bench['id'])


def test_pg_stale_candidate_rejected_and_archived_baseline_can_be_rechecked(quality_case):
    c,quality,row=quality_case
    service=BenchmarkService(quality)
    bench=service.create(c.owner,c.book,benchmark_request([row['id']]))
    baseline,candidate=replay(quality,c.owner,c.book,[row['id']]),replay(quality,c.owner,c.book,[row['id']])
    CatalogService(c.db).update_account(c.owner,c.book,'1002',AccountUpdate(code='1002',name='新的已审账户名称',expected_version=1))
    assert not comparison(service,c.owner,c.book,bench,baseline,candidate)['report']['eligible']
    current=replay(quality,c.owner,c.book,[row['id']])
    compared=comparison(service,c.owner,c.book,bench,baseline,current)
    assert compared['report']['eligible'] and compared['report']['baseline_archived']


def test_pg_source_evidence_changes_invalidate_the_fixed_set(quality_case):
    c,quality,row=quality_case
    service=BenchmarkService(quality)
    bench=service.create(c.owner,c.book,benchmark_request([row['id']]))
    baseline,candidate=replay(quality,c.owner,c.book,[row['id']]),replay(quality,c.owner,c.book,[row['id']])
    with c.db.transaction(write=True,actor=c.owner) as conn:
        conn.execute('INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_by,created_at) VALUES(?,?,?,?,?)',(row['source_id'],c.book,'another-confirmed-original',c.owner.user_id,'now'))
    assert service.get(c.owner,c.book,bench['id'])['stale']
    assert not comparison(service,c.owner,c.book,bench,baseline,candidate)['report']['eligible']


def test_pg_model_comparison_cost_acl_and_missing_evidence_are_honest(pg_ai):
    c=pg_ai
    quality,row,_,proposal,extract_calls,proposal_calls=ai_standard(c)
    service,book=BenchmarkService(quality),c.book['id']
    bench=service.create(c.manager,book,benchmark_request([row['id']]))
    args={'model':proposal['model'],'prompt_version':proposal['prompt_version']}
    baseline,candidate=replay(quality,c.manager,book,[row['id']],mode='stored_model',**args),replay(quality,c.manager,book,[row['id']],mode='stored_model',**args)
    result=comparison(service,c.manager,book,bench,baseline,candidate)
    assert result['report']['eligible']
    assert result['candidate']['costs']['unknown_calls']==1 and result['candidate']['costs']['known_micro_cny'] is None
    reviewer=comparison(service,c.actors['accountant'],book,bench,baseline,candidate)
    assert reviewer['candidate']['costs']['measurement']=='not_authorized'
    assert c.provider.extract_calls==extract_calls and c.provider.proposal_calls==proposal_calls
