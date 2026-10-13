import json
import sqlite3

import pytest

from app.accounting.domain import AccountingError
from app.ledgers.models import AliasCreate, AliasDecision, EntityCreate, EntityUpdate
from app.ledgers.service import LedgersService
from app.learning.models import RuleCreate, RuleEvaluate, RulePublish, RuleRetire, RuleUpdate
from app.learning.service import LearningService
from app.learning.governance import semantic_fingerprint
from test_business import business
from test_stage8_learning import bank_sample


def setup(case, before_master=False):
    book = case.book['id']
    learning = LearningService(case.db)
    master = LedgersService(case.db)
    if before_master:
        positive = bank_sample(case.ledger, learning, book, case.manager)
    entity = master.create_entity(case.manager, book, EntityCreate(kind='counterparty', code='P001', name='供应商甲'))
    if not before_master:
        positive = bank_sample(case.ledger, learning, book, case.manager)
    negative = bank_sample(case.ledger, learning, book, case.manager, external='negative', counterparty='供应商乙', corrected=False)
    rule = learning.overview(case.manager, book)['rules'][0]
    return learning, master, entity, positive, negative, rule


def publish(case, learning, negative, rule):
    evaluation = learning.evaluate(case.manager, case.book['id'], rule['id'], RuleEvaluate(expected_version=rule['current_version'], negative_sample_ids=[negative['id']]))
    assert evaluation['passed']
    return learning.publish(case.manager, case.book['id'], rule['id'], RulePublish(expected_version=rule['current_version'], expected_state_version=rule['state_version'], evaluation_id=evaluation['id'], reason='真实正反例及合同用途独立核验'))


def generated(case, learning, snapshot, party):
    with case.db.transaction(actor=case.manager) as conn:
        return learning.rule_generate_in_transaction(conn, case.manager, case.book['id'], 'daily_cashflow', {**snapshot['payload'], 'counterparty': party, 'amount': '27.01'}, 2701, source_kind='bank')


def test_stable_rule_matches_only_independently_approved_alias_without_changing_evidence(business):
    c = business
    learning, master, entity, positive, negative, rule = setup(c)
    assert rule['definition']['conditions']['counterparty_id'] == entity['id']
    detail = learning.get_sample(c.manager, c.book['id'], positive['id'])
    assert detail['counterparty_identity']['entity_id'] == entity['id']
    alias = master.create_alias(c.manager, c.book['id'], AliasCreate(entity_id=entity['id'], original='供应商甲简称', reason='核对营业执照和发票抬头'))
    publish(c, learning, negative, rule)
    assert generated(c, learning, detail['snapshot'], '供应商甲简称') is None
    with pytest.raises(AccountingError, match='独立复核'):
        master.decide_alias(c.manager, c.book['id'], alias['id'], AliasDecision(expected_version=1, decision='approve', reason='不能自己审核映射'))
    master.decide_alias(c.actors['accountant'], c.book['id'], alias['id'], AliasDecision(expected_version=1, decision='approve', reason='独立核对统一社会信用代码'))
    value = generated(c, learning, detail['snapshot'], '供应商甲简称')
    assert value['rule_id'] == rule['id'] and value['lines'][0] == ('6602.99', 2701, 0)
    assert learning.get_sample(c.manager, c.book['id'], positive['id'])['snapshot'] == detail['snapshot']


def test_master_change_invalidates_evaluation_and_retired_alias_stops_matching(business):
    c = business
    learning, master, entity, positive, negative, rule = setup(c)
    evaluation = learning.evaluate(c.manager, c.book['id'], rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    alias = master.create_alias(c.manager, c.book['id'], AliasCreate(entity_id=entity['id'], original='甲简称', reason='申请明确身份映射'))
    with pytest.raises(AccountingError, match='变化'):
        learning.publish(c.manager, c.book['id'], rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluation['id'], reason='旧评估不应直接发布'))
    master.decide_alias(c.actors['accountant'], c.book['id'], alias['id'], AliasDecision(expected_version=1, decision='approve', reason='独立核对通过'))
    publish(c, learning, negative, rule)
    snapshot = json.loads(positive['snapshot_json'])
    assert generated(c, learning, snapshot, '甲简称')
    master.decide_alias(c.actors['accountant'], c.book['id'], alias['id'], AliasDecision(expected_version=2, decision='retire', reason='映射不再适用'))
    assert generated(c, learning, snapshot, '甲简称') is None
    master.update_entity(c.manager, c.book['id'], entity['id'], EntityUpdate(name='供应商甲', active=False, expected_version=1, reason='主体已停用，不再参与规则匹配'))
    assert generated(c, learning, snapshot, '供应商甲') is None


