import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import Actor, AccountingError
from app.accounting.seed import LOCAL_ACTOR
from app.quality.models import RunCreate, StandardConfirm, StandardCreate
from app.quality.router import build_quality_router
from app.quality.service import QualityService
from test_catalog import case
from test_stage8_learning import ready, evaluate_publish
from test_intelligence_workflow import ai_case, create_event, extract, handoff, propose
from test_business import business


def second_reviewer(ledger, book):
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        firm_id = conn.execute("SELECT firm_id FROM books WHERE id=?", (book,)).fetchone()[0]
    actor = Actor('quality-reviewer-' + uuid4().hex, firm_id)
    with ledger.db.transaction(write=True, system=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES(?,'质量独立复核人')", (actor.user_id,))
        conn.execute("INSERT INTO memberships VALUES(?,?,'reviewer')", (actor.firm_id, actor.user_id))
        conn.execute("INSERT INTO book_access VALUES(?,?,?,'reviewer')", (book, actor.firm_id, actor.user_id))
    return actor


def standard(ready):
    ledger, _, _, book, sample, _, _ = ready
    quality = QualityService(ledger.db)
    reviewer = second_reviewer(ledger, book)
    row = quality.create_standard(LOCAL_ACTOR, book, StandardCreate(sample_id=sample['id'], reason='逐项核对原归档合同及已审核分录', idempotency_key=uuid4().hex))
    quality.confirm(reviewer, book, row['id'], StandardConfirm(evidence_checked=True, reason='第二位会计独立核对原件和金额'))
    return quality, reviewer, row


def test_ai_output_and_same_user_cannot_confirm_standard(ready):
    ledger, _, _, book, sample, _, _ = ready
    quality = QualityService(ledger.db)
    request = StandardCreate(sample_id=sample['id'], reason='原件核对后提议', idempotency_key=uuid4().hex)
    row = quality.create_standard(LOCAL_ACTOR, book, request)
    assert not row['confirmed']
    assert quality.create_standard(LOCAL_ACTOR, book, request)['id'] == row['id']
    with pytest.raises(AccountingError, match='另一位'):
        quality.confirm(LOCAL_ACTOR, book, row['id'], StandardConfirm(evidence_checked=True, reason='不能自证'))
    with pytest.raises(AccountingError, match='独立确认'):
        quality.create_run(LOCAL_ACTOR, book, RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex))
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        draft = conn.execute('SELECT status FROM drafts WHERE id=?', (sample['draft_id'],)).fetchone()
        assert draft['status'] == 'approved'


def test_replay_published_rules_is_exact_and_keeps_ledger_unchanged(ready):
    _, _, _, book, sample, _, _ = ready
    evaluate_publish(ready)
    quality, reviewer, row = standard(ready)
    request = RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex)
    run = quality.create_run(reviewer, book, request)
    assert run['report']['counts'] == {'passed': 1, 'different': 0, 'uncovered': 0, 'stale': 0}
    assert run['report']['items'][0]['provenance']['version'] == 1
    assert not run['stale'] and quality.create_run(reviewer, book, request)['id'] == run['id']
    with quality.db.transaction(actor=reviewer) as conn:
        assert conn.execute('SELECT version,status FROM drafts WHERE id=?', (sample['draft_id'],)).fetchone()['status'] == 'approved'


def test_missing_coverage_and_unmeasured_metrics_are_not_zero_accuracy(ready):
    _, _, _, book, _, _, _ = ready
    quality, reviewer, row = standard(ready)
    run = quality.create_run(reviewer, book, RunCreate(standard_ids=[row['id']], mode='stored_ocr', model='nonexistent', prompt_version='v1', idempotency_key=uuid4().hex))
    assert run['report']['counts']['uncovered'] == 1 and run['report']['counts']['passed'] == 0
    overview = quality.overview(reviewer, book)
    assert overview['human_minutes'] is None and overview['supplement_wait_seconds'] is None and overview['delivery_differences'] is None
    assert overview['summary_by_business_type'] == [{'business_type': 'daily_cashflow', 'confirmed_count': 1}]


def test_rule_and_catalog_changes_mark_old_run_stale(ready):
    _, catalog, _, book, _, _, _ = ready
    from app.accounting.catalog_models import AccountUpdate
    evaluate_publish(ready)
    quality, reviewer, row = standard(ready)
    run = quality.create_run(reviewer, book, RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex))
    catalog.update_account(LOCAL_ACTOR, book, '1002', AccountUpdate(code='1002', name='经确认的新银行名称', expected_version=1))
    assert quality.run(reviewer, book, run['id'])['stale']


def test_standard_and_run_remain_current_scope_after_revocation(ready):
    ledger, _, _, book, _, _, _ = ready
    quality, reviewer, row = standard(ready)
    run = quality.create_run(reviewer, book, RunCreate(standard_ids=[row['id']], mode='templates', idempotency_key=uuid4().hex))
    with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute('DELETE FROM book_access WHERE user_id=? AND book_id=?', (reviewer.user_id, book))
    for action in [lambda: quality.standard(reviewer, book, row['id']), lambda: quality.run(reviewer, book, run['id']), lambda: quality.overview(reviewer, book)]:
        with pytest.raises(AccountingError) as failure:
            action()
        assert failure.value.status in (403, 404)


