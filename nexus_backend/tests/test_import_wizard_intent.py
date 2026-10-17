"""银行流水导入的两段式大文件上传：用途绑定、原样保留与行数上限。"""

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError
from app.accounting.seed import LOCAL_ACTOR
from app.business.storage import AttachmentStore
from app.import_wizard.parser import MAX_ROWS, parse_file
from app.import_wizard.service import ImportWizardService
from app.import_wizard.router import build_import_wizard_router
from app.uploads import UploadCreate, UploadIntentService
from test_accounting import service, book, add_actor  # noqa: F401

HEADER = "交易编号,交易日期,摘要,交易对方,金额,方向,凭据引用"


def statement(rows=4000):
    lines = [HEADER]
    for index in range(rows):
        lines.append(
            f"L{index:06d},2026/10/01,月度银行流水第{index}笔"
            f"{'备注' * 120},虚构供应商,0.30,支出,回单{index}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def setup(service, tmp_path):
    store = AttachmentStore(tmp_path / "intent-originals")
    uploads = UploadIntentService(service.db, store)
    wizard = ImportWizardService(service.db, store, service, uploads=uploads)
    return store, uploads, wizard


def intent(uploads, data, purpose="bank_import", media_type="text/csv"):
    return uploads.create(
        LOCAL_ACTOR,
        UploadCreate(
            purpose=purpose,
            filename="月度流水.csv",
            media_type=media_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )


def test_large_statement_via_intent_is_parsed_and_consumed(service, book, tmp_path):
    _store, uploads, wizard = setup(service, tmp_path)
    data = statement()
    assert len(data) > 2 * 1024 * 1024
    created = intent(uploads, data)
    assert uploads.store_content(LOCAL_ACTOR, created["id"], data)["state"] == "stored"
    result = wizard.upload_intent(LOCAL_ACTOR, book, created["id"])
    assert result["row_count"] == 4000 and result["headers"][0] == "交易编号"
    original = wizard.original(LOCAL_ACTOR, book, result["file_id"])
    assert original["data"] == data
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        row = conn.execute(
            "SELECT storage_key FROM bank_import_files WHERE id=?", (result["file_id"],)
        ).fetchone()
        state = conn.execute(
            "SELECT state,consumed_ref FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()
    assert row["storage_key"] == f"{created['id'][:2]}/{created['id']}"
    assert tuple(state) == ("consumed", "bank_import")


def test_maximum_statement_size_records_every_row(service, book, tmp_path):
    _store, uploads, wizard = setup(service, tmp_path)
    data = statement(rows=MAX_ROWS)
    created = intent(uploads, data)
    uploads.store_content(LOCAL_ACTOR, created["id"], data)
    result = wizard.upload_intent(LOCAL_ACTOR, book, created["id"])
    assert result["row_count"] == MAX_ROWS


def test_bank_import_normalizes_legacy_csv_media_type(service, book, tmp_path):
    _store, uploads, wizard = setup(service, tmp_path)
    data = statement(rows=2)
    created = intent(uploads, data, media_type="application/vnd.ms-excel")
    uploads.store_content(LOCAL_ACTOR, created["id"], data)
    result = wizard.upload_intent(LOCAL_ACTOR, book, created["id"])
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        media_type = conn.execute(
            "SELECT media_type FROM bank_import_files WHERE id=?", (result["file_id"],)
        ).fetchone()["media_type"]
    assert media_type == "text/csv"


def test_bank_import_intent_requires_matching_purpose(service, book, tmp_path):
    _store, uploads, wizard = setup(service, tmp_path)
    data = statement(rows=2)
    created = intent(uploads, data, purpose="material_attachment")
    uploads.store_content(LOCAL_ACTOR, created["id"], data)
    with pytest.raises(AccountingError) as mismatch:
        wizard.upload_intent(LOCAL_ACTOR, book, created["id"])
    assert mismatch.value.status == 409
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()["state"]
    assert state == "stored"


def test_invalid_bank_import_content_is_rejected_at_upload(service, book, tmp_path):
    _store, uploads, _wizard = setup(service, tmp_path)
    data = HEADER.encode("utf-8")  # 仅表头、没有流水行
    created = intent(uploads, data)
    with pytest.raises(AccountingError, match="流水"):
        uploads.store_content(LOCAL_ACTOR, created["id"], data)
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        state = conn.execute(
            "SELECT state FROM upload_intents WHERE id=?", (created["id"],)
        ).fetchone()["state"]
    assert state == "pending"


def test_parser_boundary_is_five_thousand_rows():
    rows = [HEADER] + [f"L{i:06d},2026/10/01,摘要,对方,0.30,支出,回单" for i in range(MAX_ROWS)]
    table = parse_file("边界.csv", ("\n".join(rows) + "\n").encode("utf-8"))
    assert len(table["rows"]) == MAX_ROWS
    oversized = rows + ["X,2026/10/01,摘要,对方,0.30,支出,回单"]
    with pytest.raises(AccountingError, match="5,000"):
        parse_file("超量.csv", ("\n".join(oversized) + "\n").encode("utf-8"))


def test_http_upload_id_path_records_stored_intent(service, book, tmp_path):
    _store, uploads, wizard = setup(service, tmp_path)
    data = statement(rows=3)
    created = intent(uploads, data)
    uploads.store_content(LOCAL_ACTOR, created["id"], data)
    app = FastAPI()
    app.state.import_wizard = wizard
    app.include_router(build_import_wizard_router(lambda: LOCAL_ACTOR))
    client = TestClient(app)
    response = client.post(
        f"/api/books/{book}/import-wizard/files", data={"upload_id": created["id"]}
    )
    assert response.status_code == 201
    assert response.json()["row_count"] == 3
