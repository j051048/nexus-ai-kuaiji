"""Authenticated stage 4 HTTP contracts; real local transactions and an injected supplier."""

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.main import create_app
from app.intelligence.models import InvoiceExtraction, ProviderResult
from app.intelligence.provider import AISettings, ProviderError


class FakeProvider:
    settings = AISettings(base_url="https://supplier.example.test/v1", api_key="never-exposed-key", vision_model="vision-test", journal_model="journal-test")
    def __init__(self):
        self.extractions = 0
    def extract_invoice(self, pages):
        self.extractions += 1
        assert len(pages) == 1 and pages[0].image_data_url.startswith("data:image/")
        return ProviderResult(
            data=InvoiceExtraction.model_validate({"documents": [{
                "document_type": "receipt", "invoice_number": None, "issued_on": "2026-10-10",
                "seller_name": "示例办公商店", "seller_tax_id": None, "buyer_name": None, "buyer_tax_id": None,
                "gross_amount": "268.10", "net_amount": None, "tax_amount": None, "currency": "CNY",
                "pages": [{"page": 1, "raw_text": "示例办公商店 2026-10-10 合计268.10 CNY"}],
                "evidence": [{"field": "gross_amount", "page": 1, "quote": "合计268.10"}],
            }]}), provider="mock", model="vision-test", prompt_version="test-v1", usage=None,
        )


@pytest.fixture
def client(tmp_path):
    supplier = FakeProvider()
    app = create_app(tmp_path / "intelligence-http.db", seed=True, attachment_root=tmp_path / "private", intelligence_provider=supplier)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5678)) as value:
        value.supplier = supplier
        yield value


