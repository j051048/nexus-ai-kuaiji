"""Shipping FastAPI authentication, DTOs and persistent restart workflow."""
from uuid import uuid4

from fastapi.testclient import TestClient

from app.assistant.meter import AssistantMeter
from app.assistant.worker import AssistantWorker
from app.main import create_app
from test_business_api import auth, create as create_event
from test_stage15_conversations import SyntheticChatProvider


def application(tmp_path, provider, seed=True):
    return create_app(tmp_path / "assistant-http.db", seed=seed, attachment_root=tmp_path / "originals", queue_worker_enabled=False, assistant_provider=provider)


def test_real_login_close_browser_restart_and_private_messages(tmp_path):
    provider = SyntheticChatProvider(); app = application(tmp_path, provider)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 7788)) as client:
        accountant, employee = auth(client, "accountant"), auth(client, "employee")
        book = client.get("/api/workbench", headers=accountant).json()["books"][0]
        company = book["company_id"]
        enabled = client.put(f"/api/companies/{company}/intelligence/settings", headers=accountant, json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        scope = client.get("/api/assistant/scope", params={"company_id": company}, headers=accountant)
        assert scope.status_code == 200 and scope.json()["model_available"]
        created = client.post("/api/assistant/conversations", headers=accountant, json={"company_id": company, "book_ids": [book["id"]], "idempotency_key": uuid4().hex})
        assert created.status_code == 201, created.text
        identifier = created.json()["conversation"]["id"]
        assert client.get(f"/api/assistant/conversations/{identifier}").status_code == 401
        assert client.get(f"/api/assistant/conversations/{identifier}", headers=employee).status_code == 404
        body = {"question": "核对保存的账套资料", "expected_scope_version": 1, "idempotency_key": uuid4().hex}
        submitted = client.post(f"/api/assistant/conversations/{identifier}/turns", headers=accountant, json=body)
        assert submitted.status_code == 202, submitted.text
        turn = submitted.json()["turn"]
        assert turn["status"] == "queued" and provider.calls == 0
        assert client.post(f"/api/assistant/conversations/{identifier}/turns", headers=accountant, json=body).json()["turn"]["id"] == turn["id"]
        assert client.post(f"/api/assistant/conversations/{identifier}/turns", headers=accountant, json={**body, "execute_sql": "SELECT *"}).status_code == 422
    # No browser session or original server object is necessary to complete.
    restarted = application(tmp_path, provider, seed=False)
    with TestClient(restarted, base_url="http://127.0.0.1", client=("127.0.0.1", 7788)) as client:
        accountant = auth(client, "accountant")
        worker = AssistantWorker(restarted.state.assistant, provider, AssistantMeter(restarted.state.assistant.db, restarted.state.meter))
        assert worker.run_once() == turn["id"]
        result = client.get(f"/api/assistant/conversations/{identifier}/turns/{turn['id']}", headers=accountant)
        assert result.status_code == 200 and result.json()["turn"]["status"] == "succeeded", result.text
        messages = client.get(f"/api/assistant/conversations/{identifier}/messages", headers=accountant).json()
        assert [item["role"] for item in messages["messages"]] == ["user", "assistant"]
        assert "lease_token" not in str(result.json()) and "storage_key" not in str(messages)


def test_employee_http_event_scope_and_company_switch_never_reuses_private_thread(tmp_path):
    app = application(tmp_path, SyntheticChatProvider())
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 7789)) as client:
        employee, accountant = auth(client, "employee"), auth(client, "accountant")
        event = create_event(client, employee)
        created = client.post("/api/assistant/conversations", headers=employee, json={"company_id": event["company_id"], "event_ids": [event["id"]], "idempotency_key": uuid4().hex})
        assert created.status_code == 201, created.text
        thread = created.json()["conversation"]
        detail = client.get(f"/api/assistant/conversations/{thread['id']}", headers=employee)
        assert detail.status_code == 200 and detail.json()["context"]["books"] == []
        assert client.get(f"/api/assistant/conversations/{thread['id']}", headers=accountant).status_code == 404
        book = next(row for row in client.get("/api/workbench", headers=accountant).json()["books"] if row["company_id"] == event["company_id"])
        denied = client.post("/api/assistant/conversations", headers=employee, json={"company_id": event["company_id"], "book_ids": [book["id"]], "event_ids": [event["id"]], "idempotency_key": uuid4().hex})
        assert denied.status_code == 404
        other = next(row for row in client.get("/api/workbench", headers=accountant).json()["books"] if row["company_id"] != event["company_id"])
        # Demo employees may belong to more than one example company; switching
        # to that company still shows none of the first company's conversations.
        switched = client.get("/api/assistant/conversations", params={"company_id": other["company_id"]}, headers=employee)
        assert switched.status_code == 200 and switched.json()["pagination"]["total"] == 0 and switched.json()["conversations"] == []
        rejected = client.post(f"/api/assistant/conversations/{thread['id']}/turns", headers=employee, json={"question": "解释事项", "expected_scope_version": 1, "idempotency_key": uuid4().hex})
        assert rejected.status_code == 409
