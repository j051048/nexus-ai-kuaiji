"""Independent SQL boundaries for reviewed lineage and publication."""
import json
from uuid import uuid4
import pytest

from app.learning.models import RuleEvaluate, RulePublish, RuleRetire
from app.learning.service import LearningService
from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountUpdate
from app.accounting.models import ApprovalRequest
from app.business.models import BusinessEventCreate, HandoffRequest
from app.business.service import BusinessService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage8_learning import bank_sample

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


@pytest.fixture
def learned(business_case):
    case = business_case
    learning = LearningService(case.db)
    positive = bank_sample(case.service, learning, case.book, case.owner)
    negative = bank_sample(case.service, learning, case.book, case.owner, external="negative", counterparty="供应商乙", corrected=False)
    rule = learning.overview(case.owner, case.book)["rules"][0]
    return case, learning, positive, negative, rule


def test_learning_tables_force_rls_no_worker_or_browser_access(learned):
    case, _, _, _, _ = learned
    tables = ['learning_samples', 'learning_rules', 'learning_rule_versions', 'learning_rule_evaluations']
    with case.psycopg.connect(ADMIN_URL) as conn:
        rows = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND relname=ANY(%s)", (tables,)).fetchall()
        assert len(rows) == 4 and all(enabled and forced for _, enabled, forced in rows)
        for table in tables:
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + table,)).fetchone()[0]
            assert not conn.execute("SELECT has_table_privilege('accounting_app',%s,'DELETE')", ('accounting.' + table,)).fetchone()[0]


def test_sql_examples_cannot_be_enumerated_by_employee_or_unassigned(learned):
    case, _, positive, _, rule = learned
    for actor in (case.employee, case.unassigned):
        with case.db.transaction(actor=actor) as conn:
            assert not conn.execute("SELECT id FROM learning_samples WHERE id=?", (positive['id'],)).fetchall()
            assert not conn.execute("SELECT id FROM learning_rules WHERE id=?", (rule['id'],)).fetchall()
            assert not conn.execute("SELECT * FROM learning_rule_versions WHERE rule_id=?", (rule['id'],)).fetchall()
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT id FROM learning_samples WHERE id=?", (positive['id'],)).fetchone()


def test_sql_other_company_samples_and_rule_metadata_are_hidden(learned):
    case, learning, _, _, _ = learned
    other = bank_sample(case.service, learning, case.other_book, case.owner, external="foreign-company", counterparty="秘密客户供应商")
    with case.db.transaction(actor=case.reviewer) as conn:
        assert not conn.execute("SELECT * FROM learning_samples WHERE id=?", (other['id'],)).fetchall()
        assert not conn.execute("SELECT * FROM learning_rules WHERE book_id=?", (case.other_book,)).fetchall()


def test_sql_editor_cannot_publish_or_rewrite_learning_history(learned):
    case, _, positive, _, rule = learned
    with case.db.transaction(write=True, actor=case.finance) as conn:
        assert conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1 WHERE id=?", (rule['id'],)).rowcount == 0
    for table, predicate, value in [('learning_samples', 'id', positive['id']), ('learning_rule_versions', 'rule_id', rule['id'])]:
        with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
            with case.db.transaction(write=True, actor=case.owner) as conn:
                conn.execute(f"DELETE FROM {table} WHERE {predicate}=?", (value,))


def test_sql_publication_cannot_skip_passing_evaluation(learned):
    case, _, _, _, rule = learned
    with pytest.raises(case.psycopg.Error, match="passing current evaluation"):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1 WHERE id=?", (rule['id'],))


def test_postgres_evaluate_publish_retire_with_immutable_receipt(learned):
    case, learning, _, negative, rule = learned
    result = learning.evaluate(case.owner, case.book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    assert result['passed']
    published = learning.publish(case.owner, case.book, rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=result['id'], reason='真实样例核实后发布'))
    assert published['status'] == 'published'
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE learning_rule_evaluations SET report_json='{}' WHERE id=?", (result['id'],))
    assert learning.retire(case.owner, case.book, rule['id'], RuleRetire(expected_version=1, expected_state_version=2, reason='客户业务变更暂停'))['status'] == 'retired'


def test_sql_rule_scope_and_state_version_cannot_be_forged(learned):
    case, _, _, _, rule = learned
    for sql, values in [
        ("UPDATE learning_rules SET company_id=?,state_version=state_version+1 WHERE id=?", (case.other_company, rule['id'])),
        ("UPDATE learning_rules SET status='retired' WHERE id=?", (rule['id'],)),
    ]:
        with pytest.raises(case.psycopg.Error):
            with case.db.transaction(write=True, actor=case.owner) as conn:
                conn.execute(sql, values)


def raw_evaluation(case, rule, negatives, report, claimed_db_fp='f' * 32):
    receipt = uuid4().hex
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("INSERT INTO learning_rule_evaluations(id,rule_id,book_id,rule_version,negative_sample_ids_json,input_fingerprint,report_json,db_input_fingerprint,created_by,created_at) VALUES(?,?,?,1,?,?,?,?,?,'2026-10-12T00:00:00+00:00')", (receipt, rule['id'], case.book, json.dumps(negatives), 'f' * 64, json.dumps(report), claimed_db_fp, case.owner.user_id))
    return receipt


def test_sql_evaluation_rejects_positive_negative_intersection(learned):
    case, _, positive, _, rule = learned
    with pytest.raises(case.psycopg.Error, match='must be distinct'):
        raw_evaluation(case, rule, [positive['id']], {'passed': True, 'positive_count': 1, 'negative_count': 1, 'scanned_count': 2, 'matched_count': 1})


