"""Deterministic VAT estimate for small-scale/general taxpayers.

This is an offline calculation contract only. It does not file, pay or post.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

TaxpayerType = Literal["small_scale", "general"]


class TaxError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class TaxProfile:
    taxpayer_type: TaxpayerType
    vat_rate: Decimal
    surtax_rate: Decimal = Decimal("0")

    def __post_init__(self):
        if self.taxpayer_type not in {"small_scale", "general"}:
            raise TaxError("taxpayer_type_invalid", "纳税人类型无效")
        for value, code in (
            (self.vat_rate, "vat_rate"),
            (self.surtax_rate, "surtax_rate"),
        ):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or not Decimal("0") <= value <= Decimal("1")
            ):
                raise TaxError("tax_rate_invalid", f"{code} 必须在 0 到 1 之间")


def _money(value: Decimal, code: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise TaxError(code, "金额必须是非负有限 Decimal")
    if value.as_tuple().exponent < -2:
        raise TaxError(code, "金额最多两位小数")
    return value


def estimate_vat(
    *,
    profile: TaxProfile,
    sales: Decimal,
    purchases: Decimal = Decimal("0"),
    output_vat: Decimal | None = None,
    input_vat: Decimal | None = None,
) -> dict[str, Decimal | str]:
    sales = _money(sales, "sales_invalid")
    purchases = _money(purchases, "purchases_invalid")
    if profile.taxpayer_type == "small_scale":
        vat = sales * profile.vat_rate
    else:
        if output_vat is None or input_vat is None:
            raise TaxError("general_vat_missing", "一般纳税人需要销项税和进项税")
        output_vat = _money(output_vat, "output_vat_invalid")
        input_vat = _money(input_vat, "input_vat_invalid")
        vat = output_vat - input_vat
    vat = vat.quantize(Decimal("0.01"))
    surtax = (max(vat, Decimal("0")) * profile.surtax_rate).quantize(Decimal("0.01"))
    return {
        "taxpayer_type": profile.taxpayer_type,
        "sales": sales.quantize(Decimal("0.01")),
        "purchases": purchases.quantize(Decimal("0.01")),
        "vat": vat,
        "surtax": surtax,
        "total": (vat + surtax).quantize(Decimal("0.01")),
        "filing": "not_filed",
    }
