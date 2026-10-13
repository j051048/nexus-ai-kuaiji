import json
from uuid import uuid4

import pytest

from app.accounting.models import BookCreate, BookMemberGrant, DraftUpdate
from app.accounting.domain import AccountingError
from app.closing.models import PreflightRequest
from app.closing.service import ClosingService
from app.revisions.models import RevisionSubmit
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.settlements.models import AllocationConfirm, AllocationPreview, AllocationReverse, OpenItemCreate
from app.settlements.service import SettlementService
from test_business import business
from test_intelligence_workflow import ai_case, import_bank
from test_stage10_fact_revisions import prepare


def raw_private_event(c, date):
    payload = {'amount': '13.00', 'description': '私有原件业务描述不能泄漏', 'counterparty': '私有往来方', 'direction': 'expense', 'payment_status': 'unpaid', 'category': 'office'}
    if date is not None:
        payload['occurred_on'] = date
    event_id = uuid4().hex
    with c.db.transaction(write=True, actor=c.actors['employee']) as conn:
        conn.execute("INSERT INTO business_events(id,firm_id,company_id,department_id,applicant_id,template_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,'reimbursement',?,'2026-10-12','2026-10-12')",
                     (event_id, c.manager.firm_id, c.company['id'], c.department['id'], c.actors['employee'].user_id, json.dumps(payload, ensure_ascii=False)))
    return event_id, payload


@pytest.mark.parametrize('date', [None, '', '2026-02-31', '2026-13-01', '2026-1-01', 'not-a-date', '2100-01-01', 20261012, '2026-11-31'])
def test_unassignable_private_business_blocks_only_own_company_and_exposes_no_payload(business, date):
    c = business
    closing = ClosingService(c.db)
    old = closing.preflight(c.actors['accountant'], c.book['id'], PreflightRequest(idempotency_key=uuid4().hex))
    other_before = closing.overview(c.manager, c.other_book['id'])['current_fingerprint']
    event_id, payload = raw_private_event(c, date)
    value = closing.overview(c.actors['accountant'], c.book['id'])
    assert value['current_report']['counts'].get('missing_evidence') == 1
    assert value['current_fingerprint'] != old['input_fingerprint']
    serialized = json.dumps(value, ensure_ascii=False)
    assert event_id not in serialized and payload['description'] not in serialized and payload['counterparty'] not in serialized
    assert 'inputs_json' not in serialized
    other = closing.overview(c.manager, c.other_book['id'])
    assert other['current_report']['passed'] and other['current_fingerprint'] == other_before
    assert all(item['object_type'] == 'book' and item['object_id'] == c.book['id'] for item in value['current_report']['issues'])


def test_valid_future_month_private_business_does_not_block_current_period(business):
    c = business
    raw_private_event(c, '2026-11-03')
    assert ClosingService(c.db).overview(c.actors['accountant'], c.book['id'])['current_report']['passed']


def test_cross_period_target_revocation_keeps_close_blocker_generic_and_hides_revision_payload(ai_case):
    c = ai_case
    event, _ = prepare(c)
    target = c.ledger.create_book(c.manager, BookCreate(name='独立目标期间', company_id=c.company['id'], period='2026-11'))
    c.directory.grant_book_member(c.manager, target['id'], c.actors['finance'].user_id, BookMemberGrant(role='editor'))
    c.directory.grant_book_member(c.manager, target['id'], c.actors['accountant'].user_id, BookMemberGrant(role='reviewer'))
    current = c.business.get_event(c.actors['finance'], event['id'])
    revision = c.revisions.submit(c.actors['finance'], event['id'], RevisionSubmit(
        expected_event_version=current['version'], expected_fact_version=current['fact_version'], expected_draft_version=1,
        target_book_id=target['id'], payload={**current['payload'], 'occurred_on': '2026-11-03', 'description': '目标期间私有修订描述'},
        reason='原业务日期实际归属于次月，提出待独立复核修订', idempotency_key=uuid4().hex))
    c.directory.revoke_book_member(c.manager, target['id'], c.actors['accountant'].user_id)
    closing = ClosingService(c.db)
    result = closing.preflight(c.actors['accountant'], c.book['id'], PreflightRequest(idempotency_key=uuid4().hex))
    assert result['report']['counts']['fact_revision'] == 1
    assert '目标期间私有修订描述' not in json.dumps(result, ensure_ascii=False)
    assert 'proposed_payload' not in json.dumps(result)
    with pytest.raises(AccountingError):
        c.revisions.get(c.actors['accountant'], revision['id'])


