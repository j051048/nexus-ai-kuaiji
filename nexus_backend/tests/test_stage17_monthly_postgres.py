import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.monthly.models import CheckCreate, MonthlyReason, PlanApproval, PlanGenerate
from app.monthly.service import MonthlyService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage17_monthly import baseline, plan, bridge_baseline
from test_stage13_legacy_settlements_postgres import legacy_pg_case

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要隔离 PostgreSQL")


def setup(case):
    return MonthlyService(case.db, case.service)


def verified_references(case, monthly):
    result = []
    for kind, amount in [("opening", "100.00"), ("closing", "99.00")]:
        row = monthly.create_baseline(case.owner, case.book, baseline(kind, amount))
        monthly.confirm_baseline(
            case.reviewer, case.book, row["id"], MonthlyReason(reason="独立核对合成外部余额")
        )
        result.append(row["id"])
    return result


def test_pg_monthly_scopes_native_independent_review_and_immutable_original(business_case):
    case = business_case
    monthly = setup(case)
    row = monthly.create_baseline(case.owner, case.book, baseline())
    assert (
        monthly.original(case.reviewer, case.book, row["id"])["sha256"] == row["original"]["sha256"]
    )
    with case.db.transaction(actor=case.unassigned) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM monthly_baselines WHERE id=?", (row["id"],)
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(Exception):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "INSERT INTO monthly_baseline_confirmations VALUES(?,?,?,?,?,?,?)",
                (
                    row["id"],
                    case.book,
                    case.owner.firm_id,
                    case.company,
                    case.owner.user_id,
                    "试图直接绕过自审",
                    "2026-10-14",
                ),
            )
    with pytest.raises(Exception):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "UPDATE monthly_baselines SET original_content='changed' WHERE id=?", (row["id"],)
            )
    assert monthly.confirm_baseline(
        case.reviewer, case.book, row["id"], MonthlyReason(reason="独立核对对应原件")
    )["confirmed"]


def test_pg_monthly_exact_differences_and_native_forged_report_rejected(business_case):
    case = business_case
    monthly = setup(case)
    opening, closing = verified_references(case, monthly)
    check = monthly.create_check(
        case.owner,
        case.book,
        CheckCreate(opening_id=opening, closing_id=closing, idempotency_key=uuid4().hex),
    )
    assert check["report"]["lines"][0]["difference"] == "-1.00"
    with case.db.transaction(actor=case.owner) as conn:
        original = dict(
            conn.execute("SELECT * FROM monthly_checks WHERE id=?", (check["id"],)).fetchone()
        )
    original.update(id=uuid4().hex, version=2, request_key=uuid4().hex)
    report = json.loads(original["report_json"])
    report["lines"][0]["difference"] = "0.00"
    report["difference_count"] = 0
    original["report_json"] = json.dumps(report)
    for manipulated in (
        report,
        {key: value for key, value in check["report"].items() if key != "unknown_count"},
        check["report"]
        | {
            "lines": [
                check["report"]["lines"][0]
                | {
                    "account_code": "9999.UNKNOWN",
                    "baseline": None,
                    "baseline_state": "unknown",
                    "actual": None,
                    "reference_closing": None,
                    "difference": None,
                }
            ],
            "unknown_count": 1,
            "difference_count": 0,
        },
    ):
        original["report_json"] = json.dumps(manipulated)
        with pytest.raises(Exception):
            with case.db.transaction(write=True, actor=case.owner) as conn:
                conn.execute(
                    "INSERT INTO monthly_checks("
                    + ",".join(original)
                    + ") VALUES("
                    + ",".join("?" for _ in original)
                    + ")",
                    tuple(original.values()),
                )
    assert (
        monthly.close_check(
            case.reviewer,
            case.book,
            check["id"],
            MonthlyReason(reason="明确保留客户资料差异并记录核对"),
        )["state"]
        == "manually_checked"
    )


