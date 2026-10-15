"""Workspace projection under real PostgreSQL FORCE RLS and current grants."""
import json
from uuid import uuid4

import pytest
from app.accounting.domain import AccountingError
from app.accounting.review import ReviewService
from app.business.service import BusinessService
from app.cases.models import CaseCommand
from app.cases.service import CaseService
from app.delivery.models import ReceiptCreate
from app.workspace.service import WorkspaceService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres, insert_event, submit
from test_queue_postgres import queue_case, enqueue
from test_stage8_cases_postgres import prepare as prepare_case
from test_stage9_delivery_postgres import prepare as prepare_delivery
from test_stage19_request_reads import count_statements

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用 PostgreSQL 管理员与受限运行连接')


def workspace(c):
    business = BusinessService(c.db)
    return WorkspaceService(c.db, c.service, business, ReviewService(c.db, c.service), CaseService(c.db, business))


def test_pg_workspace_current_role_and_authorization_before_count_and_limit(business_case, monkeypatch):
    c, service = business_case, workspace(business_case)
    assert service.profile(c.employee)['role'] == 'employee'
    assert service.profile(c.approver)['role'] == 'approver'
    assert service.profile(c.finance)['role'] == 'finance'
    assert service.profile(c.reviewer)['role'] == 'accountant'
    hidden = [insert_event(c, c.colleague) for _ in range(4)]
    owned = [insert_event(c) for _ in range(3)]
    seen = []
    for offset in range(3):
        with count_statements(c.db, monkeypatch) as statements:
            value = service.list(c.employee, bucket='all', limit=1, offset=offset)
        assert len(statements) == 2
        assert value['pagination']['total'] == 3
        seen.extend(row['ref'] for row in value['tasks'])
        assert not any(ref in json.dumps(value) for ref in hidden)
    assert set(seen) == set(owned)
    assert service.list(c.employee, bucket='all', company_id=c.other_company)['tasks'] == []
    empty_page = service.list(c.employee, bucket='all', offset=100)
    assert empty_page['tasks'] == [] and empty_page['pagination']['total'] == 3
    assert empty_page['counts'] == {'action': 3}


def test_pg_workspace_supplement_recipient_is_private_under_native_case_source_check(business_case):
    c, service = business_case, workspace(business_case)
    cases, event_id, task = prepare_case(c)
    row = service.get(c.employee, 'case', task['id'])
    assert row['bucket'] == 'action' and row['title'] == '补充业务资料'
    assert all(row[key] is None for key in ('book_id', 'draft_id', 'event_id', 'batch_id'))
    assert c.book not in json.dumps(row) and task['title'] not in json.dumps(row)
    for actor in (c.colleague, c.approver, c.viewer, c.unassigned):
        with pytest.raises(AccountingError) as hidden:
            service.get(actor, 'case', task['id'])
        assert hidden.value.status == 404
    assert service.list(c.reviewer, kind='review')['tasks'] == []


@pytest.mark.parametrize('change', ['membership', 'book_access', 'suspended_client', 'inactive_user'])
def test_pg_workspace_live_revocation_and_suspension(business_case, change):
    c, service = business_case, workspace(business_case)
    _, _, task = prepare_case(c)
    actor = c.employee if change == 'membership' else c.reviewer
    assert service.get(actor, 'case', task['id'])['ref'] == task['id']
    with c.psycopg.connect(ADMIN_URL) as conn:
        if change == 'membership':
            conn.execute('DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s', (c.company, actor.user_id))
        elif change == 'book_access':
            conn.execute('DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s', (c.book, actor.user_id))
        elif change == 'suspended_client':
            conn.execute("UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s", (actor.firm_id, c.company))
        else:
            conn.execute('UPDATE accounting.users SET active=0 WHERE id=%s', (actor.user_id,))
    with pytest.raises(AccountingError) as revoked:
        service.get(actor, 'case', task['id'])
    assert revoked.value.status == (403 if change == 'inactive_user' else 404)


def test_pg_workspace_closed_case_returns_404_without_fallback(business_case):
    c, service = business_case, workspace(business_case)
    cases, _, task = prepare_case(c, 'exception')
    cases.command(c.reviewer, task['id'], 'close', CaseCommand(expected_version=task['version'], reason='已核实合成业务', idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError) as completed:
        service.get(c.reviewer, 'case', task['id'])
    assert completed.value.status == 404


def test_pg_workspace_export_and_unknown_receipt_are_explicit_human_actions(business_case):
    from app.accounting.models import ApprovalRequest, ExportRequest
    from test_batch_review_postgres import bank_draft
    from app.delivery.service import DeliveryService
    c, service = business_case, workspace(business_case)
    selected = bank_draft(c, c.book, uuid4().hex)
    draft = c.service.get_draft(c.reviewer, c.book, selected['draft_id'])
    c.service.approve(c.reviewer, c.book, draft['id'], ApprovalRequest(expected_version=draft['version']))
    assert service.get(c.reviewer, 'export', c.book)['title'].endswith('2026-10 · 待导出凭证 · 1 张')
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute('SELECT count(*) FROM exports WHERE book_id=?', (c.book,)).fetchone()[0] == 0
    exported = c.service.export(c.reviewer, c.book, ExportRequest(draft_ids=[draft['id']], idempotency_key=uuid4().hex))
    delivery = DeliveryService(c.db)
    facts = json.loads(delivery.template(c.reviewer, c.book, exported['id']))
    facts['items'][0].update(status='unknown', external_voucher_id=None)
    delivery.create_receipt(c.reviewer, c.book, exported['id'], ReceiptCreate(**facts, reason='未知合成回执', idempotency_key=uuid4().hex))
    assert service.get(c.reviewer, 'delivery', exported['id'])['state'] == 'unverified'
    assert service.list(c.employee, kind='delivery')['tasks'] == []
    facts['items'][0].update(status='accepted', external_voucher_id='虚构凭证-PG-001')
    delivery.create_receipt(c.reviewer, c.book, exported['id'], ReceiptCreate(**facts, reason='完整合成回执核验', idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError) as received:
        service.get(c.reviewer, 'delivery', exported['id'])
    assert received.value.status == 404


def test_pg_workspace_ocr_fact_version_survives_approval_only_versions(queue_case):
    c, service = queue_case, workspace(queue_case)
    event_id = insert_event(c)
    job = enqueue(c, event_id=event_id, expected_fact_version=1)
    submit(c, event_id)
    assert service.get(c.employee, 'ocr', job['id'])['bucket'] == 'waiting'
