from test_business_api import auth, client


def test_rule_governance_http_is_company_scoped_and_requires_current_book_access(client):
    owner = auth(client, 'accountant')
    employee = auth(client, 'employee')
    book = client.post('/api/books', headers=owner, json={'name': '规则治理合成企业', 'period': '2026-10'}).json()
    entity = client.post(f"/api/books/{book['id']}/ledger/entities", headers=owner, json={'kind': 'counterparty', 'code': 'P001', 'name': '本企业供方'}).json()
    path = f"/api/books/{book['id']}/learning/governance"
    value = client.get(path, headers=owner)
    assert value.status_code == 200
    assert value.json()['counterparties'][0]['id'] == entity['id']
    assert value.json()['usage'] == [] and value.json()['conflicts'] == []
    assert client.get(path, headers=employee).status_code == 404
    assert client.get(path).status_code == 401
    assert client.get('/api/books/unknown/learning/governance', headers=owner).status_code == 404
