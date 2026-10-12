import io
import sqlite3

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.main import create_app
from app.business.storage import MAX_REQUEST_BYTES


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "workflow.db", seed=True, attachment_root=tmp_path / "private")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5678)) as client:
        yield client


def auth(client, persona):
    response = client.get("/api/session", params={"persona": persona})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def create(client, employee):
    company = client.get("/api/business/context", headers=employee).json()["companies"][0]
    response = client.post("/api/business/events", headers=employee, json={"company_id": company["id"], "template_id": "reimbursement", "payload": {
        "occurred_on": "2026-10-10", "description": "员工办公报销", "counterparty": "示例办公商店", "amount": "268.10",
        "direction": "expense", "payment_status": "unpaid", "category": "office", "purpose": "办公耗材采购", "project": "试点",
    }})
    assert response.status_code == 201, response.text
    return response.json()


def attachment_bytes():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, "PNG")
    return output.getvalue()


def test_employee_submission_approval_handoff_ledger_review_and_private_attachment(client):
    employee, department, finance, accountant = [auth(client, persona) for persona in ("employee", "department", "finance", "accountant")]
    assert client.get("/api/workbench", headers=employee).json()["books"] == []
    event = create(client, employee)
    path = "/api/business/events/" + event["id"]
    assert client.post(path + "/submit", headers=employee, json={"expected_version": 1}).status_code == 422
    assert client.get(path, headers=accountant).status_code == 404
    uploaded = client.post(path + "/attachments?expected_version=1", headers=employee, files={"file": ("发票.png", attachment_bytes(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    event = uploaded.json()
    metadata = event["attachments"][0]
    assert "storage_key" not in metadata
    assert event["version"] == 2
    download_path = "/api/business/attachments/" + metadata["id"] + "/content"
    assert client.get(download_path).status_code == 401
    assert client.get(download_path, headers=accountant).status_code == 404
    download = client.get(download_path, headers=department)
    assert download.status_code == 200 and download.content == attachment_bytes()
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    event = client.post(path + "/submit", headers=employee, json={"expected_version": 2}).json()
    assert event["status"] == "submitted"
    assert client.post(path + "/department-approve", headers=employee, json={"expected_version": event["version"]}).status_code == 403
    department_response = client.post(path + "/department-approve", headers=department, json={"expected_version": event["version"]})
    assert department_response.status_code == 200, department_response.text
    event = department_response.json()
    finance_response = client.post(path + "/finance-approve", headers=finance, json={"expected_version": event["version"]})
    assert finance_response.status_code == 200, finance_response.text
    event = finance_response.json()
    books = client.get("/api/workbench", headers=finance).json()["books"]
    book = next(row for row in books if row["company_id"] == event["company_id"])
    payload = {"expected_version": event["version"], "book_id": book["id"], "idempotency_key": "business-http-001"}
    handoff = client.post(path + "/handoff", headers=finance, json=payload)
    assert handoff.status_code == 200, handoff.text
    receipt = handoff.json()
    assert client.post(path + "/handoff", headers=finance, json=payload).json()["draft_id"] == receipt["draft_id"]
    assert client.get(download_path, headers=accountant).status_code == 200
    detail = client.get("/api/books/" + book["id"], headers=accountant).json()
    draft = next(row for row in detail["drafts"] if row["id"] == receipt["draft_id"])
    assert draft["status"] == "draft" and draft["source_kind"] == "business"
    assert draft["issues"] == [] and draft["business_origin"]["event_id"] == event["id"]
    assert any(line["account_code"] == "2241" for line in draft["lines"])
    assert not any(line["account_code"] == "1002" for line in draft["lines"])
    approved = client.post(f"/api/books/{book['id']}/drafts/{draft['id']}/approve", headers=accountant, json={"expected_version": 1})
    assert approved.status_code == 200, approved.text
    export = client.post(f"/api/books/{book['id']}/exports", headers=accountant, json={"draft_ids": [draft["id"]], "idempotency_key": "business-http-export-001"})
    assert export.status_code == 200 and "268.10" in export.json()["csv"]


def test_attachment_failure_does_not_create_metadata_or_leave_private_blob(client, monkeypatch):
    employee = auth(client, "employee")
    event = create(client, employee)
    root = client.app.state.attachment_store.root
    original = client.app.state.business.add_attachment
    def fail(*args, **kwargs):
        from app.accounting.domain import AccountingError
        raise AccountingError("模拟提交失败", 409)
    monkeypatch.setattr(client.app.state.business, "add_attachment", fail)
    response = client.post(f"/api/business/events/{event['id']}/attachments?expected_version=1", headers=employee, files={"file": ("file.png", attachment_bytes(), "image/png")})
    assert response.status_code == 409 and not [path for path in root.rglob("*") if path.is_file()]
    monkeypatch.setattr(client.app.state.business, "add_attachment", original)
    assert client.get(f"/api/business/events/{event['id']}", headers=employee).json()["attachments"] == []


def test_oversized_chunked_multipart_is_rejected_before_unbounded_parsing(client):
    employee = auth(client, "employee")
    event = create(client, employee)
    boundary = "business-test-boundary"
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="large.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()
    def chunks():
        yield head
        for _ in range(MAX_REQUEST_BYTES // (1024 * 1024) + 2):
            yield b"x" * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()
    response = client.post(f"/api/business/events/{event['id']}/attachments?expected_version=1", headers={**employee, "Content-Type": f"multipart/form-data; boundary={boundary}"}, content=chunks())
    assert response.status_code == 413, response.text
    assert client.get(f"/api/business/events/{event['id']}", headers=employee).json()["attachments"] == []


def test_committed_upload_survives_lost_commit_acknowledgement(client, monkeypatch):
    employee = auth(client, "employee")
    event = create(client, employee)
    database = client.app.state.business.db
    original_connect = database.connect

    class ConnectionProxy:
        def __init__(self, connection):
            self.connection, self.has_attachment = connection, False
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def execute(self, statement, *args):
            if statement.lstrip().upper().startswith("INSERT INTO BUSINESS_ATTACHMENTS"):
                self.has_attachment = True
            return self.connection.execute(statement, *args)
        def commit(self):
            self.connection.commit()
            if self.has_attachment:
                raise sqlite3.OperationalError("simulated lost COMMIT acknowledgement")

    monkeypatch.setattr(database, "connect", lambda: ConnectionProxy(original_connect()))
    response = client.post(f"/api/business/events/{event['id']}/attachments?expected_version=1", headers=employee, files={"file": ("invoice.png", attachment_bytes(), "image/png")})
    assert response.status_code == 503
    monkeypatch.setattr(database, "connect", original_connect)
    saved = client.get(f"/api/business/events/{event['id']}", headers=employee).json()
    assert saved["version"] == 2 and len(saved["attachments"]) == 1
    attachment_id = saved["attachments"][0]["id"]
    assert client.get(f"/api/business/attachments/{attachment_id}/content", headers=employee).content == attachment_bytes()