def test_sql_evaluation_rejects_fabricated_passing_report_and_wrong_counts(learned):
    case, _, _, negative, rule = learned
    report = {'passed': True, 'positive_count': 1, 'negative_count': 1, 'scanned_count': 200, 'matched_count': 1}
    with pytest.raises(case.psycopg.Error, match='does not match deterministic'):
        raw_evaluation(case, rule, [negative['id']], report)
    bank_sample(case.service, LearningService(case.db), case.book, case.owner, external='known-conflicting', corrected=False)
    report.update(scanned_count=3, matched_count=2)
    with pytest.raises(case.psycopg.Error, match='does not match deterministic'):
        raw_evaluation(case, rule, [negative['id']], report)


def test_sql_database_fingerprint_is_server_computed_not_client_claimed(learned):
    case, _, _, negative, rule = learned
    report = {'passed': True, 'positive_count': 1, 'negative_count': 1, 'scanned_count': 2, 'matched_count': 1}
    receipt = raw_evaluation(case, rule, [negative['id']], report)
    with case.db.transaction(actor=case.owner) as conn:
        stored = conn.execute("SELECT db_input_fingerprint FROM learning_rule_evaluations WHERE id=?", (receipt,)).fetchone()[0]
        assert stored != 'f' * 32
        assert stored == conn.execute("SELECT accounting.learning_db_context(?,?,1)", (rule['id'], case.book)).fetchone()[0]


@pytest.mark.parametrize('change', ['sample', 'catalog'])
def test_sql_direct_publish_rejects_stale_database_context(learned, change):
    case, learning, _, negative, rule = learned
    evaluated = learning.evaluate(case.owner, case.book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    if change == 'sample':
        bank_sample(case.service, learning, case.book, case.owner, external='new-lineage', counterparty='供应商丙', corrected=False)
    else:
        CatalogService(case.db).update_account(case.owner, case.book, '1002', AccountUpdate(code='1002', name='更新后的银行科目', expected_version=1))
    with pytest.raises(case.psycopg.Error, match='database context is stale'):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE learning_rules SET status='published',state_version=state_version+1,evaluation_id=? WHERE id=?", (evaluated['id'], rule['id']))


@pytest.mark.parametrize('field', ['counterparty', 'amount', 'final'])
def test_sql_sample_insert_rejects_manufactured_negative_snapshot(learned, field):
    case, _, positive, _, _ = learned
    item = dict(positive)
    item['id'] = uuid4().hex
    snapshot, final = json.loads(item['snapshot_json']), json.loads(item['final_json'])
    if field == 'final':
        final['lines'][0]['account_code'] = '6602.03'
    else:
        snapshot['payload'][field] = '伪交易对方' if field == 'counterparty' else '999.00'
    item['snapshot_json'], item['final_json'] = json.dumps(snapshot), json.dumps(final)
    message = 'Learning identity projection must match current scoped master data' if field == 'counterparty' else 'does not match frozen journal'
    with pytest.raises(case.psycopg.Error, match=message):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute('INSERT INTO learning_samples('+','.join(item)+') VALUES('+','.join('?' for _ in item)+')', list(item.values()))


def test_sql_business_sample_facts_cannot_be_relabelled(business_case):
    case = business_case
    business, learning = BusinessService(case.db), LearningService(case.db)
    case.service.learning = learning
    event = business.create(case.employee, BusinessEventCreate(company_id=case.company, template_id='procurement_expense', payload={
        'occurred_on':'2026-10-03','description':'真实业务事项','counterparty':'真实交易对方','amount':'113.00','direction':'expense','payment_status':'paid','category':'office'}))
    event = business.add_attachment(case.employee, event['id'], event['version'], {'id':uuid4().hex,'storage_key':uuid4().hex,'filename':'合成票据.pdf','media_type':'application/pdf','sha256':'a'*64,'size_bytes':100})
    event = business.submit(case.employee, event['id'], event['version'])
    event = business.approve_department(case.approver, event['id'], event['version'])
    event = business.approve_finance(case.finance, event['id'], event['version'])
    mapped = business.handoff(case.finance, event['id'], HandoffRequest(expected_version=event['version'], book_id=case.book, idempotency_key=uuid4().hex))
    draft = case.service.get_draft(case.reviewer, case.book, mapped['draft_id'])
    case.service.approve(case.reviewer, case.book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    with case.db.transaction(actor=case.reviewer) as conn:
        sample = dict(conn.execute('SELECT * FROM learning_samples WHERE draft_id=?', (draft['id'],)).fetchone())
        snapshot, final = json.loads(sample['snapshot_json']), json.loads(sample['final_json'])
        assert conn.execute('SELECT accounting.learning_sample_truth(?,?,?,?,?)', (case.book,draft['id'],sample['source_id'],json.dumps(snapshot),json.dumps(final))).fetchone()[0]
        for mutation in ('category','payment_status','business_type','department','fact_version'):
            changed = json.loads(json.dumps(snapshot))
            if mutation in {'category','payment_status'}:
                changed['payload'][mutation] = 'other' if mutation == 'category' else 'unpaid'
            elif mutation == 'business_type':
                changed['business_type'] = 'reimbursement'
            elif mutation == 'department':
                changed['context']['department_id'] = case.other_department
            else:
                changed['fact_version'] += 1
            assert not conn.execute('SELECT accounting.learning_sample_truth(?,?,?,?,?)', (case.book,draft['id'],sample['source_id'],json.dumps(changed),json.dumps(final))).fetchone()[0]
