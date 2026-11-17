"""OPT01-C: tax paths must round half-up at half-cent boundaries."""
from __future__ import annotations

from decimal import Decimal

from app.accounting.invariants import scale_by_basis_points
from app.tax.basis import TaxBasisGenerate, TaxBasisPolicy, TaxBasisService
from app.tax.estimate import TaxProfile, estimate_vat
from app.tax.workpaper import _ratio
from test_business import business  # noqa: F401


def test_three_tax_paths_round_half_up(business):
    assert _ratio(150, 300) == 5
    assert _ratio(250, 300) == 8
    assert scale_by_basis_points(250, 300) == 8
    assert scale_by_basis_points(-250, 300) == -8

    policy = TaxBasisPolicy(
        version="opt01-c",
        taxpayer_type="small_scale",
        rate_bp=300,
        surtax_bp=0,
        effective_from="2026-01",
        authority="虚构测试税务口径",
        source_ref="客户确认-2026-01",
    )
    row = TaxBasisService(business.db).generate(
        business.manager,
        business.book["id"],
        TaxBasisGenerate(
            period="2026-10",
            policy=policy,
            base="1.50",
            manual=True,
            manual_evidence="客户申报表与完税凭证复印件，专员人工核定",
        ),
    )
    assert row["tax_cents"] == 5

    estimate = estimate_vat(
        profile=TaxProfile(taxpayer_type="small_scale", vat_rate=Decimal("0.03")),
        sales=Decimal("1.50"),
    )
    assert estimate["vat"] == Decimal("0.05")
