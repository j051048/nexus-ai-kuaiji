"""Actual SQLite worker integration with synthetic OCR, no external service."""
import io
import json
from uuid import uuid4

import pytest
from PIL import Image

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest, ImportRequest, SourceRow
from app.accounting.seed import LOCAL_ACTOR
from app.business.models import BusinessEventCreate
from app.intelligence.requests import OCRRequest
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from app.learning.models import RuleEvaluate, RulePublish
from app.learning.service import LearningService
from test_catalog import case
from test_intelligence_workflow import ai_case, handoff, invoice_document
from test_queue_workflow import TestClock
from test_stage8_learning import ready, evaluate_publish
from test_stage7_proposal_queue import independent_original


def event(case, ordinal, amount='0.30', counterparty='耗材商店'):
    value = case.business.create(case.actors['employee'], BusinessEventCreate(company_id=case.company['id'], template_id='reimbursement', payload={
        'occurred_on':'2026-10-03','description':f'合成事项 {ordinal}','counterparty':counterparty,'amount':amount,
        'direction':'expense','payment_status':'unpaid','category':'office'}))
    output = io.BytesIO()
    Image.new('RGB', (40, 20), (ordinal * 19 % 256, ordinal * 31 % 256, ordinal * 43 % 256)).save(output, 'PNG')
    attachment = case.storage.put(f'独立合成票据-{ordinal}.png', 'image/png', output.getvalue())
    return case.business.add_attachment(case.actors['employee'], value['id'], value['version'], attachment)


def seed_sample(case, learning, ordinal, counterparty='耗材商店', corrected=False, account='6602.99', auxiliaries=False):
    value = event(case, ordinal, counterparty=counterparty)
    case.original_text = invoice_document(seller_name=counterparty, invoice_number=f'STAGE8-SEED-{ordinal}')["pages"][0]["raw_text"]
    case.provider.document = invoice_document(seller_name=counterparty, invoice_number=f'STAGE8-SEED-{ordinal}')
    case.intelligence.extract(case.actors['employee'], value['id'], OCRRequest(attachment_id=value['attachments'][0]['id'], expected_version=value['version'], idempotency_key=uuid4().hex, force_refresh=True))
    mapped = handoff(case, value)
    draft = case.ledger.get_draft(case.actors['accountant'], case.book['id'], mapped['draft_id'])
    if corrected:
        lines = [{'account_code':account,'debit':'0.30','credit':'0.00','auxiliary_values':{'counterparty':counterparty} if auxiliaries else {}},
                 {'account_code':'2241','debit':'0.00','credit':'0.30'}]
        draft = case.ledger.update_draft(case.actors['accountant'], case.book['id'], draft['id'], DraftUpdate(expected_version=draft['version'], description='会计依据合同修正分类', lines=lines, correction_reason='合同显示真实用途不同，使用客户科目'))
    case.ledger.approve(case.actors['accountant'], case.book['id'], draft['id'], ApprovalRequest(expected_version=draft['version']))
    with case.db.transaction(actor=case.actors['accountant']) as conn:
        return conn.execute('SELECT id FROM learning_samples WHERE draft_id=?', (draft['id'],)).fetchone()[0]


@pytest.fixture
def company_rules(ai_case, monkeypatch):
    c = ai_case
    independent_original(c, monkeypatch)
    c.ledger.intelligence = c.intelligence
    learning = LearningService(c.db)
    c.ledger.learning = c.intelligence.ledger.learning = learning
    # The account is configured before use, preserving required aux history.
    CatalogService(c.db).create_account(c.manager, c.book['id'], AccountDefinition(code='6602.88', name='客户会计费用分类', auxiliary_requirements=['counterparty']))
    seed_sample(c, learning, 1, corrected=True, account='6602.88', auxiliaries=True)
    negative = seed_sample(c, learning, 2, counterparty='反例商店')
    rule = learning.overview(c.actors['accountant'], c.book['id'])['rules'][0]
    evaluated = learning.evaluate(c.actors['accountant'], c.book['id'], rule['id'], RuleEvaluate(expected_version=1, negative_sample_ids=[negative]))
    assert evaluated['passed']
    rule = learning.publish(c.actors['accountant'], c.book['id'], rule['id'], RulePublish(expected_version=1, expected_state_version=1, evaluation_id=evaluated['id'], reason='真实已审核正反例支持此科目处理'))
    c.clock = TestClock()
    c.queue = QueueService(c.db, c.intelligence, clock=c.clock)
    c.business.proposal_queue = c.queue.proposals
    c.worker = QueueWorker(c.queue, worker_id='stage8-company-rule-worker')
    c.learning, c.published_rule = learning, rule
    return c


def target(c, amount='1.20', ordinal=3):
    value = event(c, ordinal, amount)
    c.original_text = invoice_document(amount=amount, net_amount=amount, tax_amount='0.00', invoice_number=f'STAGE8-TARGET-{ordinal}')["pages"][0]["raw_text"]
    c.provider.document = invoice_document(amount=amount, net_amount=amount, tax_amount='0.00', invoice_number=f'STAGE8-TARGET-{ordinal}')
    return handoff(c, value)


