from __future__ import annotations

import hashlib
from decimal import Decimal

from app.ledgers.models import TransportAssignmentCreate
from app.ledgers.service import LedgersService
from app.reports.fleet import FleetMetricsService
from app.tax.costs import (
    TaxCostGenerate,
    TaxCostPolicyWrite,
    TaxCostReview,
    TaxCostService,
    TaxCostSourceWrite,
)
from app.tax.fees import TaxFeeService
from test_business import business  # noqa: F401
from test_tax06_fees import POLICY, _masters, _source


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _repair(case, service, key, vehicle, amount, source_version=1):
    return service.add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="repair",
            source_key=key,
            source_version=source_version,
            period="2026-10",
            subject_label=vehicle,
            original_ref=f"原单-{key}",
            sha256=_hash(key),
            amount=f"{Decimal(amount) / 100:.2f}",
            payload={
                "invoice_ref": key,
                "vehicle_external_id": vehicle,
                "coverage": "per_vehicle",
                "rate_cents": amount,
                "quantity_milli": 1000,
                "allocation_basis": "客户核定按车归属",
            },
        ),
    )


def test_fleet_contribution_keeps_unallocated_cost_and_does_not_fake_net_profit(business):
    case = business
    _driver, _vehicle, driver2, vehicle2 = _masters(case)
    LedgersService(case.db).create_transport_assignment(
        case.manager,
        case.book["id"],
        TransportAssignmentCreate(
            vehicle_id=vehicle2["id"],
            driver_id=driver2["id"],
            shift="full",
            contract_external_id="C1",
            valid_from="2026-10-05",
            valid_to="2026-10-05",
        ),
    )
    fee_source = _source(case)
    fee = TaxFeeService(case.db).generate(
        case.manager, case.book["id"], period="2026-10", source_file_ids=[fee_source["id"]], policy=POLICY
    )
    TaxFeeService(case.db).review(
        case.actors["accountant"], case.book["id"], fee["id"], expected_version=1, reason="独立复核"
    )
    costs = TaxCostService(case.db)
    policy = TaxCostPolicyWrite(version="cost-v1", expense_account_code="6602.03", credit_account_code="2202")
    v1 = _repair(case, costs, "INV-V1", "V1", 3000)
    v9 = _repair(case, costs, "INV-V9", "V9", 2000)
    batch = costs.generate(
        case.manager,
        case.book["id"],
        TaxCostGenerate(period="2026-10", kind="repair", source_ids=[v1["id"], v9["id"]], policy=policy, reason="客户核定按车归属"),
    )
    costs.review(
        case.actors["accountant"], case.book["id"], batch["id"], TaxCostReview(expected_version=1, review_reason="独立复核")
    )

    result = FleetMetricsService(case.db).overview(case.manager, case.book["id"], "2026-10")
    vehicles = {row["external_id"]: row for row in result["vehicles"]}
    assert vehicles["V1"]["revenue_cents"] == 9000
    assert vehicles["V1"]["assigned_cost_cents"] == 3000
    assert vehicles["V1"]["contribution_cents"] == 6000
    assert vehicles["V1"]["repair_cost_cents"] == 3000
    assert vehicles["V1"]["insurance_cost_cents"] == 0
    assert vehicles["V1"]["oa_cost_cents"] == 0
    assert vehicles["V2"]["revenue_cents"] == 13500
    assert vehicles["V2"]["assigned_cost_cents"] == 0
    assert result["totals"] == {
        **result["totals"],
        "revenue_cents": 22500,
        "assigned_cost_cents": 3000,
        "contribution_cents": 19500,
        "unallocated_cost_cents": 2000,
        "net_profit_cents": None,
        "net_profit_known": False,
        "driver_billed_cents": 22500,
        "driver_outstanding_cents": 0,
    }
    assert "共用费用未分配" in result["totals"]["net_profit_reason"]
    drivers = {row["external_id"]: row for row in result["drivers"]}
    assert drivers["D1"]["billed_cents"] == 9000
    assert drivers["D2"]["billed_cents"] == 13500
    # There are no settlement_items yet: unknown aging must stay unknown, not zero.
    assert drivers["D1"]["aging_known"] is False
    assert drivers["D1"]["outstanding_cents"] is None
    assert result["checks"]["revenue_matches_sources"] is True
    assert result["checks"]["unallocated_cost_cents"] == 2000
