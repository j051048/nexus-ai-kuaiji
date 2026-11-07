"""No model: analysis publication, page binding and strict request contracts."""

import pytest
from app.assistant import analysis
from app.accounting.domain import AccountingError
from test_imp08_analysis import posted
from test_business import business


def test_default_comparison_does_not_jump_over_missing_month(posted):
    from app.ledgers.service import LedgersService
    from app.ledgers.models import PeriodCreate

    c = posted.case
    next_book = LedgersService(c.db).create_period(
        c.manager,
        c.book["id"],
        PeriodCreate(
            period="2026-12",
            name="跨缺失期间",
            source_book_id=c.book["id"],
            idempotency_key="imp-gap-period",
        ),
    )["book_id"]
    book = analysis._book(c.db, c.manager, next_book, "2026-12")
    assert analysis._sibling_book(c.db, c.manager, book) is None
    result = analysis.analyze(
        c.db, c.manager, next_book, "2026-12", "expense_bridge", account_code="6602.02"
    )
    assert result["facts"]["previous_period"] is None
    assert result["facts"]["difference"] is None and result["status"] == "partial"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"account_code": "6602.02", "offset": True},
        {"account_code": "6602.02", "limit": True},
        {"account_code": "6602.02", "offset": 0.5},
    ],
)
def test_request_parameters_fail_before_reads(posted, params):
    c = posted.case
    with pytest.raises(AccountingError) as error:
        analysis.analyze(c.db, c.manager, c.book["id"], "2026-10", "expense_bridge", **params)
    assert error.value.status == 422


def test_page_source_version_changes_are_rejected(posted):
    c = posted.case
    call = lambda **kw: analysis.analyze(
        c.db,
        c.manager,
        c.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
        limit=1,
        **kw,
    )
    first = call()
    assert first.get("source_version")
    with c.db.transaction() as conn:
        conn.execute(
            "UPDATE voucher_sources SET amount_cents=amount_cents+1 WHERE id=?", ("imp08-src-2",)
        )
    with pytest.raises(AccountingError) as error:
        call(offset=1, expected_source_version=first["source_version"])
    assert error.value.status == 409


def test_claim_requires_full_scope_version_and_exact_value(posted):
    c = posted.case
    result = analysis.analyze(
        c.db,
        c.manager,
        c.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
    )
    claim = {key: result[key] for key in ("company_id", "book_id", "period", "fingerprint")}
    claim.update(
        fact_ids=["invoice_voucher_diff:difference_cents"], amount_cents=1000, currency="CNY"
    )
    assert analysis.validate_claim(result, claim)
    for key in ("company_id", "book_id", "period", "fingerprint"):
        with pytest.raises(AccountingError):
            analysis.validate_claim(result, {**claim, key: "foreign"})