def test_pg_recurring_real_review_export_and_monthly_idempotence(business_case):
    case = business_case
    monthly = setup(case)
    row = monthly.create_plan(case.owner, case.book, plan())
    with pytest.raises(Exception):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "INSERT INTO recurring_plan_approvals VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    row["id"],
                    1,
                    monthly.overview(case.owner, case.book)["ledger_id"],
                    case.book,
                    case.owner.firm_id,
                    case.company,
                    case.owner.user_id,
                    "试图绕过独立审批",
                    "2026-10-14",
                ),
            )
    monthly.approve_plan(
        case.reviewer, case.book, row["id"], PlanApproval(version=1, reason="核对合同和实际科目")
    )
    generated = monthly.generate(
        case.owner, case.book, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
    )
    assert (
        monthly.generate(
            case.owner, case.book, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
        )["draft_id"]
        == generated["draft_id"]
    )
    draft = case.service.get_draft(case.reviewer, case.book, generated["draft_id"])
    assert draft["status"] == "draft" and not draft["issues"]
    case.service.approve(case.reviewer, case.book, draft["id"], ApprovalRequest(expected_version=draft["version"]))
    assert (
        case.service.export(
            case.reviewer,
            case.book,
            ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex),
        )["count"]
        == 1
    )


def test_pg_monthly_closing_basis_invalidates_previous_review(business_case):
    from app.closing.models import PreflightRequest
    from app.closing.service import ClosingService

    case = business_case
    monthly, closing = setup(case), ClosingService(case.db)
    old = closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))
    row = monthly.create_plan(case.owner, case.book, plan())
    assert closing.get_snapshot(case.owner, case.book, old["id"])["stale"]
    monthly.approve_plan(
        case.reviewer, case.book, row["id"], PlanApproval(version=1, reason="独立审核明确金额")
    )
    report = closing.preflight(
        case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex)
    )["report"]
    assert report["counts"]["recurring_pending"] == 1


def test_pg_monthly_revoked_permission_closed_period_and_catalog_money_bypass(business_case):
    from period_fixtures import restore_period_state

    case = business_case
    monthly = setup(case)
    row = monthly.create_plan(case.owner, case.book, plan())
    with case.db.transaction(actor=case.owner) as conn:
        original = dict(
            conn.execute(
                "SELECT * FROM recurring_plan_versions WHERE plan_id=?", (row["id"],)
            ).fetchone()
        )
    original.update(plan_id=uuid4().hex, request_key=uuid4().hex)
    lines = json.loads(original["lines_json"])
    lines[0]["debit_cents"] = 0.1
    original["lines_json"] = json.dumps(lines)
    with pytest.raises(Exception):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "INSERT INTO recurring_plan_versions("
                + ",".join(original)
                + ") VALUES("
                + ",".join("?" for _ in original)
                + ")",
                tuple(original.values()),
            )
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (case.book, case.reviewer.user_id),
        )
    with pytest.raises(AccountingError):
        monthly.overview(case.reviewer, case.book)
    restore_period_state(case.db, case.book)
    with pytest.raises(AccountingError):
        monthly.create_baseline(case.owner, case.book, baseline())


def test_pg_reuses_real_accepted_legacy_balance_snapshot_and_native_mapping(legacy_pg_case):
    case = legacy_pg_case
    monthly = MonthlyService(case.db, case.ledger)
    with case.db.transaction(actor=case.manager) as conn:
        source_id = conn.execute(
            "SELECT id FROM migration_cutovers WHERE project_id=?", (case.project,)
        ).fetchone()[0]
    row = monthly.create_baseline(
        case.manager,
        case.book["id"],
        baseline(source_kind="legacy_cutover", source_id=source_id, lines=[]),
    )
    assert row["original"]["format"] == "legacy_snapshot"
    assert any(x["auxiliary_values"] for x in row["lines"])
    assert monthly.confirm_baseline(
        case.actors["accountant"],
        case.book["id"],
        row["id"],
        MonthlyReason(reason="独立核对原始接管余额与当前映射"),
    )["confirmed"]


