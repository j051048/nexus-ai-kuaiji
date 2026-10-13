import json
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_business_api import auth


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "delivery-api.db", seed=True, attachment_root=tmp_path / "private"), base_url="http://127.0.0.1", client=("127.0.0.1", 1234)) as client:
        yield client


def prepare(client):
    headers = auth(client, "accountant")
    response = client.post("/api/books", headers=headers, json={"name": "合成交付客户", "period": "2026-10"})
    assert response.status_code == 201, response.text
    book = response.json()
    path = "/api/books/" + book["id"]
    response = client.post(path + "/imports", headers=headers, json={"source_name": "合成客户银行明细", "rows": [{"external_id": uuid4().hex, "date": "2026-10-03", "description": "办公费用", "counterparty": "合成供应商", "amount": "23.01", "direction": "expense", "evidence_ref": "已核验合成原件"}]})
    assert response.status_code == 200, response.text
    assert client.post(path + "/suggestions", headers=headers).status_code == 200
    draft = client.get(path, headers=headers).json()["drafts"][0]
    assert client.post(path + "/drafts/" + draft["id"] + "/approve", headers=headers, json={"expected_version": draft["version"]}).status_code == 200
    response = client.post(path + "/exports", headers=headers, json={"draft_ids": [draft["id"]], "idempotency_key": uuid4().hex})
    assert response.status_code == 200, response.text
    export = response.json()
    base = path + "/delivery/exports/" + export["id"]
    template = client.get(base + "/template", headers=headers)
    assert template.status_code == 200, template.text
    return headers, book, export, draft, base, template.json()


def test_http_frozen_manifest_exact_receipt_and_no_fake_sync(client):
    headers, book, _, draft, base, body = prepare(client)
    body["items"][0].update(status="accepted", external_voucher_id="客户回执-001")
    payload = {"format": "json", "content": json.dumps(body), "reason": "合成客户凭证导入回执", "idempotency_key": uuid4().hex}
    response = client.post(base + "/receipts/import", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["summary"]["verified"] == 1 and result["automatic_resend"] is False
    assert client.post(base + "/receipts/import", headers=headers, json=payload).json() == result
    assert client.get("/api/books/" + book["id"] + "/drafts/" + draft["id"], headers=headers).json()["status"] == "exported"
    detail = client.get(base, headers=headers).json()
    assert detail["manifest"]["items"][0]["approved_version"] + 1 == detail["manifest"]["items"][0]["exported_version"]
    assert result["evidence_mode"] == "manually_supplied_receipt"


def test_http_receipt_history_isolated_unknown_and_closed_period_append(client):
    headers, book, _, _, base, body = prepare(client)
    employee = auth(client, "employee")
    assert client.get(base, headers=employee).status_code == 404
    assert client.get(base).status_code == 401
    assert client.post(base + "/receipts", headers=employee, json={**body, "reason": "越权", "idempotency_key": uuid4().hex}).status_code == 404
    path = "/api/books/" + book["id"]
    # The actual workflow now requires a matching receipt and reviewed snapshot.
    assert client.post(path + "/periods/2026-10/close", headers=headers).status_code == 409
    accepted = deepcopy(body)
    accepted['items'][0].update(status='accepted', external_voucher_id='synthetic-before-close-001')
    verified = client.post(base + '/receipts', headers=headers, json={**accepted, 'reason': '关期前逐行核对合成客户回执', 'idempotency_key': uuid4().hex})
    assert verified.status_code == 201, verified.text
    preflight = client.post(path + '/closing/preflights', headers=headers, json={'idempotency_key': uuid4().hex})
    assert preflight.status_code == 201, preflight.text
    snapshot = preflight.json()
    assert snapshot['report']['passed']
    reviewed = client.post(path + '/closing/snapshots/' + snapshot['id'] + '/review', headers=headers, json={'reason': '确认本期银行流水及资料完整并逐项复核', 'materials_confirmed': True})
    assert reviewed.status_code == 200, reviewed.text
    closed = client.post(path + '/closing/close', headers=headers, json={'snapshot_id': snapshot['id'], 'reason': '依据当前复核快照锁定本期'})
    assert closed.status_code == 200, closed.text
    response = client.post(base + "/receipts", headers=headers, json={**body, "reason": "客户软件结果不确定", "idempotency_key": uuid4().hex})
    assert response.status_code == 201, response.text
    assert response.json()["summary"]["received_unverified"] == 1
    assert client.get(path + "/delivery", headers=headers).json()["period_status"] == "closed"


def test_http_body_limits_and_forged_verified_state_rejected(client):
    headers, _, _, _, base, body = prepare(client)
    assert client.post(base + "/receipts", headers=headers, json={**body, "reason": "不允许状态注入", "idempotency_key": uuid4().hex, "verified": True}).status_code == 422
    response = client.post(base + "/receipts/import", headers=headers, json={"format": "json", "content": "{" + " " * 2_000_000, "reason": "超大合成内容", "idempotency_key": uuid4().hex})
    assert response.status_code in {413, 422}
    assert client.get(base, headers=headers).json()["receipts"] == []
