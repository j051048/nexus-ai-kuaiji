"""Real local login HTTP boundary, with all workers disabled explicitly."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.accounting.domain import Actor
from app.main import create_app
from test_business_api import auth
from test_stage8_cases_api import prepare
from test_stage13_migration_api import (assert_response, detail, prepare_http,
                                        upload)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "agent-http.db", seed=True, attachment_root=tmp_path / "private", queue_worker_enabled=False)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1588)) as value:
        yield value


def test_http_auth_boundaries_and_strict_question_body(client):
    for path in ("/api/agent-workspace/runs", "/api/agent-workspace/summary", "/api/agent-workspace/companies", "/api/workspace/companies"):
        assert client.get(path).status_code == 401
    headers = auth(client, "employee")
    for query in ("state=bogus", "limit=0", "offset=-1", "q=" + "x" * 201):
        assert client.get("/api/agent-workspace/runs?" + query, headers=headers).status_code in {400, 422}
    assert client.get("/api/workspace/tasks?sort=bogus", headers=headers).status_code == 422
    for body in ({"question": ""}, {"question": "x" * 1001}, {"question": 9}, {"question": "科目", "approve": True}):
        assert client.post("/api/agent-workspace/context/books/missing/drafts/missing/explain", headers=headers, json=body).status_code == 422


def test_http_context_and_explanation_reauthorize_and_never_operate(client):
    employee, _, _, accountant, event, mapping, _ = prepare(client)
    path = f"/api/agent-workspace/context/books/{mapping['book_id']}/drafts/{mapping['draft_id']}"
    assert client.get(path, headers=employee).status_code == 404
    value = assert_response(client.get(path, headers=accountant))
    assert value["mode"] == "evidence_summary" and value["scope"]["draft_id"] == mapping["draft_id"]
    assert value["scope"]["company_id"] == event["company_id"]
    with client.app.state.service.db.transaction(actor=Actor("local-accountant", "local-firm")) as conn:
        before = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("audit_events", "exports", "ai_runs")}
    answer = assert_response(client.post(path + "/explain", headers=accountant, json={"question": "哪些材料需要补充？"}))
    assert answer["mode"] == "evidence_summary" and "原件" in answer["answer"]
    with client.app.state.service.db.transaction(actor=Actor("local-accountant", "local-firm")) as conn:
        after = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in before}
    assert before == after
    assert client.get(path.replace(mapping["draft_id"], uuid4().hex), headers=accountant).status_code == 404


def test_http_migration_run_is_private_real_job_and_respects_revocation(client):
    c = prepare_http(client)
    upload(c)
    current = detail(c)
    assert_response(client.post(c.path + "/parse", headers=c.owner, json={"expected_version": current["project"]["version"], "reason": "合成后台解析", "idempotency_key": uuid4().hex}), 202)
    owner = assert_response(client.get("/api/agent-workspace/runs", headers=c.owner, params={"company_id": c.book["company_id"]}))
    reviewer = assert_response(client.get("/api/agent-workspace/runs", headers=c.finance, params={"company_id": c.book["company_id"]}))
    employee = assert_response(client.get("/api/agent-workspace/runs?state=all", headers=c.employee))
    assert owner["pagination"]["total"] == reviewer["pagination"]["total"] == 1
    row = owner["runs"][0]
    assert row["kind"] == "migration" and row["status"] == "queued" and row["processed"] == 0 and row["total"] is None
    assert row["route"]["id"] == c.project and row["route"]["book_id"] == c.book["id"]
    assert c.project not in json.dumps(employee) and c.book["id"] not in json.dumps(employee)
    assert_response(client.request("DELETE", c.path + "/access/local-business-finance", headers=c.owner, json={"reason": "已撤销历史访问范围"}))
    assert assert_response(client.get("/api/agent-workspace/runs?state=all", headers=c.finance, params={"company_id": c.book["company_id"]}))["pagination"]["total"] == 0
    assert assert_response(client.get("/api/agent-workspace/companies", headers=c.finance))["pagination"]["total"] == 0
