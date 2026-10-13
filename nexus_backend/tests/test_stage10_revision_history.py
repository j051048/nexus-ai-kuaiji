import io
from uuid import uuid4
from PIL import Image
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, BookMemberGrant
from app.revisions.history import SourceHistoryService
from app.revisions.models import RevisionSubmit
from app.cases.models import CaseCreate, CaseRespond, CaseReviewResponse
from app.cases.service import CaseService
from test_intelligence_workflow import ai_case
from test_stage10_fact_revisions import prepare, review_request


def cross_period(c):
    event, old = prepare(c)
    new = c.ledger.create_book(c.manager, BookCreate(name='历史投影新期', company_id=c.company['id'], period='2026-11'))
    for name,role in (('finance','editor'),('accountant','reviewer')):
        c.directory.grant_book_member(c.manager,new['id'],c.actors[name].user_id,BookMemberGrant(role=role))
    current=c.business.get_event(c.actors['finance'],event['id'])
    revision=c.revisions.submit(c.actors['finance'],event['id'],RevisionSubmit(
        expected_event_version=current['version'],expected_fact_version=current['fact_version'],expected_draft_version=1,
        target_book_id=new['id'],payload={**current['payload'],'occurred_on':'2026-11-03','amount':'80.00'},
        reason='原日期和金额有误，经独立复核改到新期',idempotency_key=uuid4().hex))
    c.revisions.review(c.actors['accountant'],revision['id'],review_request(revision))
    cases=CaseService(c.db,c.business)
    task=cases.create(c.actors['accountant'],CaseCreate(event_id=event['id'],kind='supplement',title='新期间后续补件',reason='新期补充另一份业务证明',attachment_required=True,idempotency_key=uuid4().hex))
    output=io.BytesIO();Image.new('RGB',(31,19),'black').save(output,'PNG')
    metadata=c.storage.put('新期间后续原件.png','image/png',output.getvalue())
    task=cases.add_attachment(c.actors['employee'],task['id'],task['version'],uuid4().hex,metadata)['case']
    task=cases.command(c.actors['employee'],task['id'],'respond',CaseRespond(expected_version=task['version'],reason='明确为新期上传的原件',idempotency_key=uuid4().hex))
    cases.command(c.actors['accountant'],task['id'],'review-response',CaseReviewResponse(expected_version=task['version'],reason='独立核对新期补件',idempotency_key=uuid4().hex,decision='accept'))
    c.directory.revoke_book_member(c.manager,new['id'],c.actors['accountant'].user_id)
    return event,old,new,metadata,SourceHistoryService(c.db,c.storage)


def test_only_old_scope_reads_old_snapshot_and_original_never_new_material(ai_case):
    c=ai_case;event,old,new,new_attachment,history=cross_period(c)
    value=history.get(c.actors['accountant'],old['book_id'],old['source_id'])
    assert value['payload']['amount']=='100.00' and value['payload']['occurred_on']=='2026-10-03'
    assert value['submitted_snapshot']['payload']['amount']=='100.00'
    assert [r['id'] for r in value['attachments']]==[event['attachments'][0]['id']]
    assert 'storage_key' not in str(value) and new['id'] not in str(value)
    data,filename,media_type=history.download(c.actors['accountant'],old['book_id'],old['source_id'],event['attachments'][0]['id'])
    assert data and filename=='合成发票.png' and media_type=='image/png'
    with pytest.raises(AccountingError,match='不属于'):
        history.download(c.actors['accountant'],old['book_id'],old['source_id'],new_attachment['id'])
    with pytest.raises(AccountingError):
        c.business.get_event(c.actors['accountant'],event['id'])


def test_history_current_revocation_other_scope_and_blob_tamper_fail_closed(ai_case):
    c=ai_case;event,old,_,_,history=cross_period(c)
    with pytest.raises(AccountingError):
        history.get(c.actors['outsider'],old['book_id'],old['source_id'])
    original=event['attachments'][0]
    with c.db.transaction(actor=c.manager) as conn:
        metadata=dict(conn.execute('SELECT * FROM business_attachments WHERE id=?',(original['id'],)).fetchone())
    c.storage._path(metadata['storage_key']).write_bytes(b'tampered')
    with pytest.raises(AccountingError,match='完整性'):
        history.download(c.actors['accountant'],old['book_id'],old['source_id'],original['id'])
    c.directory.revoke_book_member(c.manager,old['book_id'],c.actors['accountant'].user_id)
    with pytest.raises(AccountingError):
        history.get(c.actors['accountant'],old['book_id'],old['source_id'])
