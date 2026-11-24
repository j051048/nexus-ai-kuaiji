"""TAX06: real PostgreSQL proof for fee batches and independent review."""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.ledgers.models import TransportAssignmentCreate
from app.ledgers.service import LedgersService
from app.tax.fees import TaxFeeService
from app.tax.sources import TaxSourceAdapter
from scripts.pg_suite_target import load_suite_target, verify_suite_target
from test_tax06_fees import COLS, POLICY, ROWS, _bank, _masters
from test_tax04_sources import xlsx

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("TAX06 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


@pytest.fixture
def case(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "tax06-firm-" + suffix
    maker = Actor(user_id=f"tax06-maker-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"tax06-reviewer-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((maker, "owner"), (reviewer, "reviewer")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"tax06-{role}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构TAX06事务所", maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'reviewer')",
            (firm, reviewer.user_id),
        )
    book = AccountingService(db).create_book(
        maker, BookCreate(name="虚构TAX06账套", period="2026-10")
    )
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    result = SimpleNamespace(
        db=db,
        ledger=LedgersService(db),
        manager=maker,
        reviewer=reviewer,
        book=book,
        actors={"accountant": reviewer},
    )
    _driver, _vehicle, driver2, vehicle2 = _masters(result)
    LedgersService(db).create_transport_assignment(
        maker,
        book["id"],
        TransportAssignmentCreate(
            vehicle_id=vehicle2["id"],
            driver_id=driver2["id"],
            shift="full",
            contract_external_id="C1",
            valid_from="2026-10-05",
            valid_to="2026-10-05",
        ),
    )
    source = TaxSourceAdapter(db).import_source(
        maker,
        book["id"],
        filename="规费.xlsx",
        data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账",
        external_id="S-FEE-PG",
        version=1,
        sheet_name="运输明细",
        column_map=COLS,
        rule_version="tax-source-v1",
    )
    return result, source


def test_pg_fee_batch_balanced_and_independently_reviewed(case):
    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    assert batch["total_cents"] == 22500
    draft = AccountingService(business.db).get_draft(
        business.manager, business.book["id"], batch["draft_id"]
    )
    assert len(draft["lines"]) == 6 and draft["issues"] == []
    reviewed = service.review(
        business.reviewer,
        business.book["id"],
        batch["id"],
        expected_version=1,
        reason="PG 独立复核",
    )
    assert reviewed["status"] == "reviewed" and reviewed["draft"]["status"] == "approved"
    with business.db.transaction(actor=business.manager) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM tax_fee_batches").fetchone()["c"] >= 1
        assert conn.execute("SELECT COUNT(*) c FROM draft_line_sources").fetchone()["c"] == 6
        receivables = conn.execute(
            "SELECT COUNT(*) c,COALESCE(SUM(amount_cents),0) total FROM fee_receivable_items WHERE batch_id=?",
            (batch["id"],),
        ).fetchone()
        assert receivables["c"] == 3 and int(receivables["total"]) == 22500


def test_pg_fee_batch_rejects_maker_review_and_source_upgrade(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    with pytest.raises(AccountingError) as maker_error:
        service.review(
            business.manager, business.book["id"], batch["id"], expected_version=1, reason="自审"
        )
    assert maker_error.value.status == 409
    TaxSourceAdapter(business.db).import_source(
        business.manager,
        business.book["id"],
        filename="规费.xlsx",
        data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账",
        external_id="S-FEE-PG",
        version=2,
        sheet_name="运输明细",
        column_map=COLS,
        rule_version="tax-source-v1",
    )
    with pytest.raises(AccountingError) as stale:
        service.review(
            business.reviewer,
            business.book["id"],
            batch["id"],
            expected_version=1,
            reason="来源升级后复核",
        )
    assert stale.value.status == 409
    with pytest.raises(AccountingError) as validate:
        service.validate(business.reviewer, business.book["id"], batch["id"])
    assert validate.value.status == 409


def test_pg_fee_batch_rls_hides_other_firm(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    suffix = uuid4().hex[:12]
    outsider = Actor(user_id=f"tax06-outsider-{suffix}", firm_id=f"tax06-other-{suffix}")
    import psycopg

    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (outsider.user_id, "outside", f"outside-{suffix}@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (outsider.firm_id, "外部虚构事务所", outsider.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (outsider.firm_id, outsider.user_id),
        )
    with pytest.raises(AccountingError) as hidden:
        service.get(outsider, business.book["id"], batch["id"])
    assert hidden.value.status == 404
    with pytest.raises(AccountingError) as receivables_hidden:
        service.list_receivables(outsider, business.book["id"])
    assert receivables_hidden.value.status == 404


def test_pg_fleet_metrics_separates_unallocated_cost_and_unknown_profit(case):
    from app.reports.fleet import FleetMetricsService
    from app.tax.costs import (
        TaxCostGenerate,
        TaxCostPolicyWrite,
        TaxCostReview,
        TaxCostService,
        TaxCostSourceWrite,
    )

    business, source = case
    fee = TaxFeeService(business.db).generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    TaxFeeService(business.db).review(
        business.reviewer,
        business.book["id"],
        fee["id"],
        expected_version=1,
        reason="PG 规费复核",
    )
    costs = TaxCostService(business.db)
    written = costs.add_source(
        business.manager,
        business.book["id"],
        TaxCostSourceWrite(
            kind="repair",
            source_key="REP-PG-1",
            source_version=1,
            period="2026-10",
            subject_label="V9",
            original_ref="repair-pg.pdf",
            sha256="a" * 64,
            amount="20.00",
            payload={
                "invoice_ref": "REP-PG-1",
                "vehicle_external_id": "V9",
                "coverage": "per_vehicle",
            },
        ),
    )
    batch = costs.generate(
        business.manager,
        business.book["id"],
        TaxCostGenerate(
            period="2026-10",
            kind="repair",
            source_ids=[written["id"]],
            policy=TaxCostPolicyWrite(
                version="cost-v1",
                expense_account_code="6602.03",
                credit_account_code="2202",
            ),
            reason="PG 成本批次",
        ),
    )
    reviewed = costs.review(
        business.reviewer,
        business.book["id"],
        batch["id"],
        TaxCostReview(expected_version=1, review_reason="PG 成本复核"),
    )
    assert reviewed["status"] == "reviewed"
    view = FleetMetricsService(business.db).overview(
        business.manager, business.book["id"], "2026-10"
    )
    assert view["totals"]["revenue_cents"] == 22500
    assert view["totals"]["unallocated_cost_cents"] == 2000
    assert view["totals"]["net_profit_cents"] is None
    assert sum(v["contribution_cents"] for v in view["vehicles"]) == 22500


def test_pg_fee_refund_resets_receivable_net_and_guards_reversal(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        business.reviewer,
        business.book["id"],
        batch["id"],
        expected_version=1,
        reason="PG 退款复核",
    )
    receivable = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    amount = int(receivable["amount_cents"])
    income = _bank(business, "income", amount, "BANK-PG-REFUND-IN")
    service.allocate_receivable(
        business.reviewer,
        business.book["id"],
        receivable["receivable_id"],
        bank_source_id=income,
        amount_cents=amount,
        idempotency_key="pg-refund-alloc",
    )
    expense = _bank(business, "expense", amount, "BANK-PG-REFUND-OUT")
    refund = service.refund_receivable(
        business.reviewer,
        business.book["id"],
        receivable["receivable_id"],
        bank_source_id=expense,
        amount_cents=3000,
        reason="PG 多收退回",
        idempotency_key="pg-refund-1",
    )
    assert refund["receivable_status"] == "open"
    allocation = service.list_allocations(business.reviewer, business.book["id"])["items"][0]
    with pytest.raises(AccountingError) as blocked:
        service.reverse_allocation(
            business.reviewer,
            business.book["id"],
            allocation["allocation_id"],
            "退款未撤销",
        )
    assert blocked.value.status == 409
    assert (
        service.list_bank_sources(
            business.reviewer,
            business.book["id"],
            direction="expense",
        )["items"][0]["remaining_cents"]
        == amount - 3000
    )
    with business.db.transaction(actor=business.manager) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM fee_receivable_refunds").fetchone()["c"] == 1
        assert (
            conn.execute(
                "SELECT status FROM fee_receivable_items WHERE id=?", (receivable["receivable_id"],)
            ).fetchone()["status"]
            == "open"
        )


def test_pg_fee_allocations_span_banks_and_reject_cross_period(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        business.reviewer,
        business.book["id"],
        batch["id"],
        expected_version=1,
        reason="PG 多银行复核",
    )
    receivable = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    amount = int(receivable["amount_cents"])
    bank_a = _bank(business, "income", amount, "BANK-PG-SPLIT-A")
    bank_b = _bank(business, "income", 4000, "BANK-PG-SPLIT-B")
    service.allocate_receivable(
        business.reviewer,
        business.book["id"],
        receivable["receivable_id"],
        bank_source_id=bank_a,
        amount_cents=amount - 4000,
        idempotency_key="pg-split-a",
    )
    partial = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    partial_allocations = service.list_allocations(business.reviewer, business.book["id"])["items"]
    assert partial["settlement_status"] == "unsettled"
    assert sum(int(row["amount_cents"]) for row in partial_allocations) == amount - 4000
    service.allocate_receivable(
        business.reviewer,
        business.book["id"],
        receivable["receivable_id"],
        bank_source_id=bank_b,
        amount_cents=4000,
        idempotency_key="pg-split-b",
    )
    settled = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    assert settled["settlement_status"] == "settled"
    assert len(service.list_allocations(business.reviewer, business.book["id"])["items"]) == 2

    late = _bank(business, "income", 100, "BANK-PG-LATE", date="2026-09-30")
    with pytest.raises(AccountingError) as cross_period:
        service.allocate_receivable(
            business.reviewer,
            business.book["id"],
            receivable["receivable_id"],
            bank_source_id=late,
            amount_cents=100,
            idempotency_key="pg-late",
        )
    assert cross_period.value.status == 409

    allocation = service.list_allocations(business.reviewer, business.book["id"])["items"][0]
    service.reverse_allocation(
        business.reviewer,
        business.book["id"],
        allocation["allocation_id"],
        "PG 多银行撤销",
    )
    reopened = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    active = [
        row
        for row in service.list_allocations(business.reviewer, business.book["id"])["items"]
        if not row.get("reversed_at")
    ]
    assert reopened["settlement_status"] == "unsettled"
    assert sum(int(row["amount_cents"]) for row in active) == amount - int(
        allocation["amount_cents"]
    )


def test_pg_concurrent_allocations_do_not_double_settle_receivable(case):
    """Two parallel bank allocations must serialize on the receivable row."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        business.reviewer,
        business.book["id"],
        batch["id"],
        expected_version=1,
        reason="PG 并发核销复核",
    )
    receivable = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    amount = int(receivable["amount_cents"])
    bank_a = _bank(business, "income", amount, "BANK-PG-CONCURRENT-A")
    bank_b = _bank(business, "income", amount, "BANK-PG-CONCURRENT-B")
    barrier = threading.Barrier(2)

    def attempt(bank_id, key):
        barrier.wait(timeout=10)
        try:
            service.allocate_receivable(
                business.reviewer,
                business.book["id"],
                receivable["receivable_id"],
                bank_source_id=bank_id,
                amount_cents=amount,
                idempotency_key=key,
            )
            return "ok"
        except AccountingError as error:
            return str(error.status)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: attempt(*args),
                [
                    (bank_a, "pg-concurrent-a"),
                    (bank_b, "pg-concurrent-b"),
                ],
            )
        )
    assert sorted(results) == ["409", "ok"]
    allocations = service.list_allocations(business.reviewer, business.book["id"])["items"]
    assert len(allocations) == 1
    assert sum(int(row["amount_cents"]) for row in allocations) == amount
    assert (
        service.list_receivables(business.reviewer, business.book["id"])["items"][0][
            "settlement_status"
        ]
        == "settled"
    )


def test_pg_allocation_requires_basis_for_independent_payer(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        business.reviewer,
        business.book["id"],
        batch["id"],
        expected_version=1,
        reason="PG 独立代收复核",
    )
    item = service.list_receivables(business.reviewer, business.book["id"])["items"][0]
    bank = _bank(business, "income", 5000, "BANK-PG-PAYER", counterparty="王先生")
    with pytest.raises(AccountingError) as missing:
        service.allocate_receivable(
            business.reviewer,
            business.book["id"],
            item["receivable_id"],
            bank_source_id=bank,
            amount_cents=1000,
            idempotency_key="pg-payer-no-basis",
        )
    assert missing.value.status == 409
    allocation = service.allocate_receivable(
        business.reviewer,
        business.book["id"],
        item["receivable_id"],
        bank_source_id=bank,
        amount_cents=1000,
        idempotency_key="pg-payer-with-basis",
        payer_name="王先生",
        collection_basis="王先生代驾驶员付款，附授权说明",
    )
    assert allocation["payer_name"] == "王先生"
    assert allocation["collection_basis"].startswith("王先生代驾驶员")


def test_pg_money_columns_accept_three_billion_cents(case):
    """OPT01-A: widened fee money columns must hold >32-bit integer cents."""
    business, _source = case
    bank_id = _bank(business, "expense", 3_000_000_000, "BIG-CENTS-BANK")
    import psycopg
    from uuid import uuid4

    suffix = uuid4().hex[:10]
    with psycopg.connect(ADMIN_URL) as conn:
        types = dict(
            conn.execute(
                "SELECT table_name||'.'||column_name,data_type FROM information_schema.columns "
                "WHERE table_schema='accounting' AND column_name IN ('amount_cents','bank_amount_cents') "
                "AND table_name IN ('fee_receivable_allocations','fee_receivable_refunds')"
            ).fetchall()
        )
        assert types == {
            "fee_receivable_allocations.amount_cents": "bigint",
            "fee_receivable_allocations.bank_amount_cents": "bigint",
            "fee_receivable_refunds.amount_cents": "bigint",
            "fee_receivable_refunds.bank_amount_cents": "bigint",
        }
        conn.execute(
            "INSERT INTO accounting.fee_receivable_allocations"
            "(id,book_id,firm_id,company_id,receivable_id,bank_source_id,amount_cents,"
            "bank_draft_version,bank_amount_cents,created_by,created_at,idempotency_key,request_fingerprint)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s,%s,'2026-10-10',%s,'fp')",
            (
                f"big-alloc-{suffix}",
                business.book["id"],
                business.manager.firm_id,
                business.book["company_id"],
                f"receivable-{suffix}",
                bank_id,
                3_000_000_000,
                3_000_000_000,
                business.manager.user_id,
                f"big-alloc-{suffix}",
            ),
        )
        conn.execute(
            "INSERT INTO accounting.fee_receivable_refunds"
            "(id,book_id,firm_id,company_id,receivable_id,bank_source_id,amount_cents,"
            "bank_draft_version,bank_amount_cents,reason,created_by,created_at,idempotency_key,request_fingerprint)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s,'大额退款验证',%s,'2026-10-10',%s,'fp')",
            (
                f"big-refund-{suffix}",
                business.book["id"],
                business.manager.firm_id,
                business.book["company_id"],
                f"receivable-{suffix}",
                bank_id,
                3_000_000_000,
                3_000_000_000,
                business.manager.user_id,
                f"big-refund-{suffix}",
            ),
        )
        assert (
            conn.execute(
                "SELECT amount_cents FROM accounting.fee_receivable_allocations WHERE id=%s",
                (f"big-alloc-{suffix}",),
            ).fetchone()[0]
            == 3_000_000_000
        )