def test_settlement_item_allocation_and_reversal_change_snapshot_without_draft_version_change(ai_case):
    c = ai_case
    event, mapping = prepare(c, amount='100.00')
    draft = c.ledger.get_draft(c.actors['accountant'], c.book['id'], mapping['draft_id'])
    c.ledger.update_draft(c.actors['accountant'], c.book['id'], draft['id'], DraftUpdate(expected_version=draft['version'], description='员工未付款业务应付凭证',
        lines=[{'account_code': '6602.02', 'debit': '100.00', 'credit': '0.00'}, {'account_code': '2241', 'debit': '0.00', 'credit': '100.00'}], correction_reason='先按原业务记录应付，收付另行清偿'))
    bank = import_bank(c, amount='100.00')
    party = LedgersService(c.db).create_entity(c.actors['accountant'], c.book['id'], EntityCreate(kind='counterparty', code='CL-001', name=event['payload']['counterparty']))
    closing = ClosingService(c.db)
    before = closing.preflight(c.actors['accountant'], c.book['id'], PreflightRequest(idempotency_key=uuid4().hex))
    settlements = SettlementService(c.db)
    draft = c.ledger.get_draft(c.actors['accountant'], c.book['id'], mapping['draft_id'])
    item = settlements.open_item(c.actors['accountant'], c.book['id'], OpenItemCreate(source_id=mapping['source_id'], counterparty_id=party['id'], expected_draft_version=draft['version'], idempotency_key=uuid4().hex, reason='登记员工未付款应付事项'))
    after_item = closing.preflight(c.actors['accountant'], c.book['id'], PreflightRequest(idempotency_key=uuid4().hex))
    assert after_item['input_fingerprint'] != before['input_fingerprint']
    preview = settlements.preview(c.actors['accountant'], c.book['id'], AllocationPreview(bank_source_id=bank['id'], parts=[{'item_id': item['id'], 'amount': '30.00'}], reason='核实部分付款，保留剩余应付'))
    receipt = settlements.confirm(c.actors['accountant'], c.book['id'], preview['id'], AllocationConfirm(idempotency_key=uuid4().hex))
    after_allocation = closing.preflight(c.actors['accountant'], c.book['id'], PreflightRequest(idempotency_key=uuid4().hex))
    assert after_allocation['input_fingerprint'] != after_item['input_fingerprint']
    assert any('核销尚未分配完整' in issue['detail'] for issue in after_allocation['report']['issues'])
    assert c.ledger.get_draft(c.actors['accountant'], c.book['id'], mapping['draft_id'])['version'] == draft['version']
    allocation = settlements.overview(c.manager, c.book['id'])['allocations'][0]
    settlements.reversal(c.manager, c.book['id'], receipt['allocations'][0]['id'], AllocationReverse(expected_fingerprint=allocation['fingerprint'], idempotency_key=uuid4().hex, reason='另一会计核实误分配，独立撤销'))
    after_reversal = closing.preflight(c.actors['accountant'], c.book['id'], PreflightRequest(idempotency_key=uuid4().hex))
    assert after_reversal['input_fingerprint'] != after_allocation['input_fingerprint']
    assert not any('核销尚未分配完整' in issue['detail'] for issue in after_reversal['report']['issues'])
