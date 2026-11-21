"""Deterministic four-tax engines: VAT, surtax, CIT prepayment, stamp duty.

Every function here is pure and works in integer cents only. Rates arrive as
basis points that the caller resolved from approved policy items -- nothing in
this module invents a default rate or a "best guess" taxpayer kind. A missing
policy item is a caller-side 409 (see `app.tax.policy_items.calculate`); these
engines only refuse inputs they cannot interpret.
"""

from ..accounting.domain import AccountingError
from ..accounting.invariants import scale_by_basis_points

TAXPAYER_KINDS = {"general", "small"}
INVOICE_KINDS = {"output", "input"}


def _cents(value, label: str) -> int:
    if type(value) is not int:
        raise AccountingError(f"{label}必须为整数分", 422)
    return value


def _rate(value, label: str) -> int:
    if type(value) is not int or value < 0:
        raise AccountingError(f"{label}必须为非负整数基点", 422)
    return value


def vat(
    invoices,
    *,
    taxpayer_kind: str,
    carry_in_cents: int = 0,
    input_transfer_out_cents: int = 0,
):
    """VAT for one period.

    `invoices` items carry ``kind`` (output|input), ``cents`` whose sign already
    reflects red-letter reversal (negative), ``rate_bp``, ``status``
    (valid|void|exempt) and ``certified`` for input credit. Voided invoices are
    excluded but reported, exempt sales never produce output tax, and input tax
    is deductible only once certified -- the caller records the uncertified
    remainder instead of silently claiming it.
    """
    if taxpayer_kind not in TAXPAYER_KINDS:
        raise AccountingError("纳税人类型必须是 general 或 small", 422)
    carry_in = _cents(carry_in_cents, "上期留抵税额")
    if carry_in < 0:
        raise AccountingError("上期留抵税额不能为负", 422)
    transfer_out = _cents(input_transfer_out_cents, "进项税额转出")
    output_tax = input_tax = voided = exempt = uncertified = 0
    for item in invoices:
        kind = item.get("kind")
        if kind not in INVOICE_KINDS:
            raise AccountingError("发票方向必须是 output 或 input", 422)
        cents = _cents(item.get("cents"), "发票金额")
        rate = _rate(item.get("rate_bp"), "发票税率")
        status = item.get("status", "valid")
        if status == "void":
            voided += cents
            continue
        if status == "exempt":
            exempt += cents
            continue
        if status != "valid":
            raise AccountingError("发票状态必须是 valid、void 或 exempt", 422)
        tax = scale_by_basis_points(cents, rate)
        if kind == "output":
            output_tax += tax
        elif item.get("certified") is not True:
            uncertified += tax
        else:
            input_tax += tax
    net_input = input_tax - transfer_out
    payable_before_credit = output_tax - net_input
    credit_used = min(carry_in, max(payable_before_credit, 0))
    payable = payable_before_credit - credit_used
    carry_out = 0 if payable > 0 else -payable
    return {
        "taxpayer_kind": taxpayer_kind,
        "output_tax_cents": output_tax,
        "input_tax_cents": input_tax,
        "input_uncertified_cents": uncertified,
        "input_transfer_out_cents": transfer_out,
        "carry_in_cents": carry_in,
        "carry_in_used_cents": credit_used,
        "carry_out_cents": carry_out,
        "voided_cents": voided,
        "exempt_cents": exempt,
        "payable_cents": max(payable, 0),
    }


def surtax(vat_payable_cents, items, *, halved: bool = False):
    """Surtaxes computed on the VAT actually payable.

    `halved` must come from an explicitly approved "六税两费" policy window; this
    engine never decides eligibility on its own.
    """
    base = _cents(vat_payable_cents, "增值税实缴额")
    if base < 0:
        raise AccountingError("增值税实缴额不能为负", 422)
    lines = []
    for item in items:
        code = item.get("code")
        if not isinstance(code, str) or not code:
            raise AccountingError("附加税条目缺少税目代码", 422)
        rate = _rate(item.get("rate_bp"), "附加税税率")
        amount = scale_by_basis_points(base, rate)
        if halved:
            amount = scale_by_basis_points(amount, 5000)
        lines.append({"code": code, "rate_bp": rate, "amount_cents": amount})
    if not lines:
        raise AccountingError("缺少附加税政策条目", 409)
    return {
        "base_cents": base,
        "halved": halved,
        "lines": lines,
        "total_cents": sum(line["amount_cents"] for line in lines),
    }


def cit_prepayment(
    *,
    cumulative_profit_cents,
    adjustments=(),
    loss_carryforward_cents: int = 0,
    prepaid_cents: int = 0,
    rate_bp: int,
):
    """CIT prepayment on the cumulative basis (annual settlement stays off)."""
    profit = _cents(cumulative_profit_cents, "累计利润")
    loss = _cents(loss_carryforward_cents, "弥补以前年度亏损")
    prepaid = _cents(prepaid_cents, "累计已预缴")
    if loss < 0 or prepaid < 0:
        raise AccountingError("弥补亏损与已预缴金额不能为负", 422)
    rate = _rate(rate_bp, "企业所得税税率")
    adjusted = 0
    for item in adjustments:
        adjusted += _cents(item.get("cents"), "纳税调整金额")
    taxable_before_loss = profit + adjusted
    loss_used = min(loss, max(taxable_before_loss, 0))
    base = max(taxable_before_loss - loss, 0)
    tax = scale_by_basis_points(base, rate)
    return {
        "cumulative_profit_cents": profit,
        "adjustment_cents": adjusted,
        "loss_used_cents": loss_used,
        "taxable_base_cents": base,
        "unused_loss_cents": loss - loss_used,
        "tax_cents": tax,
        "prepaid_cents": prepaid,
        "due_cents": tax - prepaid,
        "annual_settlement": "not_enabled",
    }


def stamp_duty(contracts, rates, *, already_counted=()):
    """Stamp duty per taxable contract, idempotent on the contract key.

    A repeated key is refused instead of counted twice; an unknown tax item is
    a 409 rather than an assumed rate.
    """
    seen = {key for key in already_counted if isinstance(key, str)}
    lines = []
    for item in contracts:
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise AccountingError("印花税合同缺少幂等计征键", 422)
        if key in seen:
            raise AccountingError(f"同一合同重复计税：{key}", 409)
        seen.add(key)
        kind = item.get("kind")
        if kind not in rates:
            raise AccountingError(f"缺少印花税税目税率：{kind}", 409)
        cents = _cents(item.get("cents"), "合同金额")
        if cents < 0:
            raise AccountingError("合同金额不能为负", 422)
        rate = _rate(rates[kind], "印花税税率")
        lines.append(
            {
                "key": key,
                "kind": kind,
                "rate_bp": rate,
                "amount_cents": scale_by_basis_points(cents, rate),
            }
        )
    return {"lines": lines, "total_cents": sum(line["amount_cents"] for line in lines)}
