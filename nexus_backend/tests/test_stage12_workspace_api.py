"""Permanent HTTP checks for private actionable tasks and exact task lookup."""
from uuid import uuid4

import pytest
from app.accounting.domain import Actor
from test_stage8_cases_api import client, cmd, prepare
from test_business_api import auth, create, attachment_bytes


@pytest.mark.parametrize('persona, role', [('employee', 'employee'), ('department', 'approver'), ('finance', 'finance'), ('accountant', 'accountant')])
def test_http_workspace_current_role_profile(client, persona, role):
    response = client.get('/api/workspace/profile', headers=auth(client, persona))
    assert response.status_code == 200, response.text
    assert response.json()['role'] == role
    assert response.json()['can_review'] is (persona == 'accountant')


def test_http_workspace_department_approval_moves_task_without_self_approval(client):
    employee, department, finance = [auth(client, persona) for persona in ('employee', 'department', 'finance')]
    event = create(client, employee)
    path = '/api/business/events/' + event['id']
    event = client.post(path + '/attachments?expected_version=1', headers=employee, files={'file': ('合成票据.png', attachment_bytes(), 'image/png')}).json()
    event = client.post(path + '/submit', headers=employee, json={'expected_version': event['version']}).json()
    task = '/api/workspace/tasks/approval/' + event['id']
    assert client.get(task, headers=employee).status_code == 404
    assert client.get(task, headers=department).status_code == 200
    assert client.post(path + '/department-approve', headers=employee, json={'expected_version': event['version']}).status_code == 403
    response = client.post(path + '/department-approve', headers=department, json={'expected_version': event['version']})
    assert response.status_code == 200, response.text
    assert client.get(task, headers=department).status_code == 404
    assert client.get('/api/workspace/tasks/finance/' + event['id'], headers=finance).status_code == 200


def test_http_workspace_recipient_has_action_without_private_ledger_identifiers(client):
    employee, department, _, accountant, _, mapping, task = prepare(client)
    path = '/api/workspace/tasks/case/' + task['id']
    response = client.get(path, headers=employee)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row['title'] == '补充业务资料' and row['bucket'] == 'action'
    assert row['book_id'] is None and row['draft_id'] is None and row['event_id'] is None
    assert mapping['book_id'] not in response.text and mapping['draft_id'] not in response.text and task['title'] not in response.text
    assert client.get(path, headers=department).status_code == 404
    assert client.get(path, headers=accountant).status_code == 200


def test_http_workspace_finished_task_returns_404_and_does_not_open_another_case(client):
    _, _, _, accountant, _, _, task = prepare(client, kind='exception')
    response = client.post('/api/cases/' + task['id'] + '/close', headers=accountant, json=cmd(task))
    assert response.status_code == 200, response.text
    assert client.get('/api/workspace/tasks/case/' + task['id'], headers=accountant).status_code == 404
    assert client.get('/api/workspace/tasks/case/missing-' + uuid4().hex, headers=accountant).status_code == 404


def test_http_workspace_membership_revocation_invalidates_previously_visible_action(client):
    employee, _, _, _, event, _, task = prepare(client)
    path = '/api/workspace/tasks/case/' + task['id']
    assert client.get(path, headers=employee).status_code == 200
    with client.app.state.service.db.transaction(write=True, actor=Actor('local-accountant', 'local-firm')) as conn:
        conn.execute('DELETE FROM company_memberships WHERE company_id=? AND user_id=?', (event['company_id'], event['applicant_id']))
    assert client.get(path, headers=employee).status_code == 404
    result = client.get('/api/workspace/tasks?bucket=all', headers=employee)
    assert result.status_code == 200, result.text
    assert task['id'] not in result.text


def test_http_workspace_authentication_and_input_boundaries(client):
    assert client.get('/api/workspace/profile').status_code == 401
    assert client.get('/api/workspace/tasks').status_code == 401
    headers = auth(client, 'employee')
    for query in ('bucket=other', 'kind=other', 'limit=0', 'offset=-1'):
        assert client.get('/api/workspace/tasks?' + query, headers=headers).status_code == 422


def test_http_exact_ocr_job_is_scoped_to_current_enterprise_memberships(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.accounting.seed import LOCAL_ACTOR
    from test_intelligence_workflow import FakeProvider
    app = create_app(tmp_path / 'exact-ocr.db', seed=True, attachment_root=tmp_path / 'private', intelligence_provider=FakeProvider(), queue_worker_enabled=False)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 5678)) as client:
        employee = auth(client, 'employee')
        companies = client.get('/api/business/context', headers=employee).json()['companies'][:2]
        jobs = []
        for company in companies:
            app.state.intelligence.company_settings(LOCAL_ACTOR, company['id'], enabled=True)
            response = client.post('/api/business/events', headers=employee, json={'company_id': company['id'], 'template_id': 'reimbursement', 'payload': {'occurred_on': '2026-10-10', 'description': '合成票据核对', 'counterparty': '虚构供应商', 'amount': '113.00', 'direction': 'expense', 'payment_status': 'unpaid', 'category': 'office'}})
            assert response.status_code == 201, response.text
            event = response.json()
            response = client.post('/api/business/events/' + event['id'] + '/attachments?expected_version=1', headers=employee, files={'file': ('合成票据.png', attachment_bytes(), 'image/png')})
            assert response.status_code == 200, response.text
            event = response.json()
            response = client.post('/api/intelligence/batches', headers=employee, json={'company_id': company['id'], 'items': [{'event_id': event['id'], 'attachment_id': event['attachments'][0]['id'], 'expected_version': event['version']}], 'idempotency_key': uuid4().hex})
            assert response.status_code == 202, response.text
            jobs.append((company['id'], response.json()['jobs'][0]['id'], event['applicant_id']))
        path = '/api/intelligence/jobs/'
        assert client.get(path + jobs[0][1], headers=employee).status_code == 200
        with app.state.service.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            conn.execute('DELETE FROM company_memberships WHERE company_id=? AND user_id=?', (jobs[1][0], jobs[1][2]))
        assert client.get(path + jobs[1][1], headers=employee).status_code == 404
        assert client.get(path + jobs[0][1], headers=employee).status_code == 200
        with app.state.service.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            conn.execute('DELETE FROM company_memberships WHERE company_id=? AND user_id=?', (jobs[0][0], jobs[0][2]))
        assert client.get(path + jobs[0][1], headers=employee).status_code == 404
