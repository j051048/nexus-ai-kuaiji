from __future__ import annotations

import pytest

from app.accounting.domain import AccountingError
from app.tax.costs import (
    RepairRateDeactivate,
    RepairRateDraft,
    RepairRateService,
    TaxCostGenerate,
    TaxCostPolicyWrite,
    TaxCostReview,
    TaxCostService,
    TaxCostSourceWrite,
)
from test_business import business  # noqa: F401

POLICY = TaxCostPolicyWrite(
    version="cost-v1", expense_account_code="6602.03", credit_account_code="2202"
)


def _draft(valid_from="2026-10", valid_to="2026-12", reason="2026 年出租车基础保养费率"):
    return RepairRateDraft(
        vehicle_type="出租车",
        rate_cents=5000,
        valid_from=valid_from,
        valid_to=valid_to,
        coverage_scope="基础保养（不含轮胎）",
        source_ref="合同/2026-费率",
        unit="per_vehicle",
        currency="CNY",
        tax_included=False,
        reason=reason,
    )


def _active_rate(case):
    rates = RepairRateService(case.db)
    draft = rates.create_draft(case.manager, case.book["id"], _draft())
    confirmed = rates.confirm(
        case.actors["accountant"], case.book["id"], draft["id"], draft["version"]
    )
    return rates.activate(case.manager, case.book["id"], confirmed["id"], confirmed["version"])


def test_rate_lifecycle_confirm_activate_renew_and_deactivate(business):
    case = business
    rates = RepairRateService(case.db)
    draft = rates.create_draft(case.manager, case.book["id"], _draft())
    assert draft["status"] == "draft" and draft["version"] == 1
    with pytest.raises(AccountingError) as self_confirm:
        rates.confirm(case.manager, case.book["id"], draft["id"], 1)
    assert self_confirm.value.status == 409
    confirmed = rates.confirm(case.actors["accountant"], case.book["id"], draft["id"], 1)
    assert confirmed["status"] == "confirmed"
    active = rates.activate(case.manager, case.book["id"], draft["id"], 1)
    assert active["status"] == "active"
    assert rates.resolve(case.manager, case.book["id"], "出租车", "2026-11")["id"] == active["id"]

    renewal = rates.renew(
        case.manager,
        case.book["id"],
        active["id"],
        _draft(valid_from="2027-01", valid_to="2027-12", reason="2027 年续期费率"),
    )
    assert renewal["version"] == 2 and renewal["logical_id"] == active["logical_id"]
    rates.confirm(case.actors["accountant"], case.book["id"], renewal["id"], 2)
    upgraded = rates.activate(case.manager, case.book["id"], renewal["id"], 2)
    assert upgraded["status"] == "active"

    history = rates.history(case.manager, case.book["id"], active["logical_id"])
    assert [row["version"] for row in history["items"]] == [1, 2]
    assert history["items"][0]["status"] == "superseded"
    assert history["items"][0]["valid_to"] == "2026-12"
    assert rates.resolve(case.manager, case.book["id"], "出租车", "2026-12")["id"] == active["id"]
    assert rates.resolve(case.manager, case.book["id"], "出租车", "2027-02")["id"] == upgraded["id"]

    with pytest.raises(AccountingError):
        rates.deactivate(
            case.actors["employee"],
            case.book["id"],
            upgraded["id"],
            RepairRateDeactivate(expected_version=2, reason="无权停用该费率版本"),
        )
    stopped = rates.deactivate(
        case.manager,
        case.book["id"],
        upgraded["id"],
        RepairRateDeactivate(expected_version=2, reason="2027 年合同终止，停止使用该费率"),
    )
    assert stopped["status"] == "inactive"
    with pytest.raises(AccountingError):
        rates.resolve(case.manager, case.book["id"], "出租车", "2027-02")
    with pytest.raises(AccountingError):
        rates.list(case.actors["employee"], case.book["id"])


def test_rate_deactivation_blocks_approved_batch_review_and_marks_impact(business):
    case = business
    rates = RepairRateService(case.db)
    card = _active_rate(case)
    costs = TaxCostService(case.db)
    source = costs.add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="repair",
            source_key="FP-RATE-LIFECYCLE",
            source_version=1,
            period="2026-10",
            subject_label="京A1",
            original_ref="原件/维修-RATE-LIFECYCLE.pdf",
            sha256="d" * 64,
            amount="300.00",
            payload={
                "invoice_ref": "FP-RATE-LIFECYCLE",
                "vehicle_external_id": "V1",
                "vehicle_type": "出租车",
                "coverage": "per_vehicle",
                "quantity_milli": 6000,
                "rate_card_id": card["id"],
                "rate_version": 1,
            },
        ),
    )
    batch = costs.generate(
        case.manager,
        case.book["id"],
        TaxCostGenerate(
            period="2026-10",
            kind="repair",
            source_ids=[source["id"]],
            policy=POLICY,
            reason="按费率卡生成维修成本",
        ),
    )
    impact = rates.impact(case.manager, case.book["id"], card["id"])
    assert any(row["source_id"] == source["id"] for row in impact["items"])

    with pytest.raises(AccountingError) as mismatch:
        costs.add_source(
            case.manager,
            case.book["id"],
            TaxCostSourceWrite(
                kind="repair",
                source_key="FP-RATE-WRONG-VERSION",
                source_version=1,
                period="2026-10",
                subject_label="京A2",
                original_ref="原件/维修-RATE-WRONG-VERSION.pdf",
                sha256="e" * 64,
                amount="300.00",
                payload={
                    "invoice_ref": "FP-RATE-WRONG-VERSION",
                    "vehicle_external_id": "V2",
                    "vehicle_type": "出租车",
                    "coverage": "per_vehicle",
                    "quantity_milli": 6000,
                    "rate_card_id": card["id"],
                    "rate_version": 2,
                },
            ),
        )
    assert mismatch.value.status == 409

    rates.deactivate(
        case.manager,
        case.book["id"],
        card["id"],
        RepairRateDeactivate(expected_version=1, reason="2026 年 10 月起停止使用该费率"),
    )
    with pytest.raises(AccountingError) as invalid:
        costs.validate(case.manager, case.book["id"], batch["id"])
    assert invalid.value.status == 409
    with pytest.raises(AccountingError) as review:
        costs.review(
            case.actors["accountant"],
            case.book["id"],
            batch["id"],
            TaxCostReview(expected_version=batch["version"], review_reason="复核维修成本"),
        )
    assert review.value.status == 409
