"""大文件两段式上传：意图生命周期、内容校验与私有存储签名。"""

import base64
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.business.storage import AttachmentStore
from app.storage.settings import StorageSettings
from app.storage.supabase import SupabaseBlobBackend
from app.uploads import UploadCreate, UploadIntentService
from test_business import business, create


def pdf(payload=b"x" * 4096):
    return b"%PDF-1.4\n" + payload + b"\n%%EOF"


@pytest.fixture
def uploads(business, tmp_path):
    c = business
    store = AttachmentStore(tmp_path / "uploads")
    service = UploadIntentService(c.db, store)
    c.store, c.uploads = store, service
    return c


def intent(c, actor, data=None, **changes):
    data = data if data is not None else pdf()
    payload = {
        "purpose": "event_attachment",
        "filename": "发票.pdf",
        "media_type": "application/pdf",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        **changes,
    }
    return data, c.uploads.create(actor, UploadCreate(**payload))


def storage_key(c, actor, upload_id):
    with c.db.transaction(actor=actor) as conn:
        return conn.execute(
            "SELECT storage_key FROM upload_intents WHERE id=?", (upload_id,)
        ).fetchone()["storage_key"]


def test_local_two_phase_upload_feeds_event_attachment(uploads):
    c = uploads
    actor = c.actors["employee"]
    event = create(c, actor="employee")
    data, created = intent(c, actor)
    assert created["method"] == "PUT" and created["url"].startswith("/api/uploads/")
    assert c.uploads.store_content(actor, created["id"], data)["state"] == "stored"
    metadata = c.uploads.peek(actor, created["id"], "event_attachment")
    assert metadata["id"] == created["id"] and metadata["size_bytes"] == len(data)
    result = c.uploads.consume(actor, created["id"], "event_attachment", event["id"],
        lambda conn, stored: c.service.add_attachment(actor, event["id"], event["version"], stored, conn=conn))
    assert any(item["id"] == created["id"] for item in result["attachments"])
    with c.db.transaction(actor=actor) as conn:
        row = conn.execute(
            "SELECT state,consumed_ref FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()
    assert row["state"] == "consumed" and row["consumed_ref"] == f"business_attachments:{created['id']}"
    with pytest.raises(AccountingError) as reused:
        c.uploads.take(actor, created["id"], "event_attachment")
    assert reused.value.status == 409


def test_slow_object_put_does_not_hold_database_write_lock(uploads):
    c = uploads
    actor = c.actors["employee"]
    data_a, first = intent(c, actor, filename="慢对象甲.pdf")
    data_b, second = intent(c, actor, filename="慢对象乙.pdf")
    started, release = threading.Event(), threading.Event()
    original_put = c.store.backend.put

    def slow_put(key, data, media_type=""):
        if key.endswith(first["id"]):
            started.set()
            assert release.wait(2)
        return original_put(key, data, media_type)

    c.store.backend.put = slow_put
    first_result = {}

    def store_a():
        first_result["value"] = c.uploads.store_content(actor, first["id"], data_a)

    thread = threading.Thread(target=store_a)
    thread.start()
    assert started.wait(2)
    # While A is still in slow object IO, B must complete without waiting on A's
    # database write lock.
    assert c.uploads.store_content(actor, second["id"], data_b)["state"] == "stored"
    release.set()
    thread.join(3)
    assert first_result["value"]["state"] == "stored"


def test_hash_mismatch_blocks_local_store_and_allows_retry(uploads):
    c = uploads
    actor = c.actors["employee"]
    data, created = intent(c, actor)
    with pytest.raises(AccountingError) as mismatch:
        c.uploads.store_content(actor, created["id"], pdf(b"other"))
    assert mismatch.value.status == 409
    key = storage_key(c, actor, created["id"])
    with c.db.transaction(actor=actor) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()["state"]
    assert state == "pending" and not (c.store.root / key).exists()
    assert c.uploads.store_content(actor, created["id"], data)["state"] == "stored"


def test_complete_verifies_signed_object_and_discards_mismatch(uploads):
    c = uploads
    actor = c.actors["employee"]
    data, created = intent(c, actor)
    key = storage_key(c, actor, created["id"])
    c.store.backend.put(key, data, "application/pdf")
    assert c.uploads.complete(actor, created["id"])["state"] == "stored"

    tampered, second = intent(c, actor, filename="发票二.pdf")
    key2 = storage_key(c, actor, second["id"])
    c.store.backend.put(key2, pdf(b"tampered"), "application/pdf")
    with pytest.raises(AccountingError) as mismatch:
        c.uploads.complete(actor, second["id"])
    assert mismatch.value.status == 409
    with c.db.transaction(actor=actor) as conn:
        assert (
            conn.execute("SELECT state FROM upload_intents WHERE id=?", (second["id"],)).fetchone()[
                "state"
            ]
            == "discarded"
        )
    assert not (c.store.root / key2).exists()

    garbage = b"<html>not a pdf</html>"
    _, third = intent(c, actor, data=garbage, filename="发票三.pdf")
    key3 = storage_key(c, actor, third["id"])
    c.store.backend.put(key3, garbage, "application/pdf")
    with pytest.raises(AccountingError) as invalid:
        c.uploads.complete(actor, third["id"])
    assert invalid.value.status == 422
    assert not (c.store.root / key3).exists()

    broken_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lK3Q6wAAAABJRU5ErkJggg=="
    )
    _, fourth = intent(c, actor, data=broken_png, filename="损坏回执.png")
    key4 = storage_key(c, actor, fourth["id"])
    c.store.backend.put(key4, broken_png, "image/png")
    with pytest.raises(AccountingError) as broken:
        c.uploads.complete(actor, fourth["id"])
    assert broken.value.status == 422
    assert not (c.store.root / key4).exists()


