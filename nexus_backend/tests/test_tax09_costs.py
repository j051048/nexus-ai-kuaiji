from __future__ import annotations

from decimal import Decimal

import pytest

from app.accounting.domain import AccountingError, Actor
from app.tax.costs import (
    TaxCostGenerate,
    TaxCostInvalidate,
    TaxCostPolicyWrite,
    TaxCostReview,
    TaxCostService,
    TaxCostSourceWrite,
)
from test_business import business  # noqa: F401

POLICY = TaxCostPolicyWrite(version="cost-v1", expense_account_code="6602.03", credit_account_code="2202")
SHA = "a" * 64


def _repair(case, key="FP-001", version=1, amount="100.00"):
    return TaxCostService(case.db).add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="repair", source_key=key, source_version=version, period="2026-10",
            subject_label="京A1", original_ref="原件/维修-R-1.pdf", sha256=SHA,
            amount=amount,
            payload={
                "invoice_ref": key, "vehicle_external_id": "V1", "coverage": "per_vehicle",
                "rate_cents": 5000, "quantity_milli": 2000,
            },
        ),
    )


def _insurance(case, key="P-1", version=1):
    return TaxCostService(case.db).add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="insurance", source_key=key, source_version=version, period="2026-10",
            subject_label="京A1", original_ref="原件/保单-P-1.pdf", sha256="b" * 64,
            amount="100.00",
            payload={
                "policy_no": "P-2026-01", "premium_cents": 120000,
                "start_period": "2026-01", "end_period": "2026-12", "total_months": 12,
                "algorithm": "straight_line",
            },
        ),
    )


def _oa(case, key="OA-2026-10-1"):
    return TaxCostService(case.db).add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="oa", source_key=key, source_version=3, period="2026-10",
            subject_label="车队费用", original_ref="OA/审批快照-1.json", sha256="c" * 64,
            amount="100.00",
            payload={
                "oa_event_id": key, "oa_version": 3, "approved_by": "部门负责人",
                "approved_period": "2026-10",
                "lines": [
                    {"account_code": "6602.03", "amount_cents": 6000},
                    {"account_code": "6602.04", "amount_cents": 4000},
                ],
            },
        ),
    )


def _generate(case, sources, kind):
    return TaxCostService(case.db).generate(
        case.manager,
        case.book["id"],
        TaxCostGenerate(
            period="2026-10", kind=kind, source_ids=[source["id"] for source in sources],
            policy=POLICY, reason="按核定来源生成成本批次",
        ),
    )


def test_insurance_manual_allocation_is_explicit_and_complete(business):
    case = business
    source = TaxCostService(case.db).add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="insurance", source_key="P-MANUAL", source_version=1, period="2026-10",
            subject_label="京A1", original_ref="原件/保单-P-MANUAL.pdf", sha256="d" * 64,
            amount="30.00",
            payload={
                "policy_no": "P-MANUAL", "premium_cents": 10000,
                "start_period": "2026-10", "end_period": "2026-12", "total_months": 3,
                "algorithm": "manual",
                "allocation_cents": {"2026-10": 3000, "2026-11": 3000, "2026-12": 4000},
            },
        ),
    )
    assert source["amount_cents"] == 3000
    with pytest.raises(AccountingError):
        TaxCostService(case.db).add_source(
            case.manager,
            case.book["id"],
            TaxCostSourceWrite(
                kind="insurance", source_key="P-MANUAL", source_version=2, period="2026-10",
                subject_label="京A1", original_ref="原件/保单-P-MANUAL.pdf", sha256="d" * 64,
                amount="35.00",
                payload={
                    "policy_no": "P-MANUAL", "premium_cents": 10000,
                    "start_period": "2026-10", "end_period": "2026-12", "total_months": 3,
                    "algorithm": "manual",
                    "allocation_cents": {"2026-10": 3000, "2026-11": 3000, "2026-12": 4000},
                },
            ),
        )


def test_insurance_straight_line_tail_is_explicit_at_policy_end():
    TaxCostService._validate_payload(
        "insurance",
        "2026-12",
        838,
        {
            "policy_no": "P-TAIL", "premium_cents": 10001,
            "start_period": "2026-01", "end_period": "2026-12", "total_months": 12,
            "algorithm": "straight_line",
        },
    )


def test_cost_lists_use_the_item_envelope_expected_by_the_ui(business):
    case = business
    source = _repair(case)
    batch = _generate(case, [source], "repair")
    service = TaxCostService(case.db)
    sources = service.list_sources(case.manager, case.book["id"], kind="repair")
    batches = service.list_batches(case.manager, case.book["id"], kind="repair")
    assert [row["id"] for row in sources["items"]] == [source["id"]]
    assert [row["id"] for row in batches["items"]] == [batch["id"]]


