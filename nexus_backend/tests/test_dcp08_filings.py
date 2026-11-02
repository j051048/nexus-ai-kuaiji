"""DCP08-B: external filing records are human evidence, append-only."""

import pytest

from app.tax import filings
from app.tax.readiness import filing_readiness
from app.tax.workpaper import TaxWorkpaperService
from test_business import business  # noqa: F401  (pytest fixture re-export)
from test_tax_workpaper import invoices, profile, row


@pytest.fixture
def tax(business):
    business.tax = TaxWorkpaperService(business.db)
    business.actor_accountant = business.actors["accountant"]
    return business


def payload(tax, **changes):
    body = {
        "book_id": tax.book["id"],
        "period": "2026-10",
        "kind": "vat",
        "status": "filed",
        "external_ref": "SB2026100001",
        "occurred_at": "2026-11-10",
        "amount_cents": 0,
        "evidence_note": "电子税务局回执截图已归档",
    }
    body.update(changes)
    return body


def reviewed(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    tax.tax.review(tax.manager, tax.book["id"], paper["id"], "账表差异已说明", 1)


def test_customer_finance_cannot_register_an_external_result(tax):
    with pytest.raises(Exception) as error:
        filings.record(tax.tax, tax.actors["finance"], payload(tax))
    assert getattr(error.value, "status", 0) in (403, 404)
    # Reading stays allowed: the client sees what was registered for them.
    assert filings.list_for(tax.tax, tax.actors["finance"], tax.book["id"]) == []


def test_filing_and_payment_are_appended_and_never_rewritten(tax):
    reviewed(tax)
    filed = filings.record(tax.tax, tax.manager, payload(tax))
    assert filed["status"] == "filed"
    assert filed["status_label"] == "已申报"

    with pytest.raises(Exception) as duplicate:
        filings.record(tax.tax, tax.manager, payload(tax))
    assert getattr(duplicate.value, "status", 0) == 409

    paid = filings.record(
        tax.tax,
        tax.manager,
        payload(tax, status="paid", external_ref="SB2026100001-P", amount_cents=3_000),
    )
    assert paid["amount_cents"] == 3_000
    rows = filings.list_for(tax.tax, tax.manager, tax.book["id"])
    assert [item["status"] for item in rows] == ["paid", "filed"]

    ready = filing_readiness(tax.tax, tax.manager, tax.book["id"], "2026-10")
    assert ready["external_result"] == "registered"
    assert ready["filing_state"] == "paid"
    kinds = {item["kind"]: item for item in ready["kinds"]}
    assert kinds["vat"]["filing"]["status"] == "paid"
    assert kinds["surtax"]["external_result"] == "none"
    assert len(ready["filings"]) == 2


def test_registration_validates_tax_kind_status_and_reference(tax):
    for bad in (
        payload(tax, kind="cit"),
        payload(tax, status="approved"),
        payload(tax, external_ref="x"),
        payload(tax, occurred_at="2026/11/10"),
        payload(tax, amount_cents=-1),
    ):
        with pytest.raises(Exception) as error:
            filings.record(tax.tax, tax.manager, bad)
        assert getattr(error.value, "status", 0) == 422


def test_an_employee_without_book_access_cannot_register(tax):
    with pytest.raises(Exception) as error:
        filings.record(tax.tax, tax.actors["employee"], payload(tax))
    assert getattr(error.value, "status", 0) in (403, 404)
