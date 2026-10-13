"""Current linked-bank blockers propagate even with models explicitly disabled."""
from uuid import uuid4

import pytest

from app.accounting.bank_imports import BankImportService
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest
from app.cases.models import CaseCreate
from app.cases.service import CaseService
from app.intelligence.service import IntelligenceService
from app.jobs.service import QueueService
from app.reconciliation.service import ReconciliationService
from test_business import business
from test_intelligence_http import auth, client
from test_intelligence_workflow import FakeProvider
from test_stage9_reconciliation import paid, command, selection


def stable_bank(c, transaction):
    c.ledger.import_sources(c.manager, c.book['id'], ImportRequest(source_name='明确合成银行流水', namespace='bank-file', bank_account='configured-bank-account', rows=[{
        'external_id':'display-'+transaction,'stable_transaction_id':transaction,'date':'2026-10-03','description':'办公耗材付款',
        'counterparty':'耗材商店','amount':'0.30','direction':'expense','evidence_ref':'明确合成付款回单'}]))
    c.ledger.suggestions(c.manager, c.book['id'])


def linked(c):
    mapped = paid(c)
    stable_bank(c, 'transaction-one')
    service = ReconciliationService(c.db)
    found = service.candidates(c.actors['accountant'], c.book['id'], mapped['event']['id'])
    service.confirm(c.actors['accountant'], command(selection(found)))
    return mapped, found['candidates'][0]['source_id']


def test_new_similar_bank_after_link_blocks_model_disabled_review_and_existing_approval_export(business):
    c = business
    mapped, _ = linked(c)
    reviewer = c.actors['accountant']
    draft = c.ledger.get_draft(reviewer, c.book['id'], mapped['draft_id'])
    assert not draft['issues']  # No OCR requirement in the disabled manual path.
    c.ledger.approve(reviewer, c.book['id'], draft['id'], ApprovalRequest(expected_version=draft['version']))
    stable_bank(c, 'independent-id-same-bank-facts')
    current = c.ledger.get_draft(reviewer, c.book['id'], draft['id'])
    assert current['status'] == 'approved' and any('已关联银行流水' in issue and '相似交易' in issue for issue in current['issues'])
    with pytest.raises(AccountingError, match='校验失败'):
        c.ledger.export(reviewer, c.book['id'], ExportRequest(draft_ids=[draft['id']], idempotency_key=uuid4().hex))
    with c.db.transaction(actor=reviewer) as conn:
        assert conn.execute('SELECT count(*) FROM exports WHERE book_id=?', (c.book['id'],)).fetchone()[0] == 0


def test_new_similar_bank_after_link_blocks_unreviewed_business_approval(business):
    c = business
    mapped, _ = linked(c)
    stable_bank(c, 'independent-id-same-facts')
    draft = c.ledger.get_draft(c.actors['accountant'], c.book['id'], mapped['draft_id'])
    with pytest.raises(AccountingError, match='相似交易'):
        c.ledger.approve(c.actors['accountant'], c.book['id'], draft['id'], ApprovalRequest(expected_version=draft['version']))
    assert c.ledger.get_draft(c.actors['accountant'], c.book['id'], draft['id'])['status'] == 'draft'


def test_linked_bank_private_case_reaches_nonoverridable_assessment_and_queue_attention(business):
    c = business
    mapped, bank_id = linked(c)
    reviewer = c.actors['accountant']
    with c.db.transaction(actor=reviewer) as conn:
        bank_draft = conn.execute('SELECT id FROM drafts WHERE source_id=? AND book_id=?', (bank_id,c.book['id'])).fetchone()[0]
    CaseService(c.db,c.service).create(reviewer, CaseCreate(book_id=c.book['id'],draft_id=bank_draft,title='内部调查标题不泄露',reason='内部会计调查备注不泄露',idempotency_key=uuid4().hex))
    intelligence = IntelligenceService(c.db,storage=None,provider=FakeProvider())
    queue = QueueService(c.db,intelligence)
    with c.db.transaction(actor=reviewer) as conn:
        event = c.service._event(conn,reviewer,mapped['event']['id'])
        assert not intelligence._enabled(conn,reviewer,event)
        assessment = intelligence._assessment(conn,reviewer,event)
        finding = next(item for item in assessment['findings'] if item['code'].startswith('linked_bank_case_'))
        assert finding['payload']['non_overridable'] and not finding['resolved']
        assert '内部会计调查备注不泄露' not in str(assessment) and '内部调查标题不泄露' not in str(assessment)
        draft = conn.execute('SELECT * FROM drafts WHERE id=?', (mapped['draft_id'],)).fetchone()
        assert queue.proposals._attention(conn,reviewer,event,draft)[0] == 'linked_bank_pending'
    current = c.ledger.get_draft(reviewer,c.book['id'],mapped['draft_id'])
    assert any('已关联银行流水' in issue and '异常' in issue for issue in current['issues'])