def test_repair_batch_is_balanced_and_needs_independent_review(business):
    case = business
    source = _repair(case)
    assert source["amount_cents"] == 10000
    batch = _generate(case, [source], "repair")
    assert batch["amount_cents"] == 10000
    assert batch["draft_status"] == "draft"
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert [(line["account_code"], line["debit"], line["credit"]) for line in draft["lines"]] == [
        ("6602.03", "100.00", "0.00"),
        ("2202", "0.00", "100.00"),
    ]
    assert draft["issues"] == []
    service = TaxCostService(case.db)
    with pytest.raises(AccountingError):
        service.review(case.manager, case.book["id"], batch["id"],
                       TaxCostReview(expected_version=1, review_reason="本人复核"))
    reviewed = service.review(case.actors["accountant"], case.book["id"], batch["id"],
                              TaxCostReview(expected_version=1, review_reason="独立复核"))
    assert reviewed["status"] == "reviewed"
    assert reviewed["draft_status"] == "approved"


def test_insurance_straight_line_uses_explicit_period_and_tail(business):
    case = business
    source = _insurance(case)
    assert source["amount_cents"] == 10000
    batch = _generate(case, [source], "insurance")
    assert batch["amount_cents"] == 10000
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert [line["account_code"] for line in draft["lines"]] == ["6602.03", "2202"]


def test_oa_snapshot_preserves_lines_and_total(business):
    case = business
    source = _oa(case)
    batch = _generate(case, [source], "oa")
    draft = case.ledger.get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert [(line["account_code"], line["debit"]) for line in draft["lines"]] == [
        ("6602.03", "60.00"),
        ("6602.04", "40.00"),
        ("2202", "0.00"),
    ]
    assert sum(Decimal(line["debit"]) for line in draft["lines"]) == Decimal("100.00")


def test_invalid_repair_quantity_and_insurance_period_are_rejected(business):
    case = business
    service = TaxCostService(case.db)
    with pytest.raises(AccountingError):
        service.add_source(case.manager, case.book["id"], TaxCostSourceWrite(
            kind="repair", source_key="R-bad", source_version=1, period="2026-10",
            original_ref="x", sha256=SHA, amount="99.00",
            payload={"invoice_ref": "F", "vehicle_external_id": "V1", "coverage": "per_vehicle",
                     "rate_cents": 5000, "quantity_milli": 2000},
        ))
    with pytest.raises(AccountingError):
        service.add_source(case.manager, case.book["id"], TaxCostSourceWrite(
            kind="insurance", source_key="P-bad", source_version=1, period="2026-10",
            original_ref="x", sha256="b" * 64, amount="100.00",
            payload={"policy_no": "P", "premium_cents": 120000, "start_period": "2026-01",
                     "end_period": "2026-12", "total_months": 11, "algorithm": "straight_line"},
        ))


def test_source_upgrade_makes_batch_stale_and_cannot_be_reviewed(business):
    case = business
    source = _repair(case)
    batch = _generate(case, [source], "repair")
    _repair(case, version=2)
    service = TaxCostService(case.db)
    stale = service.get_batch(case.manager, case.book["id"], batch["id"])
    assert stale["stale"] is True
    with pytest.raises(AccountingError) as error:
        service.validate(case.manager, case.book["id"], batch["id"])
    assert error.value.status == 409
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"],
                       TaxCostReview(expected_version=1, review_reason="来源已升级"))


def test_invalidate_keeps_history_and_review_rejects_invalidated(business):
    case = business
    source = _repair(case)
    batch = _generate(case, [source], "repair")
    service = TaxCostService(case.db)
    invalid = service.invalidate(case.manager, case.book["id"], batch["id"],
                                 TaxCostInvalidate(expected_version=1, reason="来源更正"))
    assert invalid["status"] == "invalidated"
    with pytest.raises(AccountingError):
        service.review(case.actors["accountant"], case.book["id"], batch["id"],
                       TaxCostReview(expected_version=2, review_reason="失效后复核"))


def test_duplicate_invoice_and_oa_version_cannot_be_reused(business):
    case = business
    service = TaxCostService(case.db)
    _repair(case)
    with pytest.raises(AccountingError):
        service.add_source(case.manager, case.book["id"], TaxCostSourceWrite(
            kind="repair", source_key="FP-001", source_version=1, period="2026-10",
            original_ref="x", sha256=SHA, amount="90.00",
            payload={"invoice_ref": "FP-001", "vehicle_external_id": "V1", "coverage": "actual"},
        ))
    _oa(case)
    with pytest.raises(AccountingError):
        service.add_source(case.manager, case.book["id"], TaxCostSourceWrite(
            kind="oa", source_key="OA-2026-10-1", source_version=2, period="2026-10",
            original_ref="x", sha256="c" * 64, amount="100.00",
            payload={"oa_event_id": "OA-2026-10-1", "oa_version": 2, "approved_by": "负责人",
                     "approved_period": "2026-10",
                     "lines": [{"account_code": "6602.03", "amount_cents": 10000}]},
        ))


def test_operator_cannot_register_cost_source(business):
    case = business
    with pytest.raises(AccountingError):
        TaxCostService(case.db).add_source(Actor(user_id="outsider", firm_id=case.manager.firm_id), case.book["id"], TaxCostSourceWrite(
            kind="repair", source_key="FP-OP", source_version=1, period="2026-10",
            original_ref="x", sha256=SHA, amount="100.00",
            payload={"invoice_ref": "FP-OP", "vehicle_external_id": "V1", "coverage": "actual"},
        ))
