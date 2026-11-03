"""Two-session receipt replay and native scope enforcement on restricted PG roles."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading
import time
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.delivery.receipts import ProviderReceiptService, sign
from test_n03_notification_postgres import (  # noqa: F401
    p0_pg,
    p0_case,
    package_case,
    notification_case,
)


@pytest.fixture
def callback(notification_case, monkeypatch):
    c = notification_case
    monkeypatch.setenv("DELIVERY_CALLBACK_SECRET", "synthetic-pg-receipt-secret-0123456789")
    event_id = uuid4().hex
    raw = json.dumps(
        {
            "notification_id": c.notification["notification_id"],
            "provider_event_id": event_id,
            "event_type": "accepted",
            "occurred_at": "2026-11-03T00:00:00+00:00",
        }
    ).encode()
    stamp = str(int(time.time()))
    return c, raw, stamp, event_id


@pytest.mark.parametrize("field", ["firm_id", "company_id", "book_id"])
def test_receipt_native_scope_cannot_cross_notification(callback, field):
    c, raw, stamp, event_id = callback
    with_worker = Database(c.worker_url)
    try:
        service = ProviderReceiptService(with_worker)
        row = service.receive(raw, stamp, sign(raw, stamp), event_id)["event"]
        row.update(id=uuid4().hex, provider_event_id=uuid4().hex)
        row[field] = "foreign-scope"
        with pytest.raises(c.psycopg.errors.CheckViolation):
            with service.worker_transaction() as conn:
                conn.execute(
                    "INSERT INTO delivery_provider_events("
                    + ",".join(row)
                    + ") VALUES("
                    + ",".join("?" for _ in row)
                    + ")",
                    tuple(row.values()),
                )
    finally:
        with_worker.close()


@pytest.mark.parametrize("conflict", [False, True])
def test_two_sessions_replay_without_send_or_acknowledgement(callback, conflict):
    c, raw, stamp, event_id = callback
    barrier = threading.Barrier(2)

    def receive(index):
        db = Database(c.worker_url)
        body = raw.replace(b'"accepted"', b'"delivered"') if index and conflict else raw
        try:
            barrier.wait(timeout=10)
            try:
                result = ProviderReceiptService(db).receive(
                    body, stamp, sign(body, stamp), event_id
                )
                return "duplicate" if result["duplicate"] else "created"
            except AccountingError as error:
                assert error.status == 409
                return "conflict"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(receive, range(2)))
    assert sorted(outcomes) == sorted(["created", "conflict" if conflict else "duplicate"])
    with c.db.transaction(actor=c.reviewer) as conn:
        row = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id=?",
            (c.notification["notification_id"],),
        ).fetchone()
        assert row["state"] == "pending" and row["attempts"] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM delivery_provider_events WHERE provider_event_id=?",
                (event_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM monthly_delivery_acknowledgements WHERE package_id=?",
                (c.package["id"],),
            ).fetchone()[0]
            == 0
        )


def test_tax_receipt_upload_has_real_pg_consumed_binding_and_original(notification_case):
    from app.tax import filings
    from app.tax.workpaper import TaxWorkpaperService
    from app.uploads import UploadCreate, UploadIntentService
    from scripts.backup_accounting import original_rows

    c = notification_case
    tax = TaxWorkpaperService(c.db)
    filing = filings.record(
        tax,
        c.reviewer,
        {
            "book_id": c.book["id"],
            "period": "2026-10",
            "kind": "vat",
            "status": "filed",
            "external_ref": "synthetic-" + uuid4().hex,
            "occurred_at": "2026-11-03",
            "amount_cents": 0,
            "evidence_note": "本机虚构回执，不代表系统已申报",
        },
    )
    content = b"%PDF-1.4\nsynthetic receipt\n%%EOF"
    uploads = UploadIntentService(c.db, c.store)
    upload = uploads.create(
        c.reviewer,
        UploadCreate(
            purpose="tax_filing_attachment",
            filename="receipt.pdf",
            media_type="application/pdf",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        ),
    )
    uploads.store_content(c.reviewer, upload["id"], content)
    attached = filings.attach(uploads, tax, c.reviewer, filing["id"], upload["id"])
    assert (
        c.store.read(
            filings.attachment_for(tax, c.reviewer, filing["id"], attached["attachment_id"])
        )
        == content
    )
    with c.db.transaction(actor=c.reviewer) as conn:
        row = conn.execute(
            "SELECT state,binding_json FROM upload_intents WHERE id=?", (upload["id"],)
        ).fetchone()
        binding = json.loads(row["binding_json"])
        assert row["state"] == "consumed" and binding["target_id"] == filing["id"]
        assert binding["company_id"] == c.book["company_id"]
    # Use the privileged snapshot only for backup inventory, never business IO.
    with c.psycopg.connect(c.admin) as conn:
        _, originals = original_rows(conn, "postgres")
        assert any(
            any(
                ref["table"] == "tax_filing_attachments"
                and ref["links"]["filing_id"] == filing["id"]
                for ref in item["references"]
            )
            for item in originals
        )
