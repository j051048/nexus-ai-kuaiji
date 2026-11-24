"""PLT04-D: a non-import source has one immutable, scoped original."""

import hashlib
import os
import time
from uuid import uuid4

import pytest

from app.auth.supabase import Identity
from app.business.storage import AttachmentStore
from app.main import create_app
from app.tax.sources import TaxSourceAdapter
from app.uploads import UploadCreate, UploadIntentService
from test_business import business  # noqa: F401  (pytest fixture re-export)
from test_business_api import local_client
from test_p02_formal_read_postgres import formal_case  # noqa: F401
from test_n03_notification_postgres import p0_case, p0_pg  # noqa: F401


def pdf(payload=b"source-original"):
    return b"%PDF-1.4\n" + payload + b"\n%%EOF"


@pytest.fixture
def source_original(business, tmp_path):
    business.store = AttachmentStore(tmp_path / "source-originals")
    business.uploads = UploadIntentService(business.db, business.store)
    source = TaxSourceAdapter(business.db)
    source_id = "src-original-plt04"
    with business.db.transaction(actor=business.manager) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
            "counterparty,amount_cents,direction,evidence_ref,fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                business.book["id"],
                "PLT04-ORIGINAL",
                "平台结算单",
                "2026-10-31",
                "平台结算单，差额待核对",
                "虚构平台",
                10_000_000,
                "income",
                "原有文字引用",
                "a" * 64,
            ),
        )
    business.source = source
    business.source_id = source_id
    return business


def upload(source_original, data=None):
    data = data or pdf()
    created = source_original.uploads.create(
        source_original.manager,
        UploadCreate(
            purpose="source_original",
            filename="平台结算单.pdf",
            media_type="application/pdf",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )
    source_original.uploads.store_content(source_original.manager, created["id"], data)
    return created, data


def test_source_original_binds_exact_bytes_and_is_scoped(source_original):
    created, data = upload(source_original)
    result = source_original.source.attach_original(
        source_original.uploads,
        source_original.manager,
        source_original.book["id"],
        source_original.source_id,
        created["id"],
    )
    assert result["source_id"] == source_original.source_id

    metadata = source_original.source.original_for(
        source_original.manager,
        source_original.book["id"],
        source_original.source_id,
        result["attachment_id"],
    )
    assert metadata["sha256"] == hashlib.sha256(data).hexdigest()
    assert metadata["size_bytes"] == len(data)
    assert source_original.store.read(metadata) == data

    with source_original.db.transaction(actor=source_original.manager) as conn:
        binding = conn.execute(
            "SELECT binding_json FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()
    assert '"purpose":"source_original"' in binding["binding_json"]
    assert f'"source_id":"{source_original.source_id}"' in binding["binding_json"]

    # One source has one immutable original; a replacement needs a new source version.
    again = upload(source_original, b"%PDF-1.4\nsecond\n%%EOF")
    with pytest.raises(Exception) as error:
        source_original.source.attach_original(
            source_original.uploads,
            source_original.manager,
            source_original.book["id"],
            source_original.source_id,
            again[0]["id"],
        )
    assert getattr(error.value, "status", None) == 409


def test_source_original_rejects_non_reviewer_and_other_book(source_original):
    created, _ = upload(source_original)
    with pytest.raises(Exception) as denied:
        source_original.source.attach_original(
            source_original.uploads,
            source_original.actors["employee"],
            source_original.book["id"],
            source_original.source_id,
            created["id"],
        )
    assert getattr(denied.value, "status", None) == 404

    with pytest.raises(Exception) as missing:
        source_original.source.attach_original(
            source_original.uploads,
            source_original.manager,
            source_original.other_book["id"],
            source_original.source_id,
            created["id"],
        )
    assert getattr(missing.value, "status", None) == 404


def test_source_original_http_upload_and_download(source_original):
    case = source_original
    data = pdf(b"http-source")
    app = create_app(case.db.path, seed=False)
    token = "plt04d-source-original"
    with local_client(app, 45714) as client:
        app.state.tokens[token] = time.monotonic() + 60
        app.state.token_users[token] = Identity(
            case.manager.user_id, "manager@example.test", "PLT04 经理"
        )
        headers = {
            "Authorization": "Bearer " + token,
            "X-Firm-Id": case.manager.firm_id,
            "X-Tenant-Id": "test-tenant",
        }
        created = client.post(
            "/api/uploads",
            headers=headers,
            json={
                "purpose": "source_original",
                "filename": "平台结算单.pdf",
                "media_type": "application/pdf",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        assert created.status_code == 201, created.text
        upload_id = created.json()["id"]
        stored = client.put(
            f"/api/uploads/{upload_id}/content",
            headers={**headers, "Content-Type": "application/pdf"},
            content=data,
        )
        assert stored.status_code == 200, stored.text
        completed = client.post(f"/api/uploads/{upload_id}/complete", headers=headers)
        assert completed.status_code == 200, completed.text

        attached = client.post(
            f"/api/tax/books/{case.book['id']}/sources/{case.source_id}/originals",
            headers=headers,
            json={"upload_id": upload_id},
        )
        assert attached.status_code == 201, attached.text
        attachment_id = attached.json()["attachment_id"]
        downloaded = client.get(
            f"/api/tax/books/{case.book['id']}/sources/{case.source_id}/originals/{attachment_id}",
            headers=headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == data
        assert downloaded.headers["cache-control"] == "no-store"
        assert downloaded.headers["x-content-type-options"] == "nosniff"

        missing = client.get(
            f"/api/tax/books/{case.book['id']}/sources/not-this-source/originals/{attachment_id}",
            headers=headers,
        )
        assert missing.status_code == 404


@pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)
def test_pg_source_original_round_trip(formal_case, tmp_path):
    """The PG 298 table/view/function binding works on the real dispatcher."""
    case = formal_case
    case.store = AttachmentStore(tmp_path / "pg-source-originals")
    case.uploads = UploadIntentService(case.db, case.store)
    source_id = f"src-original-pg-{uuid4().hex[:10]}"
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
            "counterparty,amount_cents,direction,evidence_ref,fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                case.book["id"],
                "PLT04-PG-ORIGINAL",
                "平台结算单",
                "2026-10-31",
                "平台结算单，差额待核对",
                "虚构平台",
                10_000_000,
                "income",
                "原有文字引用",
                "b" * 64,
            ),
        )
    data = pdf(b"pg-source")
    created = case.uploads.create(
        case.manager,
        UploadCreate(
            purpose="source_original",
            filename="平台结算单.pdf",
            media_type="application/pdf",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )
    case.uploads.store_content(case.manager, created["id"], data)
    source = TaxSourceAdapter(case.db)
    result = source.attach_original(
        case.uploads, case.manager, case.book["id"], source_id, created["id"]
    )
    metadata = source.original_for(
        case.manager, case.book["id"], source_id, result["attachment_id"]
    )
    assert metadata["sha256"] == hashlib.sha256(data).hexdigest()
    assert case.store.read(metadata) == data
    with case.db.transaction(actor=case.manager) as conn:
        binding = conn.execute(
            "SELECT binding_json FROM accounting.upload_intents WHERE id=?",
            (created["id"],),
        ).fetchone()
        assert '"purpose":"source_original"' in binding["binding_json"]
        assert f'"source_id":"{source_id}"' in binding["binding_json"]
