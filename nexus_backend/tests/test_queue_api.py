"""Queue HTTP contract and input trust boundary; no suppliers or real customer data."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.accounting.domain import Actor
from app.jobs.router import build_queue_router


@pytest.fixture
def queue_client():
    calls = []
    actor = Actor("queue-api-test-person", "queue-api-test-firm")

    def authenticated(authorization: str | None = Header(None)):
        if authorization != "Bearer test-queue-session":
            raise HTTPException(401, "请登录")
        return actor

    class RecordingQueue:
        def enqueue(self, current, request):
            calls.append(("enqueue", current, request.model_dump()))
            return {"id": "batch-test", "jobs": [], "counts": {"queued": 1}}

        def list_batches(self, current, **filters):
            calls.append(("list", current, filters))
            return {"batches": [], "total": 0}

        def get_batch(self, current, batch_id):
            calls.append(("get", current, batch_id))
            return {"id": batch_id, "jobs": [], "counts": {}}

        def retry_job(self, current, job_id, key):
            calls.append(("retry_job", current, job_id, key))
            return {"id": job_id, "status": "queued"}

        def retry_batch(self, current, batch_id, key):
            calls.append(("retry_batch", current, batch_id, key))
            return {"id": batch_id, "jobs": []}

        def cancel_job(self, current, job_id, reason):
            calls.append(("cancel", current, job_id, reason))
            return {"id": job_id, "status": "cancelled"}

    app = FastAPI()
    app.state.queue = RecordingQueue()
    app.include_router(build_queue_router(authenticated))
    with TestClient(app) as client:
        client.queue_calls = calls
        client.queue_actor = actor
        client.queue_headers = {"Authorization": "Bearer test-queue-session"}
        yield client


def batch_body():
    return {"company_id": "company-test", "items": [{"event_id": "event-test", "attachment_id": "attachment-test", "expected_version": 2}], "idempotency_key": "batch-api-test-key"}


def test_queue_endpoints_use_authenticated_actor_and_never_take_identity_from_body(queue_client):
    client = queue_client
    response = client.post("/api/intelligence/batches", headers=client.queue_headers, json=batch_body())
    assert response.status_code == 202
    assert client.queue_calls[0] == ("enqueue", client.queue_actor, batch_body())
    response = client.get("/api/intelligence/batches?company_id=c&event_id=e&limit=10&offset=20", headers=client.queue_headers)
    assert response.status_code == 200
    assert client.queue_calls[-1] == ("list", client.queue_actor, {"company_id": "c", "event_id": "e", "limit": 10, "offset": 20})
    assert client.get("/api/intelligence/batches/batch-test", headers=client.queue_headers).status_code == 200
    for path in ("/api/intelligence/jobs/job-test/retry", "/api/intelligence/batches/batch-test/retry"):
        assert client.post(path, headers=client.queue_headers, json={"idempotency_key": "retry-test-key"}).status_code == 200
    assert client.post("/api/intelligence/jobs/job-test/cancel", headers=client.queue_headers, json={"reason": "已撤销重复上传的附件"}).status_code == 200
    assert client.queue_calls[-1] == ("cancel", client.queue_actor, "job-test", "已撤销重复上传的附件")
    assert all(call[1] == client.queue_actor for call in client.queue_calls)


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/intelligence/batches", batch_body()),
    ("GET", "/api/intelligence/batches", None),
    ("GET", "/api/intelligence/batches/batch-test", None),
    ("POST", "/api/intelligence/jobs/job-test/retry", {"idempotency_key": "retry-test-key"}),
    ("POST", "/api/intelligence/batches/batch-test/retry", {"idempotency_key": "retry-test-key"}),
    ("POST", "/api/intelligence/jobs/job-test/cancel", {"reason": "测试取消"}),
])
def test_anonymous_queue_reads_and_mutations_do_not_reach_service(queue_client, method, path, body):
    response = queue_client.request(method, path, json=body) if body else queue_client.request(method, path)
    assert response.status_code == 401
    assert queue_client.queue_calls == []


@pytest.mark.parametrize("mutation", [
    lambda body: body.update({"created_by": "victim", "firm_id": "other-firm"}),
    lambda body: body["items"][0].update({"lease_token": "forged-worker-token"}),
    lambda body: body["items"][0].update({"expected_version": True}),
    lambda body: body["items"][0].update({"expected_version": "2"}),
    lambda body: body.update({"items": []}),
    lambda body: body.update({"items": body["items"] * 101}),
    lambda body: body.update({"idempotency_key": "        "}),
    lambda body: body.update({"company_id": 42}),
])
def test_untrusted_queue_control_fields_and_invalid_batch_are_rejected(queue_client, mutation):
    body = batch_body()
    mutation(body)
    response = queue_client.post("/api/intelligence/batches", headers=queue_client.queue_headers, json=body)
    assert response.status_code == 422
    assert queue_client.queue_calls == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=100001"])
def test_queue_pagination_is_bounded(queue_client, query):
    response = queue_client.get("/api/intelligence/batches?" + query, headers=queue_client.queue_headers)
    assert response.status_code == 422
    assert queue_client.queue_calls == []


def test_cancel_requires_explicit_nonblank_reason(queue_client):
    for body in ({}, {"reason": " "}, {"reason": "测试", "force": True}):
        response = queue_client.post("/api/intelligence/jobs/job-test/cancel", headers=queue_client.queue_headers, json=body)
        assert response.status_code == 422
    assert queue_client.queue_calls == []
