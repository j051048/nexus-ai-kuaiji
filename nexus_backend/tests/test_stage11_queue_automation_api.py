from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_business_api import auth

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / 'stage11-queue-http.db', seed=True, attachment_root=tmp_path / 'private'), base_url='http://127.0.0.1', client=('127.0.0.1', 6789)) as client:
        yield client


def test_queue_automation_http_scope_version_and_strict_fields(client):
    headers = auth(client, 'accountant')
    book = client.post('/api/books', headers=headers, json={'name':'合成企业续跑配置', 'period':'2026-10'}).json()
    path = f"/api/intelligence/companies/{book['company_id']}/automation-policy"
    old = client.get(path, headers=headers); assert old.status_code == 200 and old.json()['enabled'] is False
    body = {'expected_version':0, 'enabled':True, 'allow_verified_supplements':True, 'reason':'仅恢复已核验未发送任务'}
    assert client.put(path, headers=headers, json={**body, 'enabled':'true'}).status_code == 422
    assert client.put(path, headers=headers, json={**body, 'execution_user_id':'administrator'}).status_code == 422
    assert client.put(path, headers=headers, json=body).status_code == 200
    assert client.put(path, headers=headers, json={**body, 'enabled':False}).status_code == 409
    employee = auth(client, 'employee')
    assert client.put(path, headers=employee, json={**body,'expected_version':1}).status_code in (403,404)
    assert client.get(f"/api/intelligence/companies/{book['company_id']}/job-metrics", headers=employee).status_code in (403,404)
    assert client.get(f"/api/intelligence/books/{book['id']}/resume-events", headers=employee).status_code == 404
    metrics = client.get(f"/api/intelligence/companies/{book['company_id']}/job-metrics?days=7", headers=headers)
    assert metrics.status_code == 200 and metrics.json()['stages'] == []
    assert client.get(f"/api/intelligence/companies/{book['company_id']}/job-metrics?days=91", headers=headers).status_code == 422
    assert client.get(path).status_code == 401
