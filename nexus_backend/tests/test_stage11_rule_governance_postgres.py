import json
import io
from uuid import uuid4

import pytest
from PIL import Image

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate
from app.business.models import BusinessEventCreate
from app.ledgers.models import AliasCreate, AliasDecision, EntityCreate, EntityUpdate
from app.ledgers.service import LedgersService
from app.learning.models import RuleCreate, RuleEvaluate, RulePublish, RuleUpdate
from app.learning.service import LearningService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage8_learning import bank_sample
from test_stage7_proposal_postgres import pg_stage7, pg_queue, pg_ai, WORKER_URL
from test_intelligence_workflow import create_event, extract, handoff, invoice_document

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员和受限运行账号')


def setup(case, before_master=False):
    learning = LearningService(case.db)
    master = LedgersService(case.db)
    if before_master:
        positive = bank_sample(case.service, learning, case.book, case.owner)
    entity = master.create_entity(case.owner, case.book, EntityCreate(kind='counterparty', code='PARTY1', name='供应商甲'))
    if not before_master:
        positive = bank_sample(case.service, learning, case.book, case.owner)
    negative = bank_sample(case.service, learning, case.book, case.owner, external='negative', counterparty='供应商乙', corrected=False)
    rule = learning.overview(case.owner, case.book)['rules'][0]
    return learning, master, entity, positive, negative, rule


def test_native_scoped_matches_alias_and_rejects_ambiguous_or_foreign_identity(business_case):
    c = business_case
    learning, master, entity, positive, _, rule = setup(c)
    snapshot = json.loads(positive['snapshot_json'])
    snapshot['payload']['counterparty'] = '别名供方'
    alias = master.create_alias(c.owner, c.book, AliasCreate(entity_id=entity['id'], original='别名供方', reason='核对真实登记主体'))
    with c.db.transaction(actor=c.owner) as conn:
        assert not conn.execute('SELECT accounting.learning_matches_scoped(?,?::jsonb,?::jsonb)', (c.book, json.dumps(rule['definition']), json.dumps(snapshot))).fetchone()[0]
    master.decide_alias(c.reviewer, c.book, alias['id'], AliasDecision(expected_version=1, decision='approve', reason='独立核对真实票据'))
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute('SELECT accounting.learning_matches_scoped(?,?::jsonb,?::jsonb)', (c.book, json.dumps(rule['definition']), json.dumps(snapshot))).fetchone()[0]
        assert learning.matches_in_transaction(conn, c.owner, c.book, rule['definition'], snapshot)
        identity = conn.execute('SELECT accounting.learning_counterparty_identity(?,?)', (c.book, '别名供方')).fetchone()[0]
        assert identity['entity_id'] == entity['id']
    with c.db.transaction(actor=c.employee) as conn:
        assert conn.execute('SELECT accounting.learning_counterparty_identity(?,?)', (c.book, '供应商甲')).fetchone()[0] is None
    master.create_entity(c.owner, c.book, EntityCreate(kind='counterparty', code='PARTY2', name='别名供方'))
    with c.db.transaction(actor=c.owner) as conn:
        assert not conn.execute('SELECT accounting.learning_matches_scoped(?,?::jsonb,?::jsonb)', (c.book, json.dumps(rule['definition']), json.dumps(snapshot))).fetchone()[0]
        assert not learning.matches_in_transaction(conn, c.owner, c.book, rule['definition'], snapshot)