def test_freeze_rejects_forged_final_and_immutable_update(ready):
    ledger, _, _, book, _, _, _ = ready
    quality, reviewer, row = standard(ready)
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            conn.execute("UPDATE quality_standards SET final_json='{}' WHERE id=?", (row['id'],))
    with pytest.raises(sqlite3.IntegrityError, match='actual reviewed'):
        with ledger.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            conn.execute("INSERT INTO quality_standards SELECT ?,firm_id,company_id,book_id,period,sample_id,draft_id,source_id,snapshot_json,'{}',evidence_json,labels_json,reason,created_by,created_at,?,request_fingerprint FROM quality_standards WHERE id=?", (uuid4().hex, uuid4().hex, row['id']))


def test_http_models_and_authorization_recheck(ready):
    quality, reviewer, row = standard(ready)
    book = ready[3]
    app = FastAPI()
    app.state.quality = quality
    actor = LOCAL_ACTOR
    app.include_router(build_quality_router(lambda: actor))

    @app.exception_handler(AccountingError)
    def failure(_: Request, error: AccountingError):
        return JSONResponse({'detail': str(error)}, status_code=error.status)

    with TestClient(app) as client:
        assert client.post(f'/api/books/{book}/quality/runs', json={'standard_ids': [row['id']], 'mode': 'stored_model', 'idempotency_key': uuid4().hex}).status_code == 422
        assert client.get(f'/api/books/{book}/quality?limit=101').status_code == 422
        assert client.get(f'/api/books/{book}/quality/standards/{row["id"]}').status_code == 200
        actor = Actor('no-assigned-quality-user', LOCAL_ACTOR.firm_id)
        assert client.get(f'/api/books/{book}/quality/standards/{row["id"]}').status_code in (403, 404)


def ai_standard(c, changed_label=False):
    from app.accounting.models import ApprovalRequest
    from app.accounting.catalog import CatalogService
    from app.accounting.catalog_models import TemplateDefinition
    CatalogService(c.db).save_template(c.manager, c.book['id'], TemplateDefinition(name='合成评测未付款报销', business_type='reimbursement', direction='expense', category='office', payment_status='unpaid', lines=[{'account_code': '6602.02', 'side': 'debit', 'amount_field': 'gross'}, {'account_code': '2241', 'side': 'credit', 'amount_field': 'gross'}]))
    event = create_event(c)
    ocr = extract(c, event)
    mapping = handoff(c, event)
    proposal = propose(c, mapping['draft_id'])
    assert proposal['status'] == 'succeeded'
    applied = c.intelligence.apply(c.actors['accountant'], proposal['id'], 1)
    reviewer = second_reviewer(c.ledger, c.book['id'])
    c.ledger.approve(reviewer, c.book['id'], applied['id'], ApprovalRequest(expected_version=applied['version']))
    quality = QualityService(c.db)
    # Public assistant projections intentionally hide provider/model metadata.
    # The replay test is an operator-side check, so read the frozen run meta
    # under the manager's authorized transaction instead of weakening redaction.
    with c.db.transaction(actor=c.manager) as conn:
        sample_id = conn.execute('SELECT id FROM learning_samples WHERE draft_id=?', (applied['id'],)).fetchone()[0]
        for run in (ocr, proposal):
            meta = conn.execute('SELECT model,prompt_version FROM ai_runs WHERE id=?', (run['id'],)).fetchone()
            run['model'], run['prompt_version'] = meta['model'], meta['prompt_version']
    value = '人工核对的另一销售方' if changed_label else '耗材商店'
    row = quality.create_standard(c.actors['accountant'], c.book['id'], StandardCreate(sample_id=sample_id, field_labels=[{'extraction_id': ocr['id'], 'document_index': 0, 'field': 'seller_name', 'page': 1, 'value': value}, {'extraction_id': ocr['id'], 'document_index': 0, 'field': 'tax_amount', 'page': 1, 'value': '0.00'}], reason='独立评测标签来自合成原件的人工答案', idempotency_key=uuid4().hex))
    quality.confirm(c.manager, c.book['id'], row['id'], StandardConfirm(evidence_checked=True, reason='另一会计独立确认，未把模型预测作为答案'))
    return quality, row, ocr, proposal, c.provider.extract_calls, c.provider.proposal_calls


@pytest.mark.parametrize('mode', ['templates', 'stored_model', 'stored_ocr'])
def test_actual_frozen_ocr_and_journal_replay_are_versioned_and_do_not_call_model(ai_case, mode):
    c = ai_case
    quality, row, ocr, proposal, extraction_count, proposal_count = ai_standard(c)
    candidate = ocr if mode == 'stored_ocr' else proposal
    request = RunCreate(standard_ids=[row['id']], mode=mode, idempotency_key=uuid4().hex, **({'model': candidate['model'], 'prompt_version': candidate['prompt_version']} if mode.startswith('stored_') else {}))
    run = quality.create_run(c.manager, c.book['id'], request)
    assert run['report']['counts']['passed'] == 1, run
    assert c.provider.extract_calls == extraction_count and c.provider.proposal_calls == proposal_count
    assert run['report']['items'][0]['differences'] == []


