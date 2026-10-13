import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_business_api import auth


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/'bridge-api.db',seed=True,attachment_root=tmp_path/'private')
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',6789)) as client:
        yield client


def configured(client):
    headers=auth(client,'accountant')
    response=client.post('/api/books',headers=headers,json={'name':'第十阶段文件接入合成企业','period':'2026-10'})
    assert response.status_code==201,response.text
    book=response.json();base=f"/api/books/{book['id']}/integrations/tplus"
    cfg={'expected_version':0,'external_book_id':'synthetic-customer-book','accounts':[{'local_code':'1002','external_code':'100201'},{'local_code':'6602.02','external_code':'660202'}],'reason':'合成资料映射测试','idempotency_key':uuid4().hex}
    response=client.post(base+'/configuration',headers=headers,json=cfg)
    assert response.status_code==200,response.text
    body=client.get(base+'/template',headers=headers).json();body['records'][0]['external_id']='synthetic-export-account'
    return headers,book,base,body


def test_bridge_http_reference_confirm_is_explicit_and_no_sources_are_generated(client):
    headers,book,base,body=configured(client)
    employee=auth(client,'employee')
    assert client.get(base,headers=employee).status_code==404
    request={'format':'json','content':json.dumps(body,ensure_ascii=False),'reason':'核对合成客户参考科目','idempotency_key':uuid4().hex}
    preview=client.post(base+'/imports',headers=headers,json=request)
    assert preview.status_code==201,preview.text
    value=preview.json();assert value['state']=='preview' and value['creates_journal_sources'] is False
    assert client.post(base+'/imports/'+value['id']+'/confirm',headers=headers,json={'fingerprint':'0'*64}).status_code==409
    result=client.post(base+'/imports/'+value['id']+'/confirm',headers=headers,json={'fingerprint':value['fingerprint']})
    assert result.status_code==200 and result.json()['state']=='confirmed'
    current=client.get('/api/books/'+book['id'],headers=headers).json()
    assert current['sources']==[] and current['drafts']==[]
    overview=client.get(base,headers=headers).json()
    assert overview['live_enabled'] is False and overview['capabilities']['live_write'] is False


def test_bridge_http_parse_limits_and_actual_request_body_limit(client):
    headers,_,base,body=configured(client)
    body['company_id']='different-company'
    invalid=client.post(base+'/imports',headers=headers,json={'format':'json','content':json.dumps(body),'reason':'跨企业不得导入','idempotency_key':uuid4().hex})
    assert invalid.status_code==422
    too_big=client.post(base+'/imports',headers=headers,content=b' '*4_000_001)
    assert too_big.status_code==413,too_big.text
    oversized_utf8=client.post(base+'/imports',headers=headers,json={'format':'json','content':'会'*700_000,'reason':'文件字节上限验证','idempotency_key':uuid4().hex})
    assert oversized_utf8.status_code==413,oversized_utf8.text[:500]


def test_bridge_http_package_frozen_after_review_and_native_send_disabled(client):
    headers,book,base,_=configured(client)
    bid=book['id']
    imported=client.post(f'/api/books/{bid}/imports',headers=headers,json={'source_name':'合成回单','rows':[{'external_id':uuid4().hex,'date':'2026-10-03','description':'办公用品','counterparty':'合成供应商','amount':'80.00','direction':'expense','evidence_ref':'合成核验资料'}]})
    assert imported.status_code==200,imported.text
    client.post(f'/api/books/{bid}/suggestions',headers=headers)
    draft=client.get(f'/api/books/{bid}',headers=headers).json()['drafts'][0]
    assert client.post(f"/api/books/{bid}/drafts/{draft['id']}/approve",headers=headers,json={'expected_version':draft['version']}).status_code==200
    export=client.post(f'/api/books/{bid}/exports',headers=headers,json={'draft_ids':[draft['id']],'idempotency_key':uuid4().hex})
    assert export.status_code==200,export.text
    cmd={'export_id':export.json()['id'],'configuration_version':1,'reason':'凭证经人工审核后准备交付','idempotency_key':uuid4().hex}
    result=client.post(base+'/outbox',headers=headers,json=cmd)
    assert result.status_code==201,result.text
    intent=result.json();assert intent['state']=='awaiting_adapter' and intent['automatic_resend'] is False
    assert client.post(base+'/outbox',headers=headers,json=cmd).json()['id']==intent['id']
    assert client.post(base+'/outbox/'+intent['id']+'/dispatch',headers=headers).status_code==409
    assert client.get(base+'/outbox/'+intent['id'],headers=headers).json()==intent