def test_postgres_stable_evaluation_and_publish_use_native_current_master_context(business_case):
    c = business_case
    learning, master, entity, _, negative, rule = setup(c)
    evaluated = learning.evaluate(c.owner, c.book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    assert evaluated['passed']
    master.update_entity(c.owner, c.book, entity['id'], EntityUpdate(expected_version=1, name='供应商甲', active=True, reason='重新核验主体资料并保留版本'))
    with pytest.raises(c.psycopg.Error, match='stale'):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute("UPDATE learning_rules SET status='published',state_version=2,evaluation_id=? WHERE id=?", (evaluated['id'], rule['id']))
    evaluated = learning.evaluate(c.owner, c.book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    published = learning.publish(c.owner, c.book, rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluated['id'], reason='主数据更新后重新核验真实样例'))
    assert published['status'] == 'published'


def test_sql_duplicate_semantics_and_foreign_id_cannot_bypass_python(business_case):
    c = business_case
    learning, master, _, positive, _, rule = setup(c)
    another = bank_sample(c.service, learning, c.book, c.owner, external='another-corrected', amount='51.01')
    assert learning.overview(c.owner, c.book)['pagination']['total'] == 3
    assert len(learning.overview(c.owner, c.book)['rules']) == 1
    foreign = master.create_entity(c.owner, c.other_book, EntityCreate(kind='counterparty', code='FOREIGN', name='供应商甲'))
    for definition, reason in [(rule['definition'], 'Duplicate'), ({**rule['definition'], 'conditions': {**rule['definition']['conditions'], 'counterparty_id': foreign['id']}}, 'own active company')]:
        with pytest.raises(c.psycopg.Error, match=reason):
            with c.db.transaction(write=True, actor=c.owner) as conn:
                if reason == 'Duplicate':
                    new_id = 'duplicate-' + rule['id']
                    conn.execute("INSERT INTO learning_rules SELECT ?,firm_id,company_id,book_id,period,1,1,'draft',NULL,?,created_at,updated_at FROM learning_rules WHERE id=?", (new_id, c.owner.user_id, rule['id']))
                else:
                    new_id = rule['id']
                    conn.execute('UPDATE learning_rules SET current_version=2,state_version=2 WHERE id=?', (new_id,))
                conn.execute('INSERT INTO learning_rule_versions(rule_id,book_id,version,definition_json,definition_fingerprint,sample_ids_json,reason,created_by,created_at) SELECT ?,book_id,?, ?,definition_fingerprint,sample_ids_json,reason,?,created_at FROM learning_rule_versions WHERE rule_id=? AND version=1', (new_id, 1 if reason == 'Duplicate' else 2, json.dumps(definition, ensure_ascii=False), c.owner.user_id, rule['id']))
    assert another['id'] != positive['id']


def test_sql_cannot_publish_overlapping_raw_and_identity_rules(business_case):
    c = business_case
    learning, _, entity, positive, negative, raw = setup(c, before_master=True)
    stable = learning.save_rule(c.owner, c.book, RuleCreate(definition={**raw['definition'], 'name': '稳定往来方规则', 'conditions': {**raw['definition']['conditions'], 'counterparty_id': entity['id']}}, sample_ids=[positive['id']], reason='升级为企业稳定身份条件'))
    raw_eval = learning.evaluate(c.owner, c.book, raw['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    stable_eval = learning.evaluate(c.owner, c.book, stable['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    assert raw_eval['passed'] and stable_eval['passed']
    learning.publish(c.owner, c.book, raw['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=raw_eval['id'], reason='显式发布精确原文规则'))
    with pytest.raises(c.psycopg.Error, match='overlapping'):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute("UPDATE learning_rules SET status='published',state_version=2,evaluation_id=? WHERE id=?", (stable_eval['id'], stable['id']))


@pytest.mark.skipif(not WORKER_URL, reason='需要独立 worker 测试连接')
def test_actual_worker_uses_independently_approved_alias_company_rule_without_model_call(pg_stage7):
    c = pg_stage7
    book = c.book['id']
    learning = LearningService(c.db)
    c.ledger.learning = learning
    master = LedgersService(c.db)
    entity = master.create_entity(c.manager, book, EntityCreate(kind='counterparty', code='BUS-PARTY', name='耗材商店'))
    alias = master.create_alias(c.manager, book, AliasCreate(entity_id=entity['id'], original='耗材供应商简称', reason='核对营业执照上的同一主体'))
    master.decide_alias(c.actors['accountant'], book, alias['id'], AliasDecision(expected_version=1, decision='approve', reason='会计独立核实发票主体'))
    event = create_event(c)
    extract(c, event)
    mapping = handoff(c, event)
    assert c.worker.run_once()
    draft = c.ledger.get_draft(c.actors['accountant'], book, mapping['draft_id'])
    draft = c.ledger.update_draft(c.manager, book, draft['id'], DraftUpdate(expected_version=draft['version'], description='会计核实为其他费用', correction_reason='合同实质独立确认', lines=[{'account_code': '6602.99', 'debit': '0.30', 'credit': '0.00'}, {'account_code': '2241', 'debit': '0.00', 'credit': '0.30'}]))
    c.ledger.approve(c.actors['accountant'], book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    negative = bank_sample(c.ledger, learning, book, c.manager, external='business-rule-negative', counterparty='不相关银行来源', corrected=False)
    rule = next(row for row in learning.overview(c.manager, book)['rules'] if row['definition']['source_kind'] == 'business')
    evaluated = learning.evaluate(c.manager, book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    assert evaluated['passed']
    learning.publish(c.manager, book, rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluated['id'], reason='审核后的本企业稳定往来方规则'))
    before_calls = c.provider.proposal_calls
    # A genuinely distinct synthetic file avoids reusing the first invoice's OCR.
    c.provider.document = invoice_document(seller_name='耗材供应商简称', invoice_number='RULE-' + uuid4().hex, buyer_tax_id=c.provider.document['buyer_tax_id'])
    # Freeze this second fixture's independent original, not the first ticket.
    c.original_text = c.provider.document['pages'][0]['raw_text']
    future = c.business.create(c.actors['employee'], BusinessEventCreate(company_id=c.company['id'], template_id='reimbursement', payload={**event['payload'], 'counterparty': '耗材供应商简称', 'description': '新事项沿用经审核身份规则'}))
    output = io.BytesIO()
    Image.new('RGB', (42, 21), 'blue').save(output, 'PNG')
    future = c.business.add_attachment(c.actors['employee'], future['id'], future['version'], c.storage.put('独立合成发票.png', 'image/png', output.getvalue()))
    extract(c, future, key='future-ocr-' + future['id'])
    mapping = handoff(c, future)
    assert c.worker.run_once()
    job = c.queue.proposals.get(c.actors['accountant'], mapping['automatic_job']['id'])
    assert job['status'] == 'succeeded', json.dumps(job, ensure_ascii=False)
    assert job['result_rule_id'] == rule['id'] and c.provider.proposal_calls == before_calls
    result = c.ledger.get_draft(c.actors['accountant'], book, mapping['draft_id'])
    assert result['status'] == 'draft' and result['lines'][0]['account_code'] == '6602.99'
