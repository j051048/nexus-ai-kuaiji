"""NEXT05: external filing evidence is bound, appended and frozen, never rewritten."""

import hashlib
import json

import pytest

from app.business.storage import AttachmentStore
from app.delivery.monthly_package import (
    FILE_PATHS_V1,
    FILE_PATHS_V2,
    MonthlyPackageService,
    encoded,
)
from app.delivery.service import encode
from app.tax import filings
from app.tax.workpaper import TaxWorkpaperService
from app.uploads import UploadCreate, UploadIntentService
from test_business import business  # noqa: F401  (pytest fixture re-export)
from test_tax_workpaper import invoices, profile, row


def pdf(payload=b"filing-receipt"):
    return b"%PDF-1.4\n" + payload + b"\n%%EOF"


@pytest.fixture
def tax(business, tmp_path):
    business.tax = TaxWorkpaperService(business.db)
    business.actor_accountant = business.actors["accountant"]
    business.store = AttachmentStore(tmp_path / "filing-uploads")
    business.uploads = UploadIntentService(business.db, business.store)
    return business


def body(tax, **changes):
    payload = {
        "book_id": tax.book["id"],
        "period": "2026-10",
        "kind": "vat",
        "status": "filed",
        "external_ref": "SB2026100001",
        "occurred_at": "2026-11-10",
        "amount_cents": 3_000,
        "evidence_note": "电子税务局回执",
    }
    payload.update(changes)
    return payload


