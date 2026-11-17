"""FSC04 shared import envelopes: evidence, versions and human confirmation."""

import hashlib
import sqlite3

import pytest

from app.accounting.domain import Actor, AccountingError
from app.fsc.imports import FscImportService, ImportEnvelopeWrite
from test_business import business  # noqa: F401


def envelope_payload(**overrides):
    rows = [
        {"external_id": "R1", "date": "2026-10-02", "amount_cents": 120000,
         "quantity_milli": 2500, "unit": "CNY", "currency": "CNY"},
        {"external_id": "R2", "date": "2026-10-03", "amount_cents": 30000,
         "quantity_milli": 500, "unit": "CNY", "currency": "CNY",
         "status": "excluded", "reason": "客户确认不属于本期"},
        {"external_id": "R3", "date": "2026-10-04", "amount_cents": 5000,
         "quantity_milli": 100, "unit": "CNY", "currency": "CNY",
         "status": "needs_review", "reason": "待客户补充合同"},
    ]
    data = {
        "source_system": "advertising_project",
        "source_object": "project_revenue",
        "external_id": "BATCH-001",
        "source_version": 1,
        "filename": "广告项目收入.xlsx",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "sha256": hashlib.sha256(b"fixture").hexdigest(),
        "size_bytes": 2048,
        "mapping_version": "map-v1",
        "rule_version": "rule-v1",
        "unit": "CNY",
        "currency": "CNY",
        "rows": rows,
    }
    data.update(overrides)
    return ImportEnvelopeWrite(**data)


def test_import_envelope_versions_receipts_and_confirmation(business):
    case = business
    service = FscImportService(case.db)
    book_id = case.book["id"]
    first = service.import_envelope(case.manager, book_id, envelope_payload())
    assert first["status"] == "draft"
    assert first["row_count"] == 3 and first["adopted_count"] == 1
    assert first["total_cents"] == 120000

    replay = service.import_envelope(case.manager, book_id, envelope_payload())
    assert replay["id"] == first["id"] and replay["replayed"] is True

    with pytest.raises(AccountingError) as changed:
        service.import_envelope(case.manager, book_id, envelope_payload(sha256=hashlib.sha256(b"changed").hexdigest()))
    assert changed.value.status == 409

    with pytest.raises(AccountingError) as pending:
        service.confirm(case.manager, first["id"], 1)
    assert pending.value.status == 409

    second = service.import_envelope(
        case.manager, book_id,
        envelope_payload(source_version=2, rows=[{
            "external_id": "R1", "date": "2026-10-02", "amount_cents": 120000,
            "quantity_milli": 2500, "unit": "CNY", "currency": "CNY",
        }]),
    )
    assert second["source_version"] == 2
    assert service.detail(case.manager, first["id"])["status"] == "superseded"

    confirmed = service.confirm(case.manager, second["id"], 2)
    assert confirmed["status"] == "confirmed"
    with pytest.raises(AccountingError) as immutable:
        service.confirm(case.manager, second["id"], 2)
    assert immutable.value.status == 409
    with sqlite3.connect(case.db.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE fsc_import_envelopes SET rows_json='[]' WHERE id=?", (second["id"],))

    receipt = service.row_receipt(case.manager, second["id"], 1)
    assert receipt["row"]["external_id"] == "R1"
    assert receipt["sha256"] == second["sha256"]
    assert len(receipt["receipt_fingerprint"]) == 64
    assert receipt == service.row_receipt(case.manager, second["id"], 1)


def test_import_envelope_rejects_cross_period_duplicate_and_cross_firm(business):
    case = business
    service = FscImportService(case.db)
    book_id = case.book["id"]
    with pytest.raises(AccountingError) as period:
        service.import_envelope(case.manager, book_id, envelope_payload(rows=[{
            "external_id": "R1", "date": "2026-09-30", "amount_cents": 1,
            "quantity_milli": 0, "unit": "CNY", "currency": "CNY",
        }]))
    assert period.value.status == 422

    rows = list(envelope_payload().rows)
    duplicate = rows + [rows[0]]
    with pytest.raises(AccountingError) as repeated:
        service.import_envelope(case.manager, book_id, envelope_payload(rows=duplicate))
    assert repeated.value.status == 422

    stranger = Actor(case.manager.user_id, "other-firm")
    with pytest.raises(AccountingError) as cross:
        service.list(stranger, book_id)
    assert cross.value.status in {403, 404}

    service.import_envelope(case.manager, book_id, envelope_payload(external_id="RECENT-1"))
    recent = service.recent(case.manager)["items"]
    assert any(item["external_id"] == "RECENT-1" for item in recent)
    with pytest.raises(AccountingError) as employee:
        service.recent(case.actors["employee"])
    assert employee.value.status == 403

    invalid_json = envelope_payload(rows=[{
        "external_id": "R1", "date": "2026-10-02", "amount_cents": 1,
        "quantity_milli": 0, "unit": "CNY", "currency": "CNY", "raw": {"sql": "select 1"},
    }])
    with pytest.raises(AccountingError) as forbidden:
        service.import_envelope(case.manager, book_id, invalid_json)
    assert forbidden.value.status == 422