def test_pg_aggregate_balance_and_auxiliary_movement_stay_separate(business_case):
    from app.accounting.catalog_models import AccountUpdate
    from app.delivery.models import ReceiptCreate
    from app.delivery.service import DeliveryService
    from app.ledgers.models import EntityCreate
    from app.ledgers.service import LedgersService

    case = business_case
    monthly = setup(case)
    entity = LedgersService(case.db).create_entity(
        case.owner,
        case.book,
        EntityCreate(kind="counterparty", code="SYNTHETIC-PARTY", name="虚构房东"),
    )
    monthly.catalog.update_account(
        case.owner,
        case.book,
        "2241",
        AccountUpdate(
            code="2241",
            name="其他应付款",
            category="liability",
            normal_balance="credit",
            auxiliary_requirements=["counterparty"],
            expected_version=1,
        ),
    )
    row = monthly.create_plan(
        case.owner,
        case.book,
        plan(
            lines=[
                {"account_code": "6602.01", "debit": "0.30", "credit": "0.00"},
                {
                    "account_code": "2241",
                    "debit": "0.00",
                    "credit": "0.30",
                    "auxiliary_values": {"counterparty": entity["id"]},
                },
            ]
        ),
    )
    monthly.approve_plan(
        case.reviewer,
        case.book,
        row["id"],
        PlanApproval(version=1, reason="明确核对辅助项实际归属"),
    )
    generated = monthly.generate(
        case.owner, case.book, row["id"], PlanGenerate(version=1, idempotency_key=uuid4().hex)
    )
    case.service.approve(
        case.reviewer, case.book, generated["draft_id"], ApprovalRequest(expected_version=case.service.get_draft(case.reviewer, case.book, generated["draft_id"])["version"])
    )
    export = case.service.export(
        case.reviewer,
        case.book,
        ExportRequest(draft_ids=[generated["draft_id"]], idempotency_key=uuid4().hex),
    )
    delivery = DeliveryService(case.db)
    body = json.loads(delivery.template(case.reviewer, case.book, export["id"]))
    for item in body["items"]:
        item.update(status="accepted", external_voucher_id="SYNTHETIC-" + item["draft_id"])
    delivery.create_receipt(
        case.reviewer,
        case.book,
        export["id"],
        ReceiptCreate(**body, reason="明确标记的合成对应回执", idempotency_key=uuid4().hex),
    )
    ids = []
    for kind, amount in [("opening", "-100.00"), ("closing", "-100.30")]:
        row = monthly.create_baseline(case.owner, case.book, baseline(kind, amount, code="2241"))
        monthly.confirm_baseline(
            case.reviewer, case.book, row["id"], MonthlyReason(reason="独立核对科目净借余额总计")
        )
        ids.append(row["id"])
    check = monthly.create_check(
        case.owner,
        case.book,
        CheckCreate(opening_id=ids[0], closing_id=ids[1], idempotency_key=uuid4().hex),
    )
    total = next(
        x
        for x in check["report"]["lines"]
        if x["account_code"] == "2241" and not x["auxiliary_values"]
    )
    assert total["reference_closing"] == "-100.30" and total["difference"] == "0.00"
    detail = next(
        x for x in check["report"]["lines"] if x["account_code"] == "2241" and x["auxiliary_values"]
    )
    assert detail["reference_closing"] is None


def test_pg_tplus_file_reference_derivation_keeps_original_mapping(business_case):
    from types import SimpleNamespace

    case = business_case
    row = bridge_baseline(
        SimpleNamespace(
            db=case.db, monthly=setup(case), owner=case.owner, book=case.book, company=case.company
        )
    )
    assert row["lines"][0] == {"account_code": "1002", "balance": "99.70", "auxiliary_values": {}}


def test_pg_unapproved_plan_version_cannot_hide_existing_month_close_obligation(business_case):
    from app.closing.models import PreflightRequest
    from app.closing.service import ClosingService

    case = business_case
    monthly, closing = setup(case), ClosingService(case.db)
    row = monthly.create_plan(case.owner, case.book, plan())
    monthly.approve_plan(
        case.reviewer,
        case.book,
        row["id"],
        PlanApproval(version=1, reason="独立核对原合同的本月义务"),
    )
    monthly.create_plan(
        case.owner,
        case.book,
        plan(
            plan_id=row["id"],
            expected_version=1,
            start_period="2026-09",
            end_period="2026-09",
            reason="尚未审批的终止月份修改",
        ),
    )
    assert (
        closing.preflight(case.owner, case.book, PreflightRequest(idempotency_key=uuid4().hex))[
            "report"
        ]["counts"]["recurring_pending"]
        == 1
    )
