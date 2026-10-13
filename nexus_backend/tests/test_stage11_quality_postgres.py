"""Native report, confirmation and scope tests on a dedicated PostgreSQL role."""
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.learning.models import RuleEvaluate, RulePublish
from app.learning.service import LearningService
from app.quality.models import RunCreate, StandardConfirm, StandardCreate
from app.quality.service import QualityService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage8_learning import bank_sample
from test_intelligence_workflow_postgres import pg_ai
from test_stage11_quality import ai_standard, alias_replay

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理及受限运行连接')


@pytest.fixture
def quality_case(business_case):
    c = business_case
    learning = LearningService(c.db)
    positive = bank_sample(c.service, learning, c.book, c.owner, external='quality-positive')
    negative = bank_sample(c.service, learning, c.book, c.owner, external='quality-negative', counterparty='另一真实类型供方', corrected=False)
    rule = learning.overview(c.owner, c.book)['rules'][0]
    evaluation = learning.evaluate(c.owner, c.book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    learning.publish(c.owner, c.book, rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluation['id'], reason='仅用于此账套的真实审核样例回放'))
    quality = QualityService(c.db)
    standard = quality.create_standard(c.owner, c.book, StandardCreate(sample_id=positive['id'], reason='原件和最终已审分录核对', idempotency_key=uuid4().hex))
    quality.confirm(c.reviewer, c.book, standard['id'], StandardConfirm(evidence_checked=True, reason='另一位会计独立核对'))
    return c, quality, standard


def test_postgres_replay_exact_with_native_report(quality_case):
    c, quality, standard = quality_case
    run = quality.create_run(c.reviewer, c.book, RunCreate(standard_ids=[standard['id']], mode='rules', idempotency_key=uuid4().hex))
    assert run['report']['counts'] == {'passed': 1, 'different': 0, 'uncovered': 0, 'stale': 0}
    assert not run['stale']
    assert quality.overview(c.reviewer, c.book)['human_minutes'] is None


def test_native_fabricated_passed_report_and_missing_mode_rejected(quality_case):
    c, quality, standard = quality_case
    run = quality.create_run(c.owner, c.book, RunCreate(standard_ids=[standard['id']], mode='templates', idempotency_key=uuid4().hex))
    assert run['report']['counts']['uncovered'] == 1
    fabricated = {**run['report'], 'counts': {'passed': 1, 'different': 0, 'uncovered': 0, 'stale': 0}}
    with pytest.raises(c.psycopg.Error, match='native current replay'):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute('INSERT INTO quality_runs SELECT ?,firm_id,company_id,book_id,period,standard_ids_json,settings_json,input_fingerprint,?,created_by,created_at,?,request_fingerprint FROM quality_runs WHERE id=?', (uuid4().hex, json.dumps(fabricated), uuid4().hex, run['id']))
    for settings in ({}, {'mode': None}):
        with pytest.raises(c.psycopg.Error, match='Invalid bounded'):
            with c.db.transaction(actor=c.owner) as conn:
                conn.execute('SELECT accounting.quality_report(?,?,?)', (c.book, json.dumps([standard['id']]), json.dumps(settings)))


def test_native_independent_confirmation_and_null_field_rejected(quality_case):
    c, quality, standard = quality_case
    source = quality.create_standard(c.owner, c.book, StandardCreate(sample_id=standard['sample_id'], reason='新的一组人为测量标签', idempotency_key=uuid4().hex))
    with pytest.raises(c.psycopg.Error, match='Another reviewer'):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute("INSERT INTO quality_confirmations VALUES(?,?,?,?,'自证不允许',1,'now')", (source['id'], c.book, c.owner.firm_id, c.owner.user_id))
    bad = [{'extraction_id': 'any', 'document_index': 0, 'page': 1, 'field': None, 'value': None}]
    with pytest.raises(c.psycopg.Error, match='Invalid human field'):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute('INSERT INTO quality_standards SELECT ?,firm_id,company_id,book_id,period,sample_id,draft_id,source_id,snapshot_json,final_json,evidence_json,?,reason,created_by,created_at,?,request_fingerprint FROM quality_standards WHERE id=?', (uuid4().hex, json.dumps(bad), uuid4().hex, source['id']))