def http_linked(client):
    headers = {persona: auth(client,persona) for persona in ('accountant','employee','department','finance')}
    context = client.get('/api/business/context',headers=headers['employee']).json()
    company = context['companies'][0]
    created = client.post('/api/business/events',headers=headers['employee'],json={'company_id':company['id'],'template_id':'procurement_expense','payload':{
        'occurred_on':'2026-10-10','description':'明确虚构的关联付款验证','counterparty':'示例办公商店','amount':'268.10','direction':'expense','payment_status':'paid','category':'office'}})
    assert created.status_code == 201, created.text
    event = created.json()
    # A valid synthetic image is stored; no OCR request is made in this path.
    from io import BytesIO
    from PIL import Image
    image=BytesIO();Image.new('RGB',(8,8),'white').save(image,'PNG')
    uploaded=client.post(f"/api/business/events/{event['id']}/attachments?expected_version={event['version']}",headers=headers['employee'],files={'file':('synthetic.png',image.getvalue(),'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    event=uploaded.json()
    for action,persona in (('submit','employee'),('department-approve','department'),('finance-approve','finance')):
        response=client.post(f"/api/business/events/{event['id']}/{action}",headers=headers[persona],json={'expected_version':event['version']})
        assert response.status_code == 200,response.text
        event=response.json()
    books=client.get('/api/workbench',headers=headers['finance']).json()['books']
    book=next(row for row in books if row['company_id']==company['id'])
    disabled=client.put(f"/api/companies/{company['id']}/intelligence/settings",headers=headers['accountant'],json={'enabled':False})
    assert disabled.status_code == 200
    mapped=client.post(f"/api/business/events/{event['id']}/handoff",headers=headers['finance'],json={'book_id':book['id'],'expected_version':event['version'],'idempotency_key':uuid4().hex})
    assert mapped.status_code == 200,mapped.text
    mapping=mapped.json()
    def add_bank(stable):
        imported=client.post(f"/api/books/{book['id']}/imports",headers=headers['accountant'],json={'source_name':'合成银行原始流水','namespace':'configured-source','bank_account':'configured-account','rows':[{
            'external_id':'display-'+stable,'stable_transaction_id':stable,'date':'2026-10-10','description':'办公耗材银行付款','counterparty':'示例办公商店','amount':'268.10','direction':'expense','evidence_ref':'明确合成付款回单'}]})
        assert imported.status_code == 200,imported.text
        suggested=client.post(f"/api/books/{book['id']}/suggestions",headers=headers['accountant'])
        assert suggested.status_code == 200,suggested.text
    add_bank('initial-payment')
    candidates=client.get(f"/api/reconciliation/books/{book['id']}/events/{event['id']}/candidates",headers=headers['accountant'])
    assert candidates.status_code == 200,candidates.text
    request=command(selection(candidates.json())).model_dump()
    confirmed=client.post('/api/reconciliation/confirm',headers=headers['accountant'],json=request)
    assert confirmed.status_code == 200,confirmed.text
    return headers,mapping,add_bank


@pytest.mark.parametrize('before_review',[True,False])
def test_real_http_linked_bank_late_duplicate_blocks_approval_or_export_even_ai_disabled(client,before_review):
    headers,mapping,add_bank=http_linked(client)
    path=f"/api/books/{mapping['book_id']}/drafts/{mapping['draft_id']}"
    draft=client.get(path,headers=headers['accountant']).json()
    if not before_review:
        approved=client.post(path+'/approve',headers=headers['accountant'],json={'expected_version':draft['version']})
        assert approved.status_code == 200,approved.text
    add_bank('late-id-same-payment-facts')
    current=client.get(path,headers=headers['accountant'])
    assert current.status_code == 200 and any('已关联银行流水' in issue and '相似交易' in issue for issue in current.json()['issues'])
    if before_review:
        denied=client.post(path+'/approve',headers=headers['accountant'],json={'expected_version':current.json()['version']})
    else:
        denied=client.post(f"/api/books/{mapping['book_id']}/exports",headers=headers['accountant'],json={'draft_ids':[mapping['draft_id']],'idempotency_key':uuid4().hex})
    assert denied.status_code in {409,422},denied.text
    assert '相似交易' in denied.json()['detail'] and client.supplier.extractions == 0