def auth(client, persona):
    response = client.get("/api/session", params={"persona": persona})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def uploaded_event(client):
    employee = auth(client, "employee")
    company = client.get("/api/business/context", headers=employee).json()["companies"][0]
    event = client.post("/api/business/events", headers=employee, json={"company_id": company["id"], "template_id": "reimbursement", "payload": {
        "occurred_on": "2026-10-10", "description": "员工办公报销", "counterparty": "示例办公商店", "amount": "268.10",
        "direction": "expense", "payment_status": "unpaid", "category": "office", "purpose": "办公耗材", "project": "试点",
    }}).json()
    buffer = BytesIO()
    with Image.new("RGB", (30, 20), "white") as picture:
        picture.save(buffer, format="PNG")
    response = client.post(f"/api/business/events/{event['id']}/attachments?expected_version={event['version']}", headers=employee, files={"file": ("original.png", buffer.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    return employee, response.json()


def test_config_only_exposes_public_ready_information(client):
    employee = auth(client, "employee")
    response = client.get("/api/intelligence/config", headers=employee)
    assert response.status_code == 200, response.text
    assert response.json()["vision_ready"] is True and response.json()["journal_ready"] is True
    assert response.json()["provider_label"] == "supplier.example.test"
    assert "never-exposed-key" not in response.text and "/v1" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_company_opt_in_and_employee_cannot_reconfigure(client):
    employee, event = uploaded_event(client)
    path = f"/api/companies/{event['company_id']}/intelligence/settings"
    before = client.get(path, headers=employee)
    assert before.status_code == 200 and before.json() == {"enabled": False, "can_manage": False}
    assert client.put(path, headers=employee, json={"enabled": True}).status_code == 403
    owner = auth(client, "accountant")
    assert client.put(path, headers=owner, json={"enabled": True}).json() == {"enabled": True, "can_manage": True}
    assert client.get(path, headers=employee).json()["enabled"] is True
    assert client.put(path, headers=owner, json={"enabled": "true"}).status_code == 422
    assert client.put(path, headers=owner, json={"enabled": True, "base_url": "https://evil.test"}).status_code == 422


def test_scoped_ocr_and_idempotent_model_request_keeps_source_private(client):
    employee, event = uploaded_event(client)
    path = f"/api/business/events/{event['id']}"
    body = {"attachment_id": event["attachments"][0]["id"], "expected_version": event["version"], "idempotency_key": "http-ocr-one"}
    assert client.post(path + "/ocr", headers=employee, json=body).status_code == 403
    owner = auth(client, "accountant")
    settings = f"/api/companies/{event['company_id']}/intelligence/settings"
    assert client.put(settings, headers=owner, json={"enabled": True}).status_code == 200
    response = client.post(path + "/ocr", headers=employee, json=body)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "succeeded" and run["result"]["documents"][0]["gross_amount"] == "268.10"
    assert "storage_key" not in response.text and "never-exposed-key" not in response.text
    assert client.post(path + "/ocr", headers=employee, json=body).json()["id"] == run["id"]
    assert client.supplier.extractions == 1
    # A firm manager alone has no original-document access before ledger handoff.
    assert client.get(path + "/intelligence", headers=owner).status_code == 404
    detail = client.get(path + "/intelligence", headers=employee)
    assert detail.status_code == 200 and detail.json()["runs"][0]["id"] == run["id"]
    assert client.post(path + "/reconcile", headers=employee, json={}).status_code == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/intelligence/config"),
    ("GET", "/api/companies/test/intelligence/settings"),
    ("PUT", "/api/companies/test/intelligence/settings"),
    ("GET", "/api/business/events/test/intelligence"),
    ("POST", "/api/business/events/test/ocr"),
    ("POST", "/api/business/events/test/reconcile"),
    ("GET", "/api/intelligence/exceptions"),
    ("POST", "/api/intelligence/findings/test/resolve"),
    ("POST", "/api/business/events/test/bank-links"),
    ("DELETE", "/api/business/events/test/bank-links/source-test"),
    ("POST", "/api/books/test/drafts/test/ai-proposals"),
    ("POST", "/api/intelligence/proposals/test/apply"),
])
def test_all_intelligence_endpoints_require_verified_session(client, method, path):
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=100001"])
def test_exception_pagination_validated_before_service_access(client, query):
    assert client.get("/api/intelligence/exceptions?" + query, headers=auth(client, "employee")).status_code == 422


def test_provider_http_errors_are_fixed_and_do_not_reflect_secrets(client, monkeypatch):
    def fail(*_):
        raise ProviderError("supplier_secret", "raw invoice and never-exposed-key")
    monkeypatch.setattr(client.app.state.intelligence, "config", fail)
    response = client.get("/api/intelligence/config", headers=auth(client, "employee"))
    assert response.status_code == 503
    assert response.json() == {"detail": "票据与模型处理失败，请人工核对或稍后重试"}


def test_route_dispatch_passes_validated_review_bodies_and_current_actor(client):
    calls = []
    class Dispatch:
        def exceptions(self, actor, **kwargs):
            calls.append(("exceptions", actor, kwargs))
            return {"findings": [], "total": 0}
        def resolve(self, actor, finding_id, reason):
            calls.append(("resolve", actor, finding_id, reason))
            return {"resolved": True}
        def link_bank(self, actor, event_id, request):
            calls.append(("link", actor, event_id, request.model_dump()))
            return {}
        def unlink_bank(self, actor, event_id, source_id, reason):
            calls.append(("unlink", actor, event_id, source_id, reason))
            return {}
        def propose(self, actor, book_id, draft_id, request):
            calls.append(("propose", actor, book_id, draft_id, request.model_dump()))
            return {}
        def apply(self, actor, run_id, version):
            calls.append(("apply", actor, run_id, version))
            return {}
    client.app.state.intelligence = Dispatch()
    headers = auth(client, "finance")
    assert client.get("/api/intelligence/exceptions?company_id=c&limit=20&offset=40", headers=headers).json()["total"] == 0
    assert client.post("/api/intelligence/findings/f/resolve", headers=headers, json={"reason": "  原件已核对  "}).status_code == 200
    assert client.post("/api/business/events/e/bank-links", headers=headers, json={"bank_source_ids": ["source-1"], "reason": "同笔付款"}).status_code == 200
    assert client.request("DELETE", "/api/business/events/e/bank-links/source-1", headers=headers, json={"reason": "重复付款关联已核实"}).status_code == 200
    assert client.post("/api/books/b/drafts/d/ai-proposals", headers=headers, json={"expected_version": 2, "idempotency_key": "proposal-once"}).status_code == 200
    assert client.post("/api/intelligence/proposals/r/apply", headers=headers, json={"expected_version": 2}).status_code == 200
    assert calls[0][2] == {"company_id": "c", "limit": 20, "offset": 40}
    assert calls[1][-1] == "原件已核对"
    assert calls[3][-1] == "重复付款关联已核实"
    assert all(call[1].user_id == "local-business-finance" for call in calls)
    assert client.request("DELETE", "/api/business/events/e/bank-links/source-1?reason=secret", headers=headers).status_code == 422
    assert client.post("/api/intelligence/findings/f/resolve", headers=headers, json={"reason": "  "}).status_code == 422
