"""The same exact-money and period fixtures cross real HTTP validation."""
from uuid import uuid4

from test_business_api import auth, client
from test_stage10_domain import CASES


def test_http_money_contract_and_invalid_amounts_preserve_draft(client):
    headers = auth(client, 'accountant')
    book = client.post('/api/books', headers=headers, json={'name': '金额契约合成企业', 'period': '2026-10'}).json()['id']
    base = '/api/books/' + book
    imported = client.post(base + '/imports', headers=headers, json={'source_name': '合成契约测试', 'rows': [
        {'external_id': uuid4().hex, 'date': '2026-10-10', 'description': '办公费', 'amount': '1.00', 'direction': 'expense', 'evidence_ref': '合成测试资料'}]})
    assert imported.status_code == 200, imported.text
    client.post(base + '/suggestions', headers=headers)
    draft = client.get(base, headers=headers).json()['drafts'][0]
    path = base + '/drafts/' + draft['id']
    for case in CASES['money']:
        response = client.patch(path, headers=headers, json={'expected_version': draft['version'], 'description': '合成金额边界草稿',
            'lines': [{'account_code': '1002', 'debit': case['value'], 'credit': '0'}]})
        assert response.status_code == (200 if case['valid'] else 422), response.text
        if case['valid']:
            draft = response.json()
            assert draft['lines'][0]['debit'] == f"{case['cents'] // 100}.{case['cents'] % 100:02d}"
        else:
            assert client.get(path, headers=headers).json()['version'] == draft['version']


def test_http_period_contract(client):
    headers = auth(client, 'accountant')
    for case in CASES['period']:
        result = client.post('/api/books', headers=headers, json={'name': '期间契约合成企业', 'period': case['value']})
        assert result.status_code == (201 if case['valid'] else 422), result.text