def test_human_field_answer_exposes_exact_ocr_difference(ai_case):
    c = ai_case
    quality, row, ocr, _, _, _ = ai_standard(c, changed_label=True)
    run = quality.create_run(c.manager, c.book['id'], RunCreate(standard_ids=[row['id']], mode='stored_ocr', model=ocr['model'], prompt_version=ocr['prompt_version'], idempotency_key=uuid4().hex))
    assert run['report']['counts']['different'] == 1 and run['report']['counts']['passed'] == 0
    difference = run['report']['items'][0]['differences'][0]
    assert difference['field'] == 'seller_name' and difference['actual'] == '耗材商店'
    assert difference['expected'] == '人工核对的另一销售方'


def alias_replay(ledger, owner, reviewer, book):
    from app.ledgers.models import AliasCreate, AliasDecision, EntityCreate
    from app.ledgers.service import LedgersService
    from app.learning.models import RuleEvaluate, RulePublish
    from app.learning.service import LearningService
    from test_stage8_learning import bank_sample
    master, learning, quality = LedgersService(ledger.db), LearningService(ledger.db), QualityService(ledger.db)
    entity = master.create_entity(owner, book, EntityCreate(kind='counterparty', code='QP1', name='正式质量供方'))
    alias = master.create_alias(owner, book, AliasCreate(entity_id=entity['id'], original='质量供方简称', reason='原件名称映射已提议'))
    master.decide_alias(reviewer, book, alias['id'], AliasDecision(expected_version=1, decision='approve', reason='独立核对主体及简称'))
    bank_sample(ledger, learning, book, owner, external='formal', counterparty='正式质量供方')
    negative = bank_sample(ledger, learning, book, owner, external='alias-negative', counterparty='无关联供方', corrected=False)
    rule = learning.overview(owner, book)['rules'][0]
    evaluated = learning.evaluate(owner, book, rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative['id']]))
    learning.publish(owner, book, rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluated['id'], reason='真实已审正反例完成'))
    sample = bank_sample(ledger, learning, book, owner, external='alias', counterparty='质量供方简称', corrected=False)
    row = quality.create_standard(owner, book, StandardCreate(sample_id=sample['id'], reason='核对原件简称及已审分录', idempotency_key=uuid4().hex))
    quality.confirm(reviewer, book, row['id'], StandardConfirm(evidence_checked=True, reason='独立核对简称来源'))
    run = quality.create_run(owner, book, RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex))
    assert run['report']['counts']['passed'] == 1
    master.decide_alias(reviewer, book, alias['id'], AliasDecision(expected_version=2, decision='retire', reason='别名不再适用，规则不得继续匹配'))
    assert quality.run(owner, book, run['id'])['stale']
    current = quality.create_run(owner, book, RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex))
    assert current['report']['counts']['uncovered'] == 1


def test_approved_alias_retirement_invalidates_old_run_and_new_coverage(business):
    c = business
    alias_replay(c.ledger, c.manager, c.actors['accountant'], c.book['id'])


def report_group_case(ready):
    _, catalog, _, book, _, _, _ = ready
    quality, reviewer, row = standard(ready)
    old = quality.create_run(reviewer, book, RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex))
    assert old['report']['counts']['uncovered'] == 1
    evaluate_publish(ready)
    latest = quality.create_run(reviewer, book, RunCreate(standard_ids=[row['id']], mode='rules', idempotency_key=uuid4().hex))
    quality.create_run(reviewer, book, RunCreate(standard_ids=[row['id']], mode='templates', idempotency_key=uuid4().hex))
    groups = quality.report(reviewer, book)['groups']
    assert len(groups) == 2
    rules = next(item for item in groups if item['mode'] == 'rules')
    assert rules['run_id'] == latest['id'] != old['id']
    assert rules['counts']['passed'] == rules['sample_count'] == rules['measured_sample_count'] == 1
    assert rules['measured_match_rate'] == 1 and not rules['context_stale']
    assert rules['human_minutes'] is None and rules['supplement_wait_seconds'] is None and rules['delivery_differences'] is None
    template = next(item for item in groups if item['mode'] == 'templates')
    assert template['counts']['uncovered'] == 1 and template['measured_match_rate'] is None
    from app.accounting.catalog_models import AccountUpdate
    catalog.update_account(LOCAL_ACTOR, book, '1002', AccountUpdate(code='1002', name='新银行配置', expected_version=1))
    groups = quality.report(reviewer, book)['groups']
    assert all(item['context_stale'] and item['measured_match_rate'] is None and item['measured_sample_count'] is None for item in groups)
    assert quality.overview(reviewer, book)['report_groups'] == groups


def test_month_and_business_type_report_uses_latest_run_per_mode_and_hides_stale_rate(ready):
    report_group_case(ready)
