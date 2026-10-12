"""Operations HTTP authentication and exact money inputs, independent of suppliers."""

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.accounting.domain import Actor
from app.operations.router import build_operations_router


@pytest.fixture
def operations_client():
    calls = []
    actor = Actor("operations-person", "operations-firm")

    def authenticated(authorization: str | None = Header(None)):
        if authorization != "Bearer operations-test-session":
            raise HTTPException(401, "请登录")
        return actor

    class RecordingOperations:
        def companies(self, current):
            calls.append(("companies", current))
            return {"companies": []}

        def overview(self, current, company, period=None):
            calls.append(("overview", current, company, period))
            return {"company": {"id": company}, "period": period or "2026-10"}

        def policy(self, current, company, payload):
            calls.append(("policy", current, company, payload.model_dump()))
            return {"version": 1, "quota_enabled": payload.quota_enabled}

        def refresh(self, current, company):
            calls.append(("refresh", current, company))
            return {"company": {"id": company}}

        def acknowledge(self, current, alert, payload):
            calls.append(("acknowledge", current, alert, payload.model_dump()))
            return {"id": alert, "state": "acknowledged"}

        def reconcile(self, current, cost, payload):
            calls.append(("reconcile", current, cost, payload.model_dump()))
            return {"id": cost, "state": "reconciled", "confirmed_cost": payload.confirmed_cost + "0000"}

    app = FastAPI()
    app.state.operations = RecordingOperations()
    app.include_router(build_operations_router(authenticated))
    with TestClient(app) as client:
        client.operations_actor = actor
        client.operations_calls = calls
        client.operations_headers = {"Authorization": "Bearer operations-test-session"}
        yield client


def policy_body():
    return {
        "expected_version": 0, "quota_enabled": True, "monthly_limit": "100.00", "ocr_reserve": "1.00", "proposal_reserve": "2.00",
        "failure_threshold": 3, "failure_window_minutes": 60, "backlog_threshold": 10,
        "backlog_age_minutes": 60, "queue_wait_minutes": 10, "worker_stale_seconds": 90,
    }


def test_operations_endpoints_forward_verified_actor_and_exact_inputs(operations_client):
    client = operations_client
    headers = client.operations_headers
    assert client.get("/api/operations/companies", headers=headers).status_code == 200
    assert client.get("/api/operations/companies/company/overview?period=2026-10", headers=headers).status_code == 200
    assert client.operations_calls[-1] == ("overview", client.operations_actor, "company", "2026-10")
    assert client.put("/api/operations/companies/company/policy", headers=headers, json=policy_body()).status_code == 200
    assert client.operations_calls[-1] == ("policy", client.operations_actor, "company", policy_body())
    assert client.post("/api/operations/companies/company/refresh", headers=headers, json={}).status_code == 200
    ack = {"reason": "值班财务已知晓并安排人工核对", "expected_version": 1}
    assert client.post("/api/operations/alerts/alert/acknowledge", headers=headers, json=ack).status_code == 200
    assert client.operations_calls[-1] == ("acknowledge", client.operations_actor, "alert", ack)
    confirmed = {"reason": "依据本企业供应商结算清单核定", "expected_version": 1, "confirmed_cost": "0.30"}
    assert client.post("/api/operations/costs/cost/reconcile", headers=headers, json=confirmed).status_code == 200
    assert client.operations_calls[-1] == ("reconcile", client.operations_actor, "cost", confirmed)
    assert all(call[1] == client.operations_actor for call in client.operations_calls)


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/operations/companies", None),
    ("GET", "/api/operations/companies/company/overview", None),
    ("PUT", "/api/operations/companies/company/policy", policy_body()),
    ("POST", "/api/operations/companies/company/refresh", {}),
    ("POST", "/api/operations/alerts/alert/acknowledge", {"reason": "测试确认", "expected_version": 1}),
    ("POST", "/api/operations/costs/cost/reconcile", {"reason": "测试核定", "expected_version": 1, "confirmed_cost": "0.30"}),
])
def test_operations_anonymous_requests_do_not_reach_company_service(operations_client, method, path, body):
    response = operations_client.request(method, path, json=body)
    assert response.status_code == 401 and operations_client.operations_calls == []


@pytest.mark.parametrize("period", ["1999-12", "2100-01", "2026-00", "2026-13", "2026-1", "2026-10%0A", "2026-10-01", "not-a-month"])
def test_operations_month_is_bounded_and_cannot_be_arbitrary_sql_input(operations_client, period):
    response = operations_client.get("/api/operations/companies/company/overview?period=" + period, headers=operations_client.operations_headers)
    assert response.status_code == 422 and operations_client.operations_calls == []


@pytest.mark.parametrize("mutation", [
    lambda body: body.update({"firm_id": "victim-firm", "can_manage": True}),
    lambda body: body.update({"quota_enabled": "true"}),
    lambda body: body.update({"expected_version": True}),
    lambda body: body.update({"monthly_limit": 100.0}),
    lambda body: body.update({"monthly_limit": "1e2"}),
    lambda body: body.update({"monthly_limit": "100.000"}),
    lambda body: body.update({"monthly_limit": "-0.01"}),
    lambda body: body.update({"monthly_limit": "0.00"}),
    lambda body: body.update({"ocr_reserve": "0.00"}),
    lambda body: body.update({"proposal_reserve": "0.00"}),
    lambda body: body.update({"failure_threshold": 0}),
    lambda body: body.update({"backlog_threshold": 1001}),
    lambda body: body.update({"failure_window_minutes": 10081}),
    lambda body: body.update({"worker_stale_seconds": 29}),
])
def test_budget_inputs_reject_forged_authority_inexact_money_and_unbounded_settings(operations_client, mutation):
    body = policy_body()
    mutation(body)
    response = operations_client.put("/api/operations/companies/company/policy", headers=operations_client.operations_headers, json=body)
    assert response.status_code == 422 and operations_client.operations_calls == []


@pytest.mark.parametrize("body", [
    {"expected_version": 1, "confirmed_cost": "0.30", "reason": " "},
    {"expected_version": 0, "confirmed_cost": "0.30", "reason": "核定"},
    {"expected_version": 1, "confirmed_cost": 0.3, "reason": "核定"},
    {"expected_version": 1, "confirmed_cost": "0.300000", "reason": "核定"},
    {"expected_version": 1, "confirmed_cost": "0.30", "reason": "核定", "state": "released"},
])
def test_cost_reconciliation_requires_reason_version_and_exact_two_decimal_string(operations_client, body):
    response = operations_client.post("/api/operations/costs/cost/reconcile", headers=operations_client.operations_headers, json=body)
    assert response.status_code == 422 and operations_client.operations_calls == []
