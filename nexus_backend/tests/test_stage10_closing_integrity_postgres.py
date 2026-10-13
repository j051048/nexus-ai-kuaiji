import json
from uuid import uuid4

import pytest

from app.closing.models import PreflightRequest
from app.closing.service import ClosingService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_stage10_closing_integrity import test_settlement_item_allocation_and_reversal_change_snapshot_without_draft_version_change as verify_settlement_fingerprint_mutations

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员及受限运行账号')


@pytest.mark.parametrize('date', [None, '2026-02-31', '2026-11-31', 'not-a-date', '2100-01-01'])
def test_raw_unassignable_employee_facts_block_only_book_and_fingerprint_never_returns_inputs(business_case, date):
    c = business_case
    closing = ClosingService(c.db)
    snapshot = closing.preflight(c.reviewer, c.book, PreflightRequest(idempotency_key=uuid4().hex))
    other = closing.overview(c.owner, c.other_book)
    event_id = uuid4().hex
    body = {'amount': '13.00', 'purpose': '私密员工用途不可泄漏', 'description': '员工原始私密事项', 'counterparty': '员工私有往来名称'}
    if date is not None:
        body['occurred_on'] = date
    with c.db.transaction(write=True, actor=c.employee) as conn:
        conn.execute("INSERT INTO business_events(id,firm_id,company_id,department_id,applicant_id,template_id,payload,created_at,updated_at) VALUES(?,?,?,?,?,'reimbursement',?,'2026-10-12','2026-10-12')",
                     (event_id, c.owner.firm_id, c.company, c.department, c.employee.user_id, json.dumps(body, ensure_ascii=False)))
    value = closing.overview(c.reviewer, c.book)
    assert value['current_report']['counts'] == {'missing_evidence': 1}
    assert value['current_fingerprint'] != snapshot['input_fingerprint']
    serialized = json.dumps(value, ensure_ascii=False)
    assert event_id not in serialized and body['purpose'] not in serialized and 'inputs_json' not in serialized
    assert value['current_report']['issues'][0]['object_type'] == 'book'
    assert closing.overview(c.owner, c.other_book)['current_fingerprint'] == other['current_fingerprint']
    with c.db.transaction(actor=c.reviewer) as conn:
        digest = conn.execute('SELECT accounting.closing_fingerprint(?)', (c.book,)).fetchone()[0]
        assert isinstance(digest, str) and len(digest) == 64 and all(char in '0123456789abcdef' for char in digest)
        assert 'input' not in digest


@pytest.mark.parametrize('date,expected', [('2024-02-29', True), ('2026-02-29', False), ('2026-04-31', False), ('2026-11-30', True), ('2026-1-01', False), ('2026-10-01\n', False)])
def test_native_closing_date_check_agrees_with_real_calendar(business_case, date, expected):
    with business_case.db.transaction(actor=business_case.owner) as conn:
        assert conn.execute('SELECT accounting.closing_date_valid(?)', (date,)).fetchone()[0] is expected


def test_native_settlement_allocation_and_reversal_invalidate_month_close_inputs(pg_ai):
    verify_settlement_fingerprint_mutations(pg_ai)
