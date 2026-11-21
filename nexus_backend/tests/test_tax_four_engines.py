"""Batch 04: the four-tax engines are deterministic, signed and cent-exact.

These stay pure-function tests on purpose: wiring into the workpaper/migrations
happens in the next slice, but the arithmetic and the refusals are the part that
must never be "approximately right".
"""

import pytest

from app.accounting.domain import AccountingError
from app.tax import engines


def inv(kind="output", cents=1_000_00, rate_bp=1_300, **kw):
    return {"kind": kind, "cents": cents, "rate_bp": rate_bp, **kw}


def test_general_vat_nets_input_against_output():
    result = engines.vat(
        [inv(cents=1_100_00), inv("input", cents=880_00, certified=True)],
        taxpayer_kind="general",
    )
    assert result["output_tax_cents"] == 1_100_00 * 13 // 100
    assert result["input_tax_cents"] == 880_00 * 13 // 100
    assert result["payable_cents"] == result["output_tax_cents"] - result["input_tax_cents"]


def test_red_letter_invoice_reduces_output_tax():
    result = engines.vat(
        [inv(cents=1_000_00), inv(cents=-400_00)],
        taxpayer_kind="general",
    )
    assert result["output_tax_cents"] == 600_00 * 13 // 100


def test_void_invoice_is_excluded_but_reported():
    result = engines.vat(
        [inv(cents=1_000_00), inv(cents=1_000_00, status="void")],
        taxpayer_kind="general",
    )
    assert result["output_tax_cents"] == 1_000_00 * 13 // 100
    assert result["voided_cents"] == 1_000_00


def test_exempt_sales_produce_no_output_tax():
    result = engines.vat([inv(cents=1_000_00, status="exempt")], taxpayer_kind="general")
    assert result["output_tax_cents"] == 0
    assert result["exempt_cents"] == 1_000_00


def test_uncertified_input_is_not_deducted_but_counted():
    result = engines.vat([inv("input", cents=1_000_00, certified=False)], taxpayer_kind="general")
    assert result["input_tax_cents"] == 0
    assert result["input_uncertified_cents"] == 1_000_00 * 13 // 100


def test_input_transfer_out_increases_payable():
    plain = engines.vat([inv("input", cents=1_000_00, certified=True)], taxpayer_kind="general")
    transferred = engines.vat(
        [inv("input", cents=1_000_00, certified=True)],
        taxpayer_kind="general",
        input_transfer_out_cents=100_00,
    )
    assert transferred["carry_out_cents"] == plain["carry_out_cents"] - 100_00


def test_credit_carries_forward_instead_of_going_negative():
    result = engines.vat(
        [inv(cents=100_00), inv("input", cents=1_030_00, certified=True)],
        taxpayer_kind="general",
        carry_in_cents=50_00,
    )
    assert result["payable_cents"] == 0
    assert result["carry_out_cents"] > 0


def test_small_scale_rate_is_caller_supplied():
    result = engines.vat([inv(cents=1_000_00, rate_bp=100)], taxpayer_kind="small")
    assert result["output_tax_cents"] == 10_00  # ¥1000 * 1% = ¥10
    with pytest.raises(AccountingError):
        engines.vat([], taxpayer_kind="guess")


def test_surtax_halving_is_explicit_not_inferred():
    items = [{"code": "urban_maintenance", "rate_bp": 700}, {"code": "education", "rate_bp": 300}]
    full = engines.surtax(1_000_00, items)
    half = engines.surtax(1_000_00, items, halved=True)
    assert full["total_cents"] == 100_00
    assert half["total_cents"] == 50_00


def test_surtax_requires_policy_items():
    with pytest.raises(AccountingError, match="缺少附加税政策条目"):
        engines.surtax(100_00, [])


def test_cit_prepayment_applies_adjustments_loss_and_prepaid():
    result = engines.cit_prepayment(
        cumulative_profit_cents=1_000_000_00,
        adjustments=[{"cents": 100_000_00}, {"cents": -20_000_00}],
        loss_carryforward_cents=300_000_00,
        prepaid_cents=100_000_00,
        rate_bp=2_500,
    )
    assert result["taxable_base_cents"] == 780_000_00
    assert result["tax_cents"] == 195_000_00
    assert result["due_cents"] == 95_000_00
    assert result["annual_settlement"] == "not_enabled"


def test_cit_prepayment_refunds_when_overpaid():
    result = engines.cit_prepayment(
        cumulative_profit_cents=100_000_00, prepaid_cents=50_000_00, rate_bp=2_500
    )
    assert result["due_cents"] == -25_000_00


def test_cit_loss_never_becomes_negative_base():
    result = engines.cit_prepayment(
        cumulative_profit_cents=-50_000_00, loss_carryforward_cents=10_000_00, rate_bp=2_500
    )
    assert result["taxable_base_cents"] == 0
    assert result["loss_used_cents"] == 0
    assert result["due_cents"] == 0


def test_stamp_duty_is_idempotent_on_contract_key():
    rates = {"sale": 30}
    first = engines.stamp_duty([{"key": "C-1", "kind": "sale", "cents": 1_000_000_00}], rates)
    assert first["total_cents"] == 3_000_00  # ¥1,000,000 * 0.3%
    # A later run must consult the keys that already produced a taxable line.
    with pytest.raises(AccountingError, match="同一合同重复计税"):
        engines.stamp_duty(
            [{"key": "C-1", "kind": "sale", "cents": 1}], rates, already_counted={"C-1"}
        )
    with pytest.raises(AccountingError, match="同一合同重复计税"):
        engines.stamp_duty(
            [
                {"key": "C-2", "kind": "sale", "cents": 1},
                {"key": "C-2", "kind": "sale", "cents": 1},
            ],
            rates,
        )


def test_stamp_duty_unknown_item_is_a_409_not_a_guess():
    with pytest.raises(AccountingError) as refused:
        engines.stamp_duty([{"key": "C-2", "kind": "lease", "cents": 100}], {"sale": 30})
    assert refused.value.status == 409


def test_float_money_is_refused_everywhere():
    with pytest.raises(AccountingError):
        engines.vat([inv(cents=10.5)], taxpayer_kind="general")
    with pytest.raises(AccountingError):
        engines.cit_prepayment(cumulative_profit_cents=1.0, rate_bp=2_500)