def test_ambiguous_alias_and_direct_name_never_choose_an_entity(business):
    c = business
    learning, master, entity, positive, negative, rule = setup(c)
    alias = master.create_alias(c.manager, c.book['id'], AliasCreate(entity_id=entity['id'], original='同名供方', reason='已查验甲供应商名称'))
    master.decide_alias(c.actors['accountant'], c.book['id'], alias['id'], AliasDecision(expected_version=1, decision='approve', reason='复核发票抬头'))
    master.create_entity(c.manager, c.book['id'], EntityCreate(kind='counterparty', code='P002', name='同名供方'))
    with c.db.transaction(actor=c.manager) as conn:
        assert learning.resolve_counterparty_in_transaction(conn, c.manager, c.book['id'], '同名供方')['status'] == 'ambiguous'
    publish(c, learning, negative, rule)
    assert generated(c, learning, json.loads(positive['snapshot_json']), '同名供方') is None
    with pytest.raises(AccountingError, match='歧义'):
        learning.save_rule(c.manager, c.book['id'], RuleCreate(definition={**rule['definition'], 'conditions': {**rule['definition']['conditions'], 'counterparty': '同名供方'}}, sample_ids=[positive['id']], reason='歧义不能作为稳定规则条件'))


def test_candidate_dedup_keeps_independent_samples_and_never_merges_evidence(business):
    c = business
    learning, _, _, positive, _, rule = setup(c)
    another = bank_sample(c.ledger, learning, c.book['id'], c.manager, external='second-positive', amount='31.00')
    overview = learning.overview(c.manager, c.book['id'])
    assert overview['pagination']['total'] == 3 and len(overview['rules']) == 1
    assert overview['rules'][0]['sample_ids'] == [positive['id']] and another['id'] != positive['id']
    renamed = {**rule['definition'], 'name': '名称变更但处理相同', 'lines': list(reversed(rule['definition']['lines']))}
    assert semantic_fingerprint(renamed) == semantic_fingerprint(rule['definition'])
    with pytest.raises(AccountingError, match='同语义'):
        learning.save_rule(c.manager, c.book['id'], RuleCreate(definition=renamed, sample_ids=[another['id']], reason='不可自动合并别人的原件'))


def test_raw_rule_remains_exact_and_new_stable_overlap_cannot_publish(business):
    c = business
    learning, _, entity, positive, negative, raw = setup(c, before_master=True)
    assert not raw['definition']['conditions'].get('counterparty_id')
    stable = learning.save_rule(c.manager, c.book['id'], RuleCreate(definition={**raw['definition'], 'name': '稳定身份规则', 'conditions': {**raw['definition']['conditions'], 'counterparty_id': entity['id']}}, sample_ids=[positive['id']], reason='明确使用当前稳定往来方'))
    publish(c, learning, negative, raw)
    evaluation = learning.evaluate(c.manager, c.book['id'], stable['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    assert not evaluation['passed'] and evaluation['conflicts'][0]['rule_id'] == raw['id']
    assert learning.governance(c.manager, c.book['id'])['conflicts']
    with pytest.raises(AccountingError, match='先通过'):
        learning.publish(c.manager, c.book['id'], stable['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluation['id'], reason='重叠必须先解决'))


def test_foreign_company_id_is_rejected_in_python_and_sql(business):
    c = business
    learning, master, _, positive, _, rule = setup(c)
    foreign = master.create_entity(c.manager, c.other_book['id'], EntityCreate(kind='counterparty', code='FOREIGN', name='供应商甲'))
    definition = {**rule['definition'], 'conditions': {**rule['definition']['conditions'], 'counterparty_id': foreign['id']}}
    with pytest.raises(AccountingError, match='本企业'):
        learning.save_rule(c.manager, c.book['id'], RuleUpdate(definition=definition, sample_ids=[positive['id']], reason='跨企业稳定标识不可使用', expected_version=1, expected_state_version=1), rule['id'])
    with pytest.raises(sqlite3.IntegrityError, match='own active company'):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("UPDATE learning_rules SET current_version=2,state_version=2 WHERE id=?", (rule['id'],))
            conn.execute('INSERT INTO learning_rule_versions(rule_id,book_id,version,definition_json,definition_fingerprint,sample_ids_json,reason,created_by,created_at) SELECT rule_id,book_id,2,?,definition_fingerprint,sample_ids_json,reason,created_by,created_at FROM learning_rule_versions WHERE rule_id=? AND version=1', (json.dumps(definition, ensure_ascii=False), rule['id']))


def test_observed_rule_hits_accountant_changes_and_shadow_replay_are_separate(business):
    c = business
    learning, _, _, _, negative, rule = setup(c)
    publish(c, learning, negative, rule)
    sample = bank_sample(c.ledger, learning, c.book['id'], c.manager, external='rule-hit-corrected', amount='35.01', code='6602.03')
    governance = learning.governance(c.manager, c.book['id'])
    usage = next(row for row in governance['usage'] if row['rule_id'] == rule['id'])
    assert usage['reviewed_hits'] == usage['accountant_modified'] == 1 and usage['modified_rate'] == 1
    with c.db.transaction(actor=c.manager) as conn:
        replay = learning.shadow_replay_in_transaction(conn, c.manager, c.book['id'], rule['definition'], [sample['id']])
    assert replay['different_count'] == 1 and replay['matched_count'] == 1
    assert learning.governance(c.manager, c.book['id'])['usage'] == governance['usage']
    with pytest.raises(AccountingError):
        learning.governance(c.actors['employee'], c.book['id'])