def test_postgres_force_rls_revocation_and_worker_denial(quality_case):
    c, quality, standard = quality_case
    with c.db.transaction(actor=c.viewer) as conn:
        assert conn.execute('SELECT count(*) FROM quality_standards WHERE book_id=?', (c.book,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM quality_confirmations WHERE book_id=?', (c.book,)).fetchone()[0] == 0
        with pytest.raises(c.psycopg.Error):
            conn.execute('SELECT accounting.quality_evidence(?,?)', (c.book, standard['sample_id']))
    with c.psycopg.connect(ADMIN_URL) as conn:
        tables = conn.execute("SELECT relname,relforcerowsecurity FROM pg_class JOIN pg_namespace ON pg_namespace.oid=pg_class.relnamespace WHERE nspname='accounting' AND relname IN ('quality_standards','quality_confirmations','quality_runs')").fetchall()
        assert len(tables) == 3 and all(row[1] for row in tables)
        for table in ('quality_standards', 'quality_confirmations', 'quality_runs'):
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + table,)).fetchone()[0]
        for name in ('quality_evidence(text,text)', 'quality_report(text,jsonb,jsonb)', 'quality_context(text,jsonb,jsonb)'):
            assert not conn.execute("SELECT has_function_privilege('accounting_worker',%s,'EXECUTE')", ('accounting.' + name,)).fetchone()[0]
        conn.execute('DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s', (c.book, c.reviewer.user_id))
    with pytest.raises(AccountingError):
        quality.standard(c.reviewer, c.book, standard['id'])


@pytest.mark.parametrize('mode', ['templates', 'stored_model', 'stored_ocr'])
def test_postgres_real_versioned_replay_does_not_call_supplier(pg_ai, mode):
    c = pg_ai
    quality, row, ocr, proposal, extraction_count, proposal_count = ai_standard(c)
    candidate = ocr if mode == 'stored_ocr' else proposal
    request = RunCreate(standard_ids=[row['id']], mode=mode, idempotency_key=uuid4().hex, **({'model': candidate['model'], 'prompt_version': candidate['prompt_version']} if mode.startswith('stored_') else {}))
    run = quality.create_run(c.manager, c.book['id'], request)
    assert run['report']['counts']['passed'] == 1, run
    assert c.provider.extract_calls == extraction_count and c.provider.proposal_calls == proposal_count


def test_postgres_human_field_answer_exposes_exact_ocr_difference(pg_ai):
    c = pg_ai
    quality, row, ocr, _, _, _ = ai_standard(c, changed_label=True)
    run = quality.create_run(c.manager, c.book['id'], RunCreate(standard_ids=[row['id']], mode='stored_ocr', model=ocr['model'], prompt_version=ocr['prompt_version'], idempotency_key=uuid4().hex))
    assert run['report']['counts']['different'] == 1
    assert run['report']['items'][0]['differences'][0]['field'] == 'seller_name'


def test_postgres_approved_alias_retirement_invalidates_old_run_and_coverage(business_case):
    c = business_case
    alias_replay(c.service, c.owner, c.reviewer, c.book)


def test_postgres_month_type_report_preserves_history_and_does_not_score_stale_configuration(quality_case):
    c, quality, standard = quality_case
    first = quality.create_run(c.reviewer, c.book, RunCreate(standard_ids=[standard['id']], mode='rules', idempotency_key=uuid4().hex))
    latest = quality.create_run(c.reviewer, c.book, RunCreate(standard_ids=[standard['id']], mode='rules', idempotency_key=uuid4().hex))
    quality.create_run(c.reviewer, c.book, RunCreate(standard_ids=[standard['id']], mode='templates', idempotency_key=uuid4().hex))
    groups = quality.report(c.reviewer, c.book)['groups']
    assert len(groups) == 2
    rules = next(row for row in groups if row['mode'] == 'rules')
    assert rules['business_type'] == 'daily_cashflow' and rules['run_id'] == latest['id'] != first['id']
    assert rules['sample_count'] == 1 and rules['measured_match_rate'] == 1 and rules['human_minutes'] is None
    from app.accounting.catalog import CatalogService
    from app.accounting.catalog_models import AccountUpdate
    CatalogService(c.db).update_account(c.owner, c.book, '1002', AccountUpdate(code='1002', name='当前科目版本变化', expected_version=1))
    groups = quality.report(c.reviewer, c.book)['groups']
    assert all(row['context_stale'] and row['measured_match_rate'] is None for row in groups)
    assert quality.report(c.owner, c.other_book)['groups'] == []
    with pytest.raises(AccountingError):
        quality.report(c.viewer, c.book)
