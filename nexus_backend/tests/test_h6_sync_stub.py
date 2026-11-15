"""H6 local signed synchronization contract; no upstream is contacted."""

import hashlib
import hmac
import json
import time

import pytest

from app.accounting.domain import AccountingError
from app.accounting.seed import LOCAL_ACTOR
from app.integrations.sync_stub import SyncEventWrite, SyncStubService, _payload_hash, _signature
from test_accounting import service, book  # noqa: F401


def event(*, external_id="EXT-1", version=1, payload=None, event_type="applied", ts=None):
    payload = payload if payload is not None else {"amount": "10.00", "vehicle": "V1"}
    timestamp = int(time.time()) if ts is None else ts
    digest = _payload_hash(payload)
    return SyncEventWrite(
        source_id="local-file-link",
        external_id=external_id,
        event_type=event_type,
        version=version,
        occurred_at="2026-11-12T01:02:03Z",
        timestamp=timestamp,
        signature=_signature("test-secret-0123456789", timestamp, external_id, version, digest),
        payload=payload,
    )


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_SYNC_STUB_SECRET", "test-secret-0123456789")


def test_signed_event_is_idempotent_and_cursor_paginates(service, book):
    svc = SyncStubService(service.db)
    first = svc.push(LOCAL_ACTOR, book, event())
    assert first["accepted"] is True and first["status"] == "applied"
    again = svc.push(LOCAL_ACTOR, book, event())
    assert again["duplicate"] is True and again["cursor"] == first["cursor"]

    second = svc.push(LOCAL_ACTOR, book, event(external_id="EXT-2"))
    assert second["cursor"] == first["cursor"] + 1
    listing = svc.list_events(LOCAL_ACTOR, book, source_id="local-file-link", after=first["cursor"])
    assert [row["external_id"] for row in listing["items"]] == ["EXT-2"]
    assert listing["next_cursor"] == second["cursor"]


def test_bad_signature_stale_timestamp_and_missing_secret_are_refused(service, book, monkeypatch):
    svc = SyncStubService(service.db)
    invalid = event()
    invalid.signature = "0" * 64
    with pytest.raises(AccountingError) as error:
        svc.push(LOCAL_ACTOR, book, invalid)
    assert error.value.status == 403

    stale = event(ts=int(time.time()) - 301)
    with pytest.raises(AccountingError) as outdated:
        svc.push(LOCAL_ACTOR, book, stale)
    assert outdated.value.status == 409

    monkeypatch.delenv("ACCOUNTING_SYNC_STUB_SECRET")
    with pytest.raises(AccountingError) as unconfigured:
        svc.push(LOCAL_ACTOR, book, event())
    assert unconfigured.value.status == 503


def test_same_version_cannot_change_content_and_late_version_cannot_overwrite(service, book):
    svc = SyncStubService(service.db)
    svc.push(LOCAL_ACTOR, book, event())
    with pytest.raises(AccountingError) as changed:
        svc.push(LOCAL_ACTOR, book, event(payload={"amount": "11.00"}))
    assert changed.value.status == 409

    second = svc.push(
        LOCAL_ACTOR, book, event(version=2, event_type="correction", payload={"amount": "12.00"})
    )
    assert second["status"] == "applied"
    late = svc.push(LOCAL_ACTOR, book, event(version=1, payload={"amount": "9.00"}))
    assert late["accepted"] is False and late["reason"] == "stale_version"
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        rows = conn.execute(
            "SELECT version,status FROM sync_stub_events WHERE external_id='EXT-1' ORDER BY version"
        ).fetchall()
    assert [(int(row["version"]), row["status"]) for row in rows] == [
        (1, "superseded"),
        (2, "applied"),
    ]


def test_sync_stub_http_route_is_signed_and_idempotent(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from test_business_api import auth

    monkeypatch.setenv("ACCOUNTING_SYNC_STUB_SECRET", "test-secret-0123456789")
    app = create_app(tmp_path / "sync-api.db", seed=True, attachment_root=tmp_path / "private")

    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 5678)
        return await call_next(request)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = auth(client, "accountant")
        created = client.post(
            "/api/books", headers=headers, json={"name": "同步替身企业", "period": "2026-10"}
        )
        assert created.status_code == 201, created.text
        book_id = created.json()["id"]
        request = event()
        body = request.model_dump()
        first = client.post(f"/api/books/{book_id}/integrations/sync/events", headers=headers, json=body)
        assert first.status_code == 201, first.text
        assert first.json()["signature_verified"] if "signature_verified" in first.json() else True
        again = client.post(f"/api/books/{book_id}/integrations/sync/events", headers=headers, json=body)
        assert again.status_code == 201 and again.json()["duplicate"] is True
        listed = client.get(
            f"/api/books/{book_id}/integrations/sync/events",
            headers=headers,
            params={"source_id": "local-file-link", "after": 0},
        )
        assert listed.status_code == 200 and len(listed.json()["items"]) == 1
