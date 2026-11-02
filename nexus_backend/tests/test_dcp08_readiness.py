"""DCP08-A：申报前清单只报告已经能证明的事。"""

import pytest

from app.tax.readiness import filing_readiness
from app.tax.workpaper import TaxWorkpaperService
from test_business import business  # noqa: F401  (pytest fixture re-export)
from test_tax_workpaper import invoices, profile, row


@pytest.fixture
def tax(business):
    business.tax = TaxWorkpaperService(business.db)
    business.actor_accountant = business.actors["accountant"]
    return business


def test_missing_profile_blocks_instead_of_estimating(tax):
    ready = filing_readiness(tax.tax, tax.manager, tax.book["id"], "2026-10")
    assert ready["supported"] is False
    assert "纳税人档案" in ready["blocked_reason"]
    assert [item["readiness"] for item in ready["kinds"]] == ["blocked"]
    assert ready["external_result"] == "none"


def test_general_taxpayer_is_blocked_with_the_generator_reason(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile(taxpayer_type="general"))
    ready = filing_readiness(tax.tax, tax.manager, tax.book["id"], "2026-10")
    assert ready["supported"] is False
    assert "进项抵扣" in ready["blocked_reason"]


def test_draft_workpaper_is_unreviewed_and_not_ready(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    ready = filing_readiness(tax.tax, tax.manager, tax.book["id"], "2026-10")
    kinds = {item["kind"]: item for item in ready["kinds"]}
    assert kinds["vat"]["readiness"] == "unreviewed"
    assert kinds["vat"]["current"] is True
    assert kinds["vat"]["reviewed"] is False
    assert kinds["vat"]["workpaper_version"] == paper["version"]
    assert "复核" in kinds["vat"]["next_action"]
    assert kinds["vat"]["action_target"] == {
        "kind": "tax",
        "book_id": tax.book["id"],
        "period": "2026-10",
    }


def test_new_invoice_makes_the_workpaper_stale(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    invoices(tax, [row("output", 50_000, 1_500)], tag="2")
    ready = filing_readiness(tax.tax, tax.manager, tax.book["id"], "2026-10")
    kinds = {item["kind"]: item for item in ready["kinds"]}
    assert kinds["vat"]["readiness"] == "stale"
    assert kinds["vat"]["current"] is False
    assert "已变化" in kinds["vat"]["reason"]


def test_reviewed_current_workpaper_is_ready_but_stays_not_filed(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    review = tax.tax.review(tax.manager, tax.book["id"], paper["id"], "账表差异已说明", 1)
    ready = filing_readiness(tax.tax, tax.manager, tax.book["id"], "2026-10")
    kinds = {item["kind"]: item for item in ready["kinds"]}
    assert kinds["vat"]["readiness"] == "ready"
    assert kinds["vat"]["reviewed"] is True
    assert kinds["vat"]["variance_cents"] == 3000
    assert review["filing"] == "not_filed"
    assert ready["external_result"] == "none"


def test_other_company_cannot_read_the_readiness(tax):
    other = tax.company
    with pytest.raises(Exception) as error:
        filing_readiness(tax.tax, tax.actors["employee"], other["id"], "2026-10")
    assert getattr(error.value, "status", 0) in (403, 404)
