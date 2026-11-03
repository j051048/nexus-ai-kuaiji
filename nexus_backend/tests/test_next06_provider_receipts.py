"""NEXT06: signed provider receipts are evidence, never a retry or signature."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.accounting.domain import AccountingError
from app.delivery.receipts import ProviderReceiptService, sign
from test_delivery_followups import case, freeze, grant, queue  # noqa: F401
from test_monthly_delivery_package import ready_case  # noqa: F401
from test_p0_close_dependencies import close_case  # noqa: F401
from test_business import business  # noqa: F401


SECRET = "next06-test-callback-secret-0123456789abcdef"


@pytest.fixture
def receipts(case, monkeypatch):
    monkeypatch.setenv("DELIVERY_CALLBACK_SECRET", SECRET)
    freeze(case)
    grant(case)
    queue(case)
    service = ProviderReceiptService(case.db)
    with case.db.transaction() as conn:
        notification_id = conn.execute("SELECT id FROM delivery_notifications").fetchone()[0]
    return case, service, notification_id


def envelope(notification_id, event_id, event_type="accepted", occurred_at=None):
    return {
        "notification_id": notification_id,
        "provider_event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
    }


def send(receipts, payload, *, timestamp=None, secret=SECRET, event_id=None):
    case, service, _ = receipts
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int((timestamp or datetime.now(timezone.utc)).timestamp()))
    signature = hmac.new(
        secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    return service.receive(raw, stamp, signature, event_id or payload["provider_event_id"])


def test_signed_event_is_stored_once_and_duplicate_is_idempotent(receipts):
    case, service, notification_id = receipts
    payload = envelope(notification_id, "evt-accepted-1")
    first = send(receipts, payload)
    assert first["duplicate"] is False
    assert first["event"]["event_type"] == "accepted"
    assert first["event"]["payload_sha256"] == hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode()
    ).hexdigest()
    second = send(receipts, payload)
    assert second["duplicate"] is True
    assert second["event"]["id"] == first["event"]["id"]
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM delivery_provider_events").fetchone()[0] == 1


def test_signature_timestamp_and_event_header_are_enforced(receipts):
    case, service, notification_id = receipts
    payload = envelope(notification_id, "evt-bad-1")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    with pytest.raises(AccountingError) as bad:
        service.receive(raw, stamp, "0" * 64, "evt-bad-1")
    assert bad.value.status == 401
    with pytest.raises(AccountingError) as stale:
        service.receive(
            raw, str(int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp())),
            sign(raw, str(int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()))),
            "evt-bad-1",
        )
    assert stale.value.status == 401
    with pytest.raises(AccountingError) as mismatch:
        send(receipts, payload, event_id="evt-other")
    assert mismatch.value.status == 422


def test_same_event_id_with_different_content_is_rejected(receipts):
    case, service, notification_id = receipts
    send(receipts, envelope(notification_id, "evt-dup-content", "accepted"))
    with pytest.raises(AccountingError) as conflict:
        send(receipts, envelope(notification_id, "evt-dup-content", "delivered"))
    assert conflict.value.status == 409


def test_out_of_order_events_are_kept_in_time_order(receipts):
    case, service, notification_id = receipts
    later = datetime.now(timezone.utc)
    earlier = later - timedelta(minutes=3)
    send(receipts, envelope(notification_id, "evt-viewed", "viewed", later.isoformat()))
    send(receipts, envelope(notification_id, "evt-accepted", "accepted", earlier.isoformat()))
    timeline = service.timeline(case.reviewer, case.book["id"], notification_id)
    assert [item["event_type"] for item in timeline["events"]] == ["accepted", "viewed"]
    assert timeline["accepted"] is True and timeline["viewed"] is True
    assert timeline["delivered"] is False


def test_receipts_never_change_notification_state_or_create_signature(receipts):
    case, service, notification_id = receipts
    send(receipts, envelope(notification_id, "evt-delivered", "delivered"))
    with case.db.transaction() as conn:
        state = conn.execute(
            "SELECT state FROM delivery_notifications WHERE id=?", (notification_id,)
        ).fetchone()[0]
        signatures = conn.execute(
            "SELECT COUNT(*) FROM monthly_delivery_acknowledgements"
        ).fetchone()[0]
    assert state == "pending"
    assert signatures == 0
    assert service.timeline(case.reviewer, case.book["id"], notification_id)["signed"] is False


def test_wrong_book_and_unknown_notification_are_not_probeable(receipts):
    case, service, notification_id = receipts
    send(receipts, envelope(notification_id, "evt-known", "delivered"))
    with pytest.raises(AccountingError) as wrong:
        service.timeline(case.reviewer, case.other_book["id"], notification_id)
    assert wrong.value.status == 404
    with pytest.raises(AccountingError) as unknown:
        send(receipts, envelope("not-a-notification", "evt-unknown"))
    assert unknown.value.status == 404


def test_callback_route_verifies_headers_and_is_idempotent(receipts):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.delivery.callback_router import build_delivery_callback_router

    case, service, notification_id = receipts
    app = FastAPI()
    app.state.delivery_receipts = service
    app.include_router(build_delivery_callback_router())

    @app.exception_handler(AccountingError)
    async def handled(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": error.detail}, status_code=error.status)

    client = TestClient(app)
    payload = envelope(notification_id, "evt-route", "accepted")
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    headers = {
        "X-Delivery-Timestamp": stamp,
        "X-Delivery-Signature": "sha256=" + sign(raw, stamp),
        "X-Delivery-Event-Id": "evt-route",
        "Content-Type": "application/json",
    }
    first = client.post("/api/delivery/provider-callback", content=raw, headers=headers)
    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    duplicate = client.post("/api/delivery/provider-callback", content=raw, headers=headers)
    assert duplicate.status_code == 200
    bad = client.post(
        "/api/delivery/provider-callback",
        content=raw,
        headers={**headers, "X-Delivery-Signature": "sha256=" + "0" * 64},
    )
    assert bad.status_code == 401
    assert "0" * 16 not in bad.text


def test_worker_runs_one_batch_through_the_configured_channel(receipts, monkeypatch):
    from app.delivery.worker import run_once

    case, service, notification_id = receipts
    monkeypatch.setenv("DELIVERY_CHANNEL", "local_sink")
    result = run_once(case.db, limit=5)
    assert result["sent"] == 1
    assert result["channel"] == "local_sink"
    with case.db.transaction() as conn:
        state, ref = conn.execute(
            "SELECT state,sink_ref FROM delivery_notifications WHERE id=?", (notification_id,)
        ).fetchone()
    assert state == "sink_written"
    assert ref.startswith("local-sink:")


def test_oversized_callback_is_rejected_before_parsing(receipts):
    case, service, notification_id = receipts
    with pytest.raises(AccountingError) as too_large:
        service.receive(b"{" + b"x" * 70000, "1", "0" * 64, "evt-large")
    assert too_large.value.status == 413
