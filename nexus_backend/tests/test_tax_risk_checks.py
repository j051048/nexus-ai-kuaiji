"""Batch 04: input-tax risks are classified before a filing is prepared."""

import pytest

from app.tax import risk_checks


def invoice(**kw):
    base = {"kind": "input", "cents": 1_000_00, "rate_bp": 1_300, "status": "valid"}
    return {**base, **kw}


def codes(result):
    return [item["code"] for item in result["findings"]]


def test_clean_period_has_no_findings():
    result = risk_checks.assess(invoices=[invoice(certified=True)])
    assert result["blocked"] is False
    assert result["findings"] == []


def test_red_letter_input_without_transfer_blocks():
    result = risk_checks.assess(invoices=[invoice(cents=-500_00)])
    assert result["blocked"] is True
    assert "input_red_not_transferred" in codes(result)
    assert result["findings"][0]["requires_reason"] is True


def test_red_letter_with_transfer_does_not_block():
    result = risk_checks.assess(invoices=[invoice(cents=-500_00, transferred_out=True)])
    assert result["blocked"] is False


def test_voided_input_needs_transfer_too():
    result = risk_checks.assess(invoices=[invoice(status="void")])
    assert "input_red_not_transferred" in codes(result)


def test_uncertified_input_is_a_warning_not_a_block():
    result = risk_checks.assess(invoices=[invoice(certified=False)])
    assert result["blocked"] is False
    finding = next(item for item in result["findings"] if item["code"] == "input_uncertified")
    assert finding["level"] == "warn" and finding["cents"] == 1_000_00


def test_near_deadline_input_warns():
    result = risk_checks.assess(invoices=[invoice(certified=True, deadline_days=7)])
    assert "input_near_deadline" in codes(result)


def test_rate_deviation_is_reported_with_magnitude():
    invoices = [
        {"kind": "output", "cents": 1_000_00, "rate_bp": 1_300, "status": "valid"},
        {"kind": "input", "cents": 1_000_00, "rate_bp": 100, "status": "valid", "certified": True},
    ]
    result = risk_checks.assess(invoices=invoices)
    finding = next(item for item in result["findings"] if item["code"] == "rate_deviation")
    assert finding["deviation_bp"] == 1_200


def test_duplicate_filing_and_unknown_receipt_block():
    duplicate = risk_checks.assess(duplicate_filing=True)
    assert duplicate["blocked"] is True and "duplicate_filing" in codes(duplicate)
    unknown = risk_checks.assess(filing={"status": "unknown"})
    assert unknown["blocked"] is True and "unknown_receipt" in codes(unknown)


def test_non_integer_cents_is_refused():
    with pytest.raises(ValueError):
        risk_checks.assess(invoices=[invoice(cents=10.5)])
