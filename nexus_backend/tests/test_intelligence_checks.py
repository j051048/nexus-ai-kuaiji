"""Accounting evidence checks reject mismatched facts without rounding or guessing."""
from copy import deepcopy

import pytest

from app.intelligence.checks import extraction_checks, reconcile_checks


def document(**changes):
    values = {"document_type": "invoice", "invoice_number": "000123", "issued_on": "2026-10-10",
              "seller_name": "耗材商店", "seller_tax_id": "SELLER123", "buyer_name": "试点企业", "buyer_tax_id": "BUYER123",
              "gross_amount": "10.60", "net_amount": "10.00", "tax_amount": "0.60", "currency": "CNY", **changes}
    fields = [key for key in values if key != "document_type" and values[key] is not None]
    text = "\n".join(f"{key} {values[key]}" for key in fields)
    return {**values, "pages": [{"page": 1, "raw_text": text}],
            "evidence": [{"field": key, "page": 1, "quote": f"{key} {values[key]}"} for key in fields]}


def payload(**changes):
    return {"amount": "10.60", "counterparty": "耗材商店", "direction": "expense", "occurred_on": "2026-10-10", **changes}


def codes(issues):
    return {issue["code"] for issue in issues}


def test_exact_valid_invoice_and_original_text_agree():
    value = document()
    assert extraction_checks([value], [1], {1: value["pages"][0]["raw_text"]}) == []
    assert reconcile_checks(payload(), [value], "BUYER123") == []


@pytest.mark.parametrize("changes,code", [
    ({"gross_amount": None}, "gross_missing"),
    ({"tax_amount": None}, "tax_missing"),
    ({"tax_amount": "0.61"}, "tax_sum"),
    ({"currency": None}, "currency"),
    ({"currency": "USD"}, "currency"),
    ({"document_type": "unreadable"}, "unreadable"),
])
def test_missing_and_inconsistent_fields_enter_manual_review(changes, code):
    assert "document_1_" + code in codes(extraction_checks([document(**changes)], [1]))


@pytest.mark.parametrize("mutation", ["wrong_page", "quote_not_in_ocr", "quote_not_in_original", "different_amount"])
def test_amount_evidence_requires_matching_field_page_and_quote(mutation):
    value = document()
    native = {1: value["pages"][0]["raw_text"]}
    evidence = next(item for item in value["evidence"] if item["field"] == "gross_amount")
    if mutation == "wrong_page":
        evidence["page"] = 2
    elif mutation == "quote_not_in_ocr":
        evidence["quote"] = "不存在的合计 10.60"
    elif mutation == "quote_not_in_original":
        native[1] = native[1].replace("gross_amount 10.60", "gross_amount 99.99")
    else:
        evidence["quote"] = "tax_amount 0.60"
    assert "document_1_evidence_gross_amount" in codes(extraction_checks([value], [1], native))


def test_invoice_and_payment_receipt_do_not_double_amount():
    receipt = document(document_type="receipt", invoice_number=None, net_amount=None, tax_amount=None)
    assert reconcile_checks(payload(), [document(), receipt], "BUYER123") == []


def test_repeated_invoice_is_flagged_and_not_summed_twice():
    value = document()
    assert codes(reconcile_checks(payload(), [value, deepcopy(value)])) == {"duplicate_invoice_in_event"}


def test_multiple_invoices_sum_integer_cents():
    first = document(invoice_number="000001", gross_amount="0.10", net_amount="0.10", tax_amount="0.00")
    second = document(invoice_number="000002", gross_amount="0.20", net_amount="0.20", tax_amount="0.00")
    assert reconcile_checks(payload(amount="0.30"), [first, second]) == []
    assert "document_business_amount" in codes(reconcile_checks(payload(amount="0.31"), [first, second]))


@pytest.mark.parametrize("direction,field,party", [("expense", "buyer_tax_id", "耗材商店"), ("income", "seller_tax_id", "试点企业")])
def test_current_company_tax_id_checked_for_both_purchase_and_sale(direction, field, party):
    value = document(**{field: "OTHER_COMPANY"})
    assert field + "_0" in codes(reconcile_checks(payload(direction=direction, counterparty=party), [value], "CURRENT_COMPANY"))


def test_counterparty_and_outlying_date_are_distinct_findings():
    issues = reconcile_checks(payload(), [document(seller_name="别的供应商", issued_on="2026-09-01")])
    assert codes(issues) == {"counterparty_0", "issued_on_0"}
    assert next(item for item in issues if item["code"] == "counterparty_0")["severity"] == "blocking"
    assert next(item for item in issues if item["code"] == "issued_on_0")["severity"] == "warning"