def state(c, mapped):
    return c.queue.proposals.get(c.actors['accountant'], mapped['automatic_job']['id']), c.ledger.get_draft(c.actors['accountant'], c.book['id'], mapped['draft_id'])


def test_published_business_rule_is_consumed_by_actual_worker_with_scoped_receipt(company_rules):
    c = company_rules
    mapped = target(c)
    assert c.worker.run_once() and c.worker.run_once()
    task, draft = state(c, mapped)
    assert task['status'] == 'succeeded' and task['current_run_id'] is None
    assert task['result_rule_id'] == c.published_rule['id'] and task['result_rule_version'] == 1
    assert task['result_template_id'] is None and task['result_template_version'] is None
    assert task['result_draft_version'] == draft['version'] == 2
    assert draft['status'] == 'draft' and not draft['issues']
    expense = next(line for line in draft['lines'] if line['account_code'] == '6602.88')
    assert expense['debit'] == '1.20' and expense['auxiliary_values'] == {'counterparty':'耗材商店'}
    assert c.provider.proposal_calls == 0
    with c.db.transaction(actor=c.actors['accountant']) as conn:
        assert conn.execute("SELECT count(*) FROM ai_runs WHERE kind='proposal'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events WHERE object_id=? AND action='draft.company_rule.apply'", (draft['id'],)).fetchone()[0] == 1
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors['accountant'], c.book['id'], ExportRequest(draft_ids=[draft['id']], idempotency_key='stage8-rule-unreviewed-export'))


def test_manual_edit_wins_over_pending_published_rule_job(company_rules):
    c = company_rules
    mapped = target(c)
    _, before = state(c, mapped)
    updated = c.ledger.update_draft(c.actors['accountant'], c.book['id'], before['id'], DraftUpdate(expected_version=before['version'], description='会计在任务等待时人工处理', lines=before['lines'], correction_reason='保留本次独立判断'))
    assert c.worker.run_once() and c.worker.run_once()
    task, after = state(c, mapped)
    assert task['status'] == 'stale' and task['result_rule_id'] is None
    assert after['description'] == updated['description'] and after['version'] == updated['version']
    assert c.provider.proposal_calls == 0


def test_published_rule_does_not_bypass_independent_original_mismatch(company_rules):
    c = company_rules
    mapped = target(c)
    # The archived original remains 1.20 while the fake provider invents 2.40.
    c.provider.document = invoice_document(amount='2.40', net_amount='2.40', tax_amount='0.00', invoice_number='STAGE8-TARGET-3')
    assert c.worker.run_once() and c.worker.run_once()
    task, draft = state(c, mapped)
    assert task['status'] == 'needs_attention'
    assert draft['version'] == 1 and draft['status'] == 'draft'
    assert task['result_rule_id'] is None
    assert c.provider.proposal_calls == 0


def test_rule_publication_after_enqueue_invalidates_old_policy_job(company_rules):
    c = company_rules
    mapped = target(c)
    from app.learning.models import RuleRetire
    c.learning.retire(c.actors['accountant'], c.book['id'], c.published_rule['id'], RuleRetire(expected_version=1, expected_state_version=2, reason='企业处理口径调整，停止旧规则'))
    assert c.worker.run_once() and c.worker.run_once()
    task, draft = state(c, mapped)
    assert task['status'] == 'stale' and draft['version'] == 1 and task['result_rule_id'] is None
    assert c.provider.proposal_calls == 0


def test_bank_suggestions_apply_published_rule_to_current_source_amount(ready):
    ledger, _, learning, book, _, _, _ = ready
    rule, _ = evaluate_publish(ready)
    ledger.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name='新流水金额', rows=[SourceRow(external_id='fresh-learned-bank', date='2026-10-08', description='无演示关键词', counterparty='供应商甲', amount='27.01', direction='expense', evidence_ref='合成归档票据')]))
    assert ledger.suggestions(LOCAL_ACTOR, book)['created'] == 1
    with ledger.db.transaction(actor=LOCAL_ACTOR) as conn:
        draft_id = conn.execute("SELECT d.id FROM drafts d JOIN sources s ON s.id=d.source_id WHERE s.external_id='fresh-learned-bank'").fetchone()[0]
        audit = conn.execute("SELECT details FROM audit_events WHERE object_id=? AND action='draft.suggest'", (draft_id,)).fetchone()[0]
    result = ledger.get_draft(LOCAL_ACTOR, book, draft_id)
    assert result['status'] == 'draft' and result['version'] == 1
    assert result['lines'][0]['account_code'] == '6602.99' and result['lines'][0]['debit'] == '27.01'
    assert result['lines'][1]['credit'] == '27.01'
    assert json.loads(audit)['company_rule_id'] == rule['id']