def test_intent_ownership_purpose_and_expiry(uploads):
    c = uploads
    employee, peer = c.actors["employee"], c.actors["peer"]
    data, created = intent(c, employee)
    with pytest.raises(AccountingError) as foreign:
        c.uploads.take(peer, created["id"], "event_attachment")
    assert foreign.value.status == 404
    c.uploads.store_content(employee, created["id"], data)
    with pytest.raises(AccountingError) as wrong_purpose:
        c.uploads.take(employee, created["id"], "case_attachment")
    assert wrong_purpose.value.status == 409
    with c.db.transaction(write=True, actor=employee) as conn:
        conn.execute(
            "UPDATE upload_intents SET expires_at=? WHERE id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                created["id"],
            ),
        )
    with pytest.raises(AccountingError) as expired:
        c.uploads.take(employee, created["id"], "event_attachment")
    assert expired.value.status == 410
    fresh_data, fresh = intent(c, employee, filename="过期发票.pdf")
    with c.db.transaction(write=True, actor=employee) as conn:
        conn.execute(
            "UPDATE upload_intents SET expires_at=? WHERE id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                fresh["id"],
            ),
        )
    with pytest.raises(AccountingError) as expired_store:
        c.uploads.store_content(employee, fresh["id"], fresh_data)
    assert expired_store.value.status == 410


def test_release_returns_claimed_intent_to_stored(uploads):
    c = uploads
    actor = c.actors["employee"]
    data, created = intent(c, actor)
    c.uploads.store_content(actor, created["id"], data)
    c.uploads.take(actor, created["id"], "event_attachment")
    c.uploads.release(actor, created["id"])
    metadata = c.uploads.take(actor, created["id"], "event_attachment")
    assert metadata["sha256"] == hashlib.sha256(data).hexdigest()
    c.uploads.release(actor, created["id"])


def test_material_intent_uses_material_validator_and_normalizes_filename(uploads):
    c = uploads
    actor = c.actors["employee"]
    data = b"date,amount\n2026-10-01,10.00\n"
    _, created = intent(
        c,
        actor,
        data=data,
        purpose="material_attachment",
        filename="../../月度流水.csv",
        media_type="text/csv",
    )
    with c.db.transaction(actor=actor) as conn:
        assert (
            conn.execute(
                "SELECT filename FROM upload_intents WHERE id=?", (created["id"],)
            ).fetchone()["filename"]
            == "月度流水.csv"
        )
    assert c.uploads.store_content(actor, created["id"], data)["state"] == "stored"
    with pytest.raises(ValidationError):
        UploadCreate(
            purpose="event_attachment",
            filename="发票.pdf",
            media_type="application/pdf",
            size_bytes=0,
            sha256="a" * 64,
        )
    with pytest.raises(ValidationError):
        UploadCreate(
            purpose="event_attachment",
            filename="发票.pdf",
            media_type="application/pdf",
            size_bytes=1024,
            sha256="not-a-hash",
        )


class _FakeStream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


class _FakeResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code, self._body = status_code, body
        self.headers = headers or {}

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, responses):
        self._responses, self.calls = list(responses), []

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return _FakeStream(self._responses.pop(0))


def supabase_backend(responses):
    settings = StorageSettings(
        backend="supabase",
        supabase_url="https://example.supabase.co",
        bucket="private-originals",
        server_key="sb_secret_test-key",
        timeout_seconds=5,
    )
    client = _FakeClient(responses)
    return SupabaseBlobBackend(settings, client=client), client


def bucket_ok():
    return _FakeResponse(200, json.dumps({"id": "private-originals", "public": False}).encode())


def test_supabase_signed_upload_url_is_bounded_and_project_scoped():
    key = "ab/" + "a" * 32
    backend, client = supabase_backend(
        [
            bucket_ok(),
            _FakeResponse(
                200,
                json.dumps(
                    {"url": "/object/upload/sign/private-originals/" + key + "?token=signed"}
                ).encode(),
            ),
        ]
    )
    signed = backend.sign_upload(key, 900)
    assert signed["url"] == (
        "https://example.supabase.co/storage/v1/object/upload/sign/private-originals/"
        + key
        + "?token=signed"
    )
    assert client.calls[1]["method"] == "POST"
    assert client.calls[1]["json"] == {"expiresIn": 900}
    backend, _ = supabase_backend(
        [bucket_ok(), _FakeResponse(200, b'{"url":"https://evil.example/upload"}')]
    )
    with pytest.raises(AccountingError):
        backend.sign_upload(key, 900)
