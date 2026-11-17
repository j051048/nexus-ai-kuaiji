"""OPT06-D: voucher numbering gaps are surfaced to close precheck."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from app.accounting.domain import AccountingError
from app.observability.closing import ClosingRadarService
from app.posting.integrity import VoucherGapService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from review_policy_fixtures import post_reviewed_manual
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


def posted_manual(case):
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="OPT06-D 期初绑定",
        ),
    )
    return post_reviewed_manual(
        posting,
        case.manager,
        ManualVoucher(
            command_key="opt06d-manual-001",
            book_id=case.book["id"],
            voucher_date="2026-10-16",
            description="OPT06-D 编号基线",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="100.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="100.00",
                ),
            ],
        ),
    )


def insert_gap_voucher(case, number: int):
    # The production triggers reject any direct voucher insert. This disposable
    # fixture DB removes only the two insert guards so the detection path can be
    # exercised against the real table; no service or document guard is altered.
    raw = sqlite3.connect(case.db.path)
    raw.execute("DROP TRIGGER IF EXISTS require_firm_authority_voucher")
    raw.execute("DROP TRIGGER IF EXISTS rr_voucher_insert")
    raw.commit()
    raw.close()
    with case.db.transaction(write=True, actor=case.manager) as conn:
        source = conn.execute(
            "SELECT * FROM vouchers WHERE book_id=? ORDER BY voucher_no", (case.book["id"],)
        ).fetchone()
        assert source is not None
        values = dict(source)
        values["id"] = "gap-" + uuid4().hex
        values["voucher_no"] = number
        values["command_id"] = "gap-" + uuid4().hex
        values["content_fingerprint"] = "gap-" + uuid4().hex
        values["review_mode"] = "independent"
        values["posted_by"] = case.manager.user_id
        values["review_request_id"] = None
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO vouchers({columns}) VALUES({placeholders})", tuple(values.values())
        )


def test_voucher_gap_is_reported_and_blocks_close_precheck(business):
    posted_manual(business)
    service = VoucherGapService(business.db)
    clean = service.scan(business.manager, business.book["id"])
    assert clean["contiguous"] is True
    assert clean["missing"] == []

    insert_gap_voucher(business, 3)
    gap = service.scan(business.manager, business.book["id"])
    assert gap["contiguous"] is False
    assert gap["missing"] == [{"voucher_type": "记", "voucher_no": 2}]

    radar = ClosingRadarService(business.db).radar(business.manager, "2026-10")
    item = next(row for row in radar["books"] if row["book_id"] == business.book["id"])
    blocker = next(row for row in item["blockers"] if row["code"] == "voucher_number_gap")
    assert blocker["count"] == 1
    assert blocker["voucher_numbers"] == [{"voucher_type": "记", "voucher_no": 2}]
    assert item["precheck"]["state"] == "blocked"


def test_voucher_gap_requires_book_access(business):
    service = VoucherGapService(business.db)
    try:
        service.scan(business.manager, "missing-book")
    except AccountingError as error:
        assert error.status == 404
    else:
        raise AssertionError("missing book must be rejected")
