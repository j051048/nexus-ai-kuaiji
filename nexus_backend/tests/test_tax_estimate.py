from decimal import Decimal

import pytest

from app.tax.estimate import TaxError, TaxProfile, estimate_vat


def test_small_scale_vat_and_surtax_are_exact_decimal():
    result = estimate_vat(
        profile=TaxProfile("small_scale", Decimal("0.01"), Decimal("0.12")),
        sales=Decimal("10000.00"),
    )
    assert result["vat"] == Decimal("100.00")
    assert result["surtax"] == Decimal("12.00")
    assert result["total"] == Decimal("112.00")
    assert result["filing"] == "not_filed"


def test_general_taxpayer_uses_output_minus_input_and_carries_negative():
    result = estimate_vat(
        profile=TaxProfile("general", Decimal("0.13")),
        sales=Decimal("1000.00"),
        purchases=Decimal("800.00"),
        output_vat=Decimal("130.00"),
        input_vat=Decimal("104.00"),
    )
    assert result["vat"] == Decimal("26.00")
    assert result["surtax"] == Decimal("0.00")

    negative = estimate_vat(
        profile=TaxProfile("general", Decimal("0.13")),
        sales=Decimal("100.00"),
        output_vat=Decimal("13.00"),
        input_vat=Decimal("20.00"),
    )
    assert negative["vat"] == Decimal("-7.00")
    assert negative["surtax"] == Decimal("0.00")


def test_general_taxpayer_requires_both_vat_inputs():
    with pytest.raises(TaxError) as caught:
        estimate_vat(
            profile=TaxProfile("general", Decimal("0.13")),
            sales=Decimal("100.00"),
        )
    assert caught.value.code == "general_vat_missing"


@pytest.mark.parametrize("amount", [Decimal("-1"), Decimal("1.001"), Decimal("NaN")])
def test_invalid_money_rejected(amount):
    with pytest.raises(TaxError):
        estimate_vat(profile=TaxProfile("small_scale", Decimal("0.01")), sales=amount)


@pytest.mark.parametrize("rate", [Decimal("-0.01"), Decimal("1.01"), Decimal("NaN")])
def test_invalid_rate_rejected(rate):
    with pytest.raises(TaxError):
        TaxProfile("small_scale", rate)
