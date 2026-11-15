from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.tax.basis import (
    TaxBasisAdjustment,
    TaxBasisGenerate,
    TaxBasisPolicy,
    TaxBasisReview,
    TaxBasisService,
)
from test_business import business  # noqa: F401

POLICY = TaxBasisPolicy(
    version="tax-policy-2026-v1", taxpayer_type="small_scale", rate_bp=300, surtax_bp=200,
    effective_from="2026-01", authority="虚构测试税务口径", source_ref="客户确认-2026-01",
)


def _generate(case, **changes):
    values = dict(
        period="2026-10", policy=POLICY, base="100000.00",
        adjustments=[TaxBasisAdjustment(code="上期多计", amount="-1000.00", reason="上期多计冲回")],
        manual=True, manual_evidence="客户申报表与完税凭证复印件，专员人工核定",
        source_refs=[], workpaper_id="",
    )
    values.update(changes)
    return TaxBasisService(case.db).generate(case.manager, case.book["id"], TaxBasisGenerate(**values))


def test_tax_basis_keeps_policy_base_adjustments_and_manual_evidence(business):
    case = business
    row = _generate(case)
    assert row["status"] == "draft"
    assert row["base_cents"] == 10000000
    assert row["tax_cents"] == 206000
    assert row["adjustments"][0]["reason"] == "上期多计冲回"
    assert "人工核定" in row["manual_evidence"]
    assert row["policy"]["rate_bp"] == 300
    assert _generate(case)["version"] == 2


def test_tax_basis_rejects_missing_evidence_bad_period_and_self_review(business):
    case = business
    with pytest.raises(AccountingError) as exc:
        _generate(case, manual=True, manual_evidence="")
    assert "人工核定" in exc.value.detail
    with pytest.raises(AccountingError) as exc:
        _generate(case, manual=False, source_refs=[])
    assert "来源依据" in exc.value.detail
    with pytest.raises(AccountingError) as exc:
        _generate(case, period="2026-09")
    assert "期间" in exc.value.detail
    with pytest.raises(AccountingError) as exc:
        _generate(case, workpaper_id="missing")
    assert exc.value.status == 404

    row = _generate(case)
    with pytest.raises(AccountingError) as exc:
        TaxBasisService(case.db).review(
            case.manager, case.book["id"], row["id"],
            TaxBasisReview(expected_version=1, reason="自审不应通过"),
        )
    assert exc.value.status == 403

    reviewed = TaxBasisService(case.db).review(
        case.actors["accountant"], case.book["id"], row["id"],
        TaxBasisReview(expected_version=1, reason="独立复核确认政策、基数与人工依据"),
    )
    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewed_by"] == case.actors["accountant"].user_id
    with pytest.raises(AccountingError):
        TaxBasisService(case.db).review(
            case.actors["accountant"], case.book["id"], row["id"],
            TaxBasisReview(expected_version=1, reason="重复复核应拒绝"),
        )