def reviewed(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    tax.tax.review(tax.manager, tax.book["id"], paper["id"], "账表差异已说明", 1)
    return paper


def upload(tax, actor=None, data=None):
    actor = actor or tax.manager
    data = data or pdf()
    created = tax.uploads.create(
        actor,
        UploadCreate(
            purpose="tax_filing_attachment",
            filename="完税证明.pdf",
            media_type="application/pdf",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )
    tax.uploads.store_content(actor, created["id"], data)
    return created, data


def test_registration_binds_original_hash_and_workpaper_version(tax):
    paper = reviewed(tax)
    filed = filings.record(tax.tax, tax.manager, body(tax))
    assert filed["workpaper_id"] == paper["id"]
    assert filed["workpaper_version"] == 1
    assert filed["workpaper_status"] == "reviewed"
    assert filed["amount_check"] == "match"
    assert filed["amount_matches_workpaper"] is True
    assert filed["attachment"] is None

    created, data = upload(tax)
    result = filings.attach(tax.uploads, tax.tax, tax.manager, filed["id"], created["id"])
    assert result["filing_id"] == filed["id"]
    row = filings.list_for(tax.tax, tax.manager, tax.book["id"])[0]
    assert row["attachment"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert row["attachment"]["size_bytes"] == len(data)
    metadata = filings.attachment_for(tax.tax, tax.manager, filed["id"], row["attachment"]["id"])
    assert tax.store.read(metadata) == data

    # One receipt is one immutable original; further evidence needs a correction row.
    created2, _ = upload(tax, data=pdf(b"another"))
    with pytest.raises(Exception) as second:
        filings.attach(tax.uploads, tax.tax, tax.manager, filed["id"], created2["id"])
    assert getattr(second.value, "status", 0) == 409


def test_duplicate_receipt_and_foreign_company_download_are_rejected(tax):
    reviewed(tax)
    filed = filings.record(tax.tax, tax.manager, body(tax))
    with pytest.raises(Exception) as duplicate:
        filings.record(tax.tax, tax.manager, body(tax))
    assert getattr(duplicate.value, "status", 0) == 409

    created, _ = upload(tax)
    filings.attach(tax.uploads, tax.tax, tax.manager, filed["id"], created["id"])
    attachment = filings.list_for(tax.tax, tax.manager, tax.book["id"])[0]["attachment"]
    with pytest.raises(Exception) as foreign:
        filings.attachment_for(tax.tax, tax.actors["employee"], filed["id"], attachment["id"])
    assert getattr(foreign.value, "status", 0) in (403, 404)
    with pytest.raises(Exception) as foreign_list:
        filings.list_for(tax.tax, tax.actors["employee"], tax.other_book["id"])
    assert getattr(foreign_list.value, "status", 0) in (403, 404)


def test_correction_and_void_append_history_and_keep_old_rows_frozen(tax):
    reviewed(tax)
    filed = filings.record(tax.tax, tax.manager, body(tax))
    corrected = filings.record(
        tax.tax,
        tax.manager,
        body(
            tax,
            external_ref="SB2026100001-C",
            amount_cents=3_100,
            entry_kind="correction",
            supersedes_id=filed["id"],
            correction_reason="税局更正通知书要求调整 100 分",
        ),
    )
    assert corrected["entry_kind"] == "correction"
    assert corrected["supersedes_id"] == filed["id"]
    assert corrected["amount_check"] == "mismatch"
    assert corrected["current"] is True
    rows = filings.list_for(tax.tax, tax.manager, tax.book["id"])
    assert [item["id"] for item in rows] == [corrected["id"], filed["id"]]
    assert rows[0]["current"] is True and rows[1]["current"] is False
    assert filings.summary(rows)["filing_state"] == "filed"

    created, _ = upload(tax)
    with pytest.raises(Exception) as stale:
        filings.attach(tax.uploads, tax.tax, tax.manager, filed["id"], created["id"])
    assert getattr(stale.value, "status", 0) == 409

    voided = filings.record(
        tax.tax,
        tax.manager,
        body(
            tax,
            external_ref="SB2026100001-V",
            entry_kind="void",
            supersedes_id=corrected["id"],
            correction_reason="重复登记，作废该回执",
        ),
    )
    assert voided["entry_kind"] == "void"
    rows = filings.list_for(tax.tax, tax.manager, tax.book["id"])
    assert [item["current"] for item in rows] == [False, False, False]
    assert filings.summary(rows)["filing_state"] == "not_filed"
    with pytest.raises(Exception) as again:
        filings.record(
            tax.tax,
            tax.manager,
            body(
                tax,
                external_ref="SB2026100001-V2",
                entry_kind="correction",
                supersedes_id=voided["id"],
                correction_reason="不能复活已作废记录",
            ),
        )
    assert getattr(again.value, "status", 0) == 409


def test_a_new_workpaper_version_changes_the_bound_evidence(tax):
    reviewed(tax)
    first = filings.record(tax.tax, tax.manager, body(tax))
    second_paper = tax.tax.generate(
        tax.actor_accountant, tax.book["id"], "2026-10", "新增凭证后重算"
    )
    assert second_paper["version"] == 2
    second = filings.record(
        tax.tax,
        tax.manager,
        body(tax, external_ref="SB2026100002", amount_cents=3_000),
    )
    assert second["workpaper_version"] == 2
    assert first["workpaper_version"] == 1
    rows = filings.list_for(tax.tax, tax.manager, tax.book["id"])
    assert {item["workpaper_version"] for item in rows} == {1, 2}


def test_non_reviewers_cannot_register_or_read_foreign_original(tax):
    reviewed(tax)
    for actor in (tax.actors["finance"], tax.actors["employee"]):
        with pytest.raises(Exception) as denied:
            filings.record(tax.tax, actor, body(tax))
        assert getattr(denied.value, "status", 0) in (403, 404)
    filed = filings.record(tax.tax, tax.manager, body(tax))
    created, _ = upload(tax)
    filings.attach(tax.uploads, tax.tax, tax.manager, filed["id"], created["id"])
    attachment = filings.list_for(tax.tax, tax.manager, tax.book["id"])[0]["attachment"]
    with pytest.raises(Exception) as denied_read:
        filings.attachment_for(tax.tax, tax.actors["employee"], filed["id"], attachment["id"])
    assert getattr(denied_read.value, "status", 0) in (403, 404)


def _snapshot_row(files, schema_version):
    manifest = {
        "schema_version": schema_version,
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
                "size_bytes": len(data.encode("utf-8")),
            }
            for path, data in sorted(files.items())
        ],
    }
    manifest_json = encode(manifest)
    return {
        "snapshot_json": encoded(
            {"manifest": manifest, "manifest_json": manifest_json, "files": files}
        ),
        "manifest_sha256": hashlib.sha256(manifest_json.encode("utf-8")).hexdigest(),
    }


def test_v1_packages_stay_readable_and_v2_freezes_the_tax_evidence_file():
    v1_files = {path: json.dumps({"path": path}) for path in FILE_PATHS_V1}
    assert MonthlyPackageService._snapshot(_snapshot_row(v1_files, 1))["manifest"]["schema_version"] == 1

    v2_files = dict(v1_files)
    v2_files["tax_filings.json"] = json.dumps(
        {"current": [{"id": "fil_1", "workpaper_version": 2}], "history": []}
    )
    snapshot = MonthlyPackageService._snapshot(_snapshot_row(v2_files, 2))
    assert "tax_filings.json" in snapshot["files"]
    assert set(v2_files) == set(FILE_PATHS_V2)

    broken = dict(v2_files)
    broken.pop("tax_filings.json")
    with pytest.raises(Exception) as mismatch:
        MonthlyPackageService._snapshot(_snapshot_row(broken, 2))
    assert getattr(mismatch.value, "status", 0) == 409
