"""真实站内登录、私有补件下载和异常阻断 API 验证。"""
from uuid import uuid4
from fastapi.testclient import TestClient
import pytest
from app.main import create_app
from test_business_api import auth, create, attachment_bytes


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "stage8-cases.db", seed=True, attachment_root=tmp_path / "private")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5678)) as client:
        yield client


def prepare(client, kind="supplement"):
    employee, department, finance, accountant = [auth(client, persona) for persona in ("employee", "department", "finance", "accountant")]
    event = create(client, employee)
    path = "/api/business/events/" + event["id"]
    event = client.post(path + "/attachments?expected_version=1", headers=employee, files={"file": ("发票.png", attachment_bytes(), "image/png")}).json()
    for action, headers in (("submit", employee), ("department-approve", department), ("finance-approve", finance)):
        response = client.post(path + "/" + action, headers=headers, json={"expected_version": event["version"]})
        assert response.status_code == 200, response.text
        event = response.json()
    book = next(book for book in client.get("/api/workbench", headers=finance).json()["books"] if book["company_id"] == event["company_id"])
    handoff = client.post(path + "/handoff", headers=finance, json={"expected_version": event["version"], "book_id": book["id"], "idempotency_key": uuid4().hex}).json()
    response = client.post("/api/cases", headers=accountant, json={"event_id": event["id"], "kind": kind, "title": "补充合同用途", "reason": "请说明业务用途并补充原件", "required_fields": ["用途"] if kind == "supplement" else [], "attachment_required": kind == "supplement", "idempotency_key": uuid4().hex})
    assert response.status_code == 201, response.text
    return employee, department, finance, accountant, event, handoff, response.json()


def cmd(row, **changes):
    return {"expected_version": row["version"], "idempotency_key": uuid4().hex, "reason": "已核实补件与业务相符", **changes}


def test_http_supplement_private_storage_acceptance_replacement_and_final_review(client):
    employee, department, finance, accountant, event, mapping, task = prepare(client)
    path = "/api/cases/" + task["id"]
    assert client.get("/api/cases", headers=employee).json()["total"] == 1
    assert client.get(path, headers=department).status_code == 404
    assert client.post(path + "/close", headers=accountant, json=cmd(task)).status_code == 409
    key = uuid4().hex
    upload = client.post(path + "/attachments", headers=employee, params={"expected_version": task["version"], "idempotency_key": key}, files={"file": ("清晰票据.png", attachment_bytes(), "image/png")})
    assert upload.status_code == 200, upload.text
    task = upload.json()
    attachment = task["responses"][0]["attachments"][0]
    assert "storage_key" not in str(task)
    download = "/api/cases/attachments/" + attachment["id"] + "/content"
    assert client.get(download).status_code == 401
    assert client.get(download, headers=department).status_code == 404
    assert client.get(download, headers=employee).content == attachment_bytes()
    assert client.get("/api/business/attachments/" + attachment["id"] + "/content", headers=accountant).status_code == 404
    result = client.post(path + "/respond", headers=employee, json=cmd(task, fields={"用途": "办公耗材采购"}))
    assert result.status_code == 200, result.text
    task = result.json()
    result = client.post(path + "/review-response", headers=accountant, json=cmd(task, decision="accept", supersedes_attachment_ids=[event["attachments"][0]["id"]]))
    assert result.status_code == 200, result.text
    task = result.json()
    assert client.get("/api/business/attachments/" + attachment["id"] + "/content", headers=accountant).content == attachment_bytes()
    approve = f"/api/books/{mapping['book_id']}/drafts/{mapping['draft_id']}/approve"
    assert client.post(approve, headers=accountant, json={"expected_version": 1}).status_code == 422
    result = client.post(path + "/close", headers=accountant, json=cmd(task))
    assert result.status_code == 200, result.text
    draft = client.get(f"/api/books/{mapping['book_id']}/drafts/{mapping['draft_id']}", headers=accountant).json()
    assert draft["status"] == "draft"


def test_http_internal_case_hidden_from_employee_and_public_history_only(client):
    employee, department, _, accountant, _, _, task = prepare(client, "exception")
    assert client.get("/api/cases", headers=employee).json()["total"] == 0
    assert client.get("/api/cases/" + task["id"], headers=employee).status_code == 404
    assert client.get("/api/cases/" + task["id"], headers=department).status_code == 404
    assert client.get("/api/cases/" + task["id"], headers=accountant).status_code == 200


def test_http_supplement_attachment_content_validation_and_byte_limit(client):
    employee, _, _, _, _, _, task = prepare(client)
    path = "/api/cases/" + task["id"] + "/attachments"
    params = {"expected_version": task["version"], "idempotency_key": uuid4().hex}
    response = client.post(path, headers=employee, params=params, files={"file": ("伪造.pdf", b"not a pdf", "application/pdf")})
    assert response.status_code == 422
    response = client.post(path, headers=employee, params=params, files={"file": ("过大.pdf", b"x" * (10 * 1024 * 1024 + 128 * 1024 + 1), "application/pdf")})
    assert response.status_code == 413
