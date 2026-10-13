"""Actual dedicated PostgreSQL worker company-rule receipts, synthetic OCR only."""
from uuid import uuid4

import pytest

from app.accounting.catalog_models import AccountDefinition
from app.learning.models import RuleEvaluate, RulePublish
from app.learning.service import LearningService
from test_stage7_proposal_postgres import pg_stage7
from test_queue_worker_postgres import ADMIN_URL, APP_URL, WORKER_URL, business_case, business_postgres, pg_ai, pg_queue
import test_stage8_rule_queue_integration as synthetic
from test_intelligence_workflow import invoice_document

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PostgreSQL 管理员、API、独立 worker 测试连接")


@pytest.fixture
def published(pg_stage7, monkeypatch):
    case = pg_stage7
    tax_id = case.provider.document['buyer_tax_id']
    # Preserve the actual isolated company's tax ID in synthetic documents.
    monkeypatch.setattr(synthetic, 'invoice_document', lambda **values: invoice_document(buyer_tax_id=tax_id, **values))
    learning = LearningService(case.db)
    case.ledger.learning = case.intelligence.ledger.learning = learning
    case.business.proposal_queue = None
    case.catalog.create_account(case.manager, case.book['id'], AccountDefinition(code='6602.88', name='企业规则PG辅助科目', auxiliary_requirements=['counterparty']))
    synthetic.seed_sample(case, learning, 1, corrected=True, account='6602.88', auxiliaries=True)
    negative = synthetic.seed_sample(case, learning, 2, counterparty='反例商店')
    rule = learning.overview(case.actors['accountant'], case.book['id'])['rules'][0]
    evaluated = learning.evaluate(case.actors['accountant'], case.book['id'], rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative]))
    assert evaluated['passed']
    case.published_rule = learning.publish(case.actors['accountant'], case.book['id'], rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluated['id'], reason='独立PG正反例与辅助核算均复核后显式发布'))
    case.business.proposal_queue = case.queue.proposals
    return case


def test_real_pg_company_rule_worker_receipt_and_decimal_aux_contract(published):
    case = published
    mapped = synthetic.target(case, amount='1.20')
    assert case.worker.run_once() and case.worker.run_once()
    task, draft = synthetic.state(case, mapped)
    assert task['status'] == 'succeeded', task
    assert task['result_rule_id'] == case.published_rule['id'] and task['result_rule_version'] == 1
    assert task['result_template_id'] is None and task['result_template_version'] is None and task['current_run_id'] is None
    assert task['result_draft_version'] == draft['version'] == 2 and draft['status'] == 'draft'
    assert not draft['issues'] and case.provider.proposal_calls == 0
    line = next(row for row in draft['lines'] if row['account_code'] == '6602.88')
    assert line['debit'] == '1.20' and line['auxiliary_values'] == {'counterparty':'耗材商店'}


def test_real_pg_api_insert_and_retry_cannot_manufacture_rule_receipt(published):
    case = published
    mapped = synthetic.target(case)
    case.queue.proposals.cancel(case.actors['finance'], mapped['automatic_job']['id'], '明确取消后核对API回执写入权限')
    with case.db.transaction(actor=case.actors['finance']) as conn:
        original = dict(conn.execute('SELECT * FROM proposal_jobs WHERE id=?', (mapped['automatic_job']['id'],)).fetchone())
    forged = dict(original)
    forged.update(id=uuid4().hex, idempotency_key=uuid4().hex, status='queued', finished_at=None, error_code='', error_detail='',
                  result_rule_id=case.published_rule['id'], result_rule_version=1)
    with pytest.raises(case.psycopg.Error, match='cannot manufacture a company rule result'):
        with case.db.transaction(write=True, actor=case.actors['finance']) as conn:
            conn.execute('INSERT INTO proposal_jobs('+','.join(forged)+') VALUES('+','.join('?' for _ in forged)+')', list(forged.values()))
    with pytest.raises(case.psycopg.Error, match='cannot manufacture a company rule result'):
        with case.db.transaction(write=True, actor=case.actors['finance']) as conn:
            conn.execute("UPDATE proposal_jobs SET status='queued',generation=generation+1,finished_at=NULL,error_code='',error_detail='',result_rule_id=?,result_rule_version=1 WHERE id=?", (case.published_rule['id'], original['id']))
    assert case.queue.proposals.get(case.actors['accountant'], original['id'])['status'] == 'cancelled'


def test_real_pg_api_and_worker_raw_table_privileges_remain_separate(published):
    case = published
    with case.db.transaction(actor=case.actors['accountant']) as conn:
        assert not conn.execute("SELECT has_function_privilege(current_user,'accounting.worker_finish_proposal_company_rule(text,text,text,integer,integer)','EXECUTE')").fetchone()[0]
    with case.worker.queue.worker_transaction() as conn:
        for table in ('learning_samples','learning_rules','learning_rule_versions','learning_rule_evaluations'):
            assert not conn.execute('SELECT has_table_privilege(current_user,?,?)', ('accounting.'+table,'SELECT,INSERT,UPDATE,DELETE')).fetchone()[0]
