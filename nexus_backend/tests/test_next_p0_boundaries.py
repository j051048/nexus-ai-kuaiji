"""Release-blocking regressions; synthetic inputs, no external services."""

import asyncio
import time
from pathlib import Path

import pytest
from starlette.requests import Request

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.delivery.callback_router import build_delivery_callback_router
from app.delivery.receipts import MAX_BODY, ProviderReceiptService, sign
from p01_recovery_business import BUSINESS_TABLES, require_latest_business_inventory


@pytest.mark.parametrize(
    "value",
    [
        "user=test password=synthetic-secret dbname=local host=127.0.0.1",
        "dbname=local",
        "mysql://test:synthetic-secret@localhost/db",
        " postgresql://test:synthetic-secret@localhost/db",
        "",
        "bad\npath.db",
    ],
)
def test_invalid_database_locator_rejected_before_io(value, monkeypatch):
    touched = []
    monkeypatch.setattr(Database, "_migrate_sqlite", lambda self: touched.append(True))
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: touched.append(True))
    with pytest.raises(ValueError) as error:
        Database(value)
    assert "synthetic-secret" not in str(error.value)
    assert not touched


@pytest.mark.parametrize("signature", ["é", "Ｆ" * 64, "0" * 65])
def test_callback_bad_signature_is_controlled(signature, monkeypatch):
    monkeypatch.setenv("DELIVERY_CALLBACK_SECRET", "synthetic-callback-secret-" * 2)
    with pytest.raises(AccountingError) as error:
        ProviderReceiptService(None).receive(b"{}", str(int(time.time())), signature, "event")
    assert error.value.status == 401


@pytest.mark.parametrize(
    "raw",
    [
        ("[" * 10000 + "0" + "]" * 10000).encode(),
        b'{"notification_id":"a","notification_id":"b"}',
        b'{"notification_id":NaN}',
    ],
    ids=["deeply-nested", "duplicate-key", "non-finite"],
)
def test_callback_malformed_json_is_controlled(raw, monkeypatch):
    monkeypatch.setenv("DELIVERY_CALLBACK_SECRET", "synthetic-callback-secret-" * 2)
    stamp = str(int(time.time()))
    with pytest.raises(AccountingError) as error:
        ProviderReceiptService(None).receive(raw, stamp, sign(raw, stamp), "event")
    assert error.value.status == 422


def test_callback_stops_reading_oversized_chunked_body():
    read = []

    async def receive():
        read.append(True)
        assert len(read) <= 2, "must not drain attacker-controlled body after limit"
        return {"type": "http.request", "body": b"x" * (MAX_BODY // 2 + 1), "more_body": True}

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
    endpoint = build_delivery_callback_router().routes[0].endpoint
    response = asyncio.run(endpoint(request))
    assert response.status_code == 413
    assert len(read) == 2


@pytest.mark.parametrize(
    "table", ["tax_filings", "tax_filing_attachments", "delivery_provider_events", "handoff_tasks"]
)
def test_recovery_requires_populated_new_business_tables(table):
    inventory = {"tables": {name: [{"id": "synthetic"}] for name in BUSINESS_TABLES}}
    inventory["tables"].pop(table, None)
    with pytest.raises(AssertionError, match=table):
        require_latest_business_inventory(inventory)
