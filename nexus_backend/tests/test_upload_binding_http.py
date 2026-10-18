"""All six direct-upload consumers use an atomic, replayable parent binding."""

import hashlib
import json
from uuid import uuid4

import pytest

from test_business_api import auth, client, create  # noqa: F401
from test_invoice_imports import csv_bytes, row
from test_stage8_cases_api import prepare
from test_stage25_material_attachments import request_fixture
from test_uploads import pdf


def upload(client, headers, purpose, data, filename, media_type):
    created = client.post("/api/uploads", headers=headers, json={
        "purpose": purpose, "filename": filename, "media_type": media_type,
        "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    })
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    saved = client.put(f"/api/uploads/{identifier}/content", headers=headers, content=data)
    assert saved.status_code == 200, saved.text
    return identifier


@pytest.mark.parametrize("purpose,table", [
    ("event_attachment", "business_attachments"),
    ("intake_attachment", "intake_attachments"),
    ("case_attachment", "case_attachments"),
    ("material_attachment", "material_attachments"),
    ("bank_import", "bank_import_files"),
    ("invoice_import", "invoice_import_batches"),
])
def test_http_consumers_bind_and_replay_exactly_once(client, purpose, table):
    employee = auth(client, "employee")
    actor = employee
    data, filename, mime = pdf(), "原始发票.pdf", "application/pdf"
    params, body = {"expected_version": 1}, None
    if purpose == "event_attachment":
        event = create(client, employee)
        target_id, path = event["id"], f"/api/business/events/{event['id']}/attachments"
    elif purpose == "intake_attachment":
        company = client.get("/api/business/context", headers=employee).json()["companies"][0]
        created = client.post("/api/intake/drafts", headers=employee, json={
            "company_id": company["id"], "department_id": company["department_id"],
            "idempotency_key": uuid4().hex,
        })
        assert created.status_code == 201, created.text
        target_id = created.json()["id"]
        path = f"/api/intake/drafts/{target_id}/attachments"
    elif purpose == "case_attachment":
        actor, _department, _finance, _accountant, event, _mapping, task = prepare(client)
        target_id, path = task["id"], f"/api/cases/{task['id']}/attachments"
        params = {"expected_version": task["version"], "idempotency_key": uuid4().hex}
    elif purpose == "material_attachment":
        _owner, actor, _finance, _book, target_id = request_fixture(client)
        path = f"/api/material-requests/{target_id}/attachments"
        data, filename, mime = b"date,income\n2026-10-01,10\n", "银行资料.csv", "text/csv"
    else:
        actor = auth(client, "accountant")
        book = client.get("/api/workbench", headers=actor).json()["books"][0]
        target_id, params = book["id"], {}
        if purpose == "bank_import":
            path = f"/api/books/{target_id}/import-wizard/files"
            data = "交易编号,交易日期,摘要,交易对方,金额,方向,凭据引用\nB001,2026/10/01,采购,供应商,10.00,支出,原件1\n".encode()
        else:
            path = f"/api/books/{target_id}/invoice-imports"
            data = csv_bytes([row("25442000000000000991")])
            body = True
        filename, mime = "导出原件.csv", "text/csv"
    upload_id = upload(client, actor, purpose, data, filename, mime)
    request = {"headers": actor, "params": params}
    request["json" if body else "data"] = {"upload_id": upload_id}
    first = client.post(path, **request)
    assert first.status_code in {200, 201}, first.text
    retry = client.post(path, **request)
    assert retry.status_code == first.status_code, retry.text
    if purpose == "material_attachment":
        assert retry.json()["attachment"] == first.json()["attachment"]
        assert retry.json()["duplicate"] is True
    else:
        assert retry.json() == first.json()
    with client.app.state.service.db.transaction() as conn:
        intent = conn.execute("SELECT * FROM upload_intents WHERE id=?", (upload_id,)).fetchone()
        binding = json.loads(intent["binding_json"])
        assert intent["state"] == "consumed"
        assert binding["target_id"] == target_id and binding["attachment_table"] == table
        assert intent["consumed_ref"] == f"{table}:{binding['attachment_id']}"
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE storage_key=?", (intent["storage_key"],)).fetchone()[0] == 1
        if purpose == "case_attachment":
            assert binding["event_id"] == event["id"] and binding["response_id"]
