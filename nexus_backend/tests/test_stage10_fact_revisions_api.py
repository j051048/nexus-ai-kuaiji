from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_business_api import auth, create, attachment_bytes


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/'revisions-api.db',seed=True,attachment_root=tmp_path/'private')
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',9876)) as client:
        yield client


def handed_off(client):
    employee,department,finance,accountant=[auth(client,p) for p in ('employee','department','finance','accountant')]
    event=create(client,employee);path='/api/business/events/'+event['id']
    event=client.post(path+'/attachments?expected_version=1',headers=employee,files={'file':('合成原票据.png',attachment_bytes(),'image/png')}).json()
    for action,headers in (('submit',employee),('department-approve',department),('finance-approve',finance)):
        response=client.post(path+'/'+action,headers=headers,json={'expected_version':event['version']})
        assert response.status_code==200,response.text;event=response.json()
    book=next(b for b in client.get('/api/workbench',headers=finance).json()['books'] if b['company_id']==event['company_id'])
    response=client.post(path+'/handoff',headers=finance,json={'expected_version':event['version'],'book_id':book['id'],'idempotency_key':uuid4().hex})
    assert response.status_code==200,response.text
    return employee,finance,accountant,response.json()


def test_http_fact_correction_requires_independent_review_and_freezes_old_source(client):
    employee,finance,accountant,mapping=handed_off(client)
    event=mapping['event'];base='/api/fact-revisions'
    context=client.get(base+'/context/'+event['id'],headers=finance)
    assert context.status_code==200,context.text;ctx=context.json()
    body={'expected_event_version':ctx['event_version'],'expected_fact_version':ctx['fact_version'],'expected_draft_version':ctx['draft_version'],
        'target_book_id':mapping['book_id'],'payload':{**ctx['payload'],'amount':'250.00'},'reason':'原票据金额250元，确认申请误录','idempotency_key':uuid4().hex}
    proposed=client.post(base+'/events/'+event['id'],headers=finance,json=body)
    assert proposed.status_code==200,proposed.text;revision=proposed.json()
    assert revision['state']=='pending'
    review={'expected_version':revision['version'],'decision':'accept','reason':'独立核对原票据和说明后接受修订','idempotency_key':uuid4().hex}
    assert client.post(base+'/'+revision['id']+'/review',headers=finance,json=review).status_code==403
    assert client.get(base+'/'+revision['id'],headers=employee).status_code==404
    approved=client.post(base+'/'+revision['id']+'/review',headers=accountant,json=review)
    assert approved.status_code==200,approved.text;replaced=approved.json()
    assert replaced['state']=='replaced' and replaced['lineage']['new_draft_id']!=mapping['draft_id']
    assert client.post(base+'/'+revision['id']+'/review',headers=accountant,json=review).json()['lineage']==replaced['lineage']
    old=f"/api/books/{mapping['book_id']}/drafts/{mapping['draft_id']}"
    assert client.post(old+'/approve',headers=accountant,json={'expected_version':1}).status_code in (409,422)
    history=f"/api/books/{mapping['book_id']}/sources/{mapping['source_id']}/history"
    original=client.get(history,headers=accountant)
    assert original.status_code==200,original.text
    assert original.json()['payload']['amount']=='268.10' and 'storage_key' not in original.text
    attachment=original.json()['attachments'][0]
    content=client.get(history+'/attachments/'+attachment['id']+'/content',headers=accountant)
    assert content.status_code==200 and content.content==attachment_bytes()
    assert 'no-store' in content.headers['cache-control']
    assert client.get(history+'/attachments/'+attachment['id']+'/content').status_code==401


def test_http_pending_revision_cannot_be_approved_and_float_amount_is_rejected(client):
    _,finance,accountant,mapping=handed_off(client);event=mapping['event']
    ctx=client.get('/api/fact-revisions/context/'+event['id'],headers=finance).json()
    body={'expected_event_version':ctx['event_version'],'expected_fact_version':ctx['fact_version'],'expected_draft_version':ctx['draft_version'],
        'target_book_id':mapping['book_id'],'payload':{**ctx['payload'],'amount':250.0},'reason':'金额必须为精确字符串','idempotency_key':uuid4().hex}
    assert client.post('/api/fact-revisions/events/'+event['id'],headers=finance,json=body).status_code==422
    body['payload']['amount']='250.00'
    assert client.post('/api/fact-revisions/events/'+event['id'],headers=finance,json=body).status_code==200
    approve=f"/api/books/{mapping['book_id']}/drafts/{mapping['draft_id']}/approve"
    denied=client.post(approve,headers=accountant,json={'expected_version':ctx['draft_version']})
    assert denied.status_code in (409,422) and '修订' in denied.text
