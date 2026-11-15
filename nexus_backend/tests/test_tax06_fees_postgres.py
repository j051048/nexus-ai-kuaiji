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
from test_tax06_fees import COLS, POLICY, ROWS, _masters
from test_tax04_sources import xlsx

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = []


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
    book = AccountingService(db).create_book(maker, BookCreate(name="虚构TAX06账套", period="2026-10"))
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    result = SimpleNamespace(db=db, ledger=LedgersService(db), manager=maker,
                             reviewer=reviewer, book=book, actors={"accountant": reviewer})
    _driver, _vehicle, driver2, vehicle2 = _masters(result)
    LedgersService(db).create_transport_assignment(
        maker, book["id"], TransportAssignmentCreate(
            vehicle_id=vehicle2["id"], driver_id=driver2["id"], shift="full",
            contract_external_id="C1", valid_from="2026-10-05", valid_to="2026-10-05",
        ),
    )
    source = TaxSourceAdapter(db).import_source(
        maker, book["id"], filename="规费.xlsx", data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账", external_id="S-FEE-PG", version=1,
        sheet_name="运输明细", column_map=COLS, rule_version="tax-source-v1",
    )
    return result, source


def test_pg_fee_batch_balanced_and_independently_reviewed(case):
    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(business.manager, business.book["id"], period="2026-10",
                             source_file_ids=[source["id"]], policy=POLICY)
    assert batch["total_cents"] == 22500
    draft = AccountingService(business.db).get_draft(business.manager, business.book["id"], batch["draft_id"])
    assert len(draft["lines"]) == 6 and draft["issues"] == []
    reviewed = service.review(business.reviewer, business.book["id"], batch["id"],
                              expected_version=1, reason="PG 独立复核")
    assert reviewed["status"] == "reviewed" and reviewed["draft"]["status"] == "approved"
    with business.db.transaction(actor=business.manager) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM tax_fee_batches").fetchone()["c"] >= 1
        assert conn.execute("SELECT COUNT(*) c FROM draft_line_sources").fetchone()["c"] == 6


def test_pg_fee_batch_rejects_maker_review_and_source_upgrade(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(business.manager, business.book["id"], period="2026-10",
                             source_file_ids=[source["id"]], policy=POLICY)
    with pytest.raises(AccountingError) as maker_error:
        service.review(business.manager, business.book["id"], batch["id"], expected_version=1, reason="自审")
    assert maker_error.value.status == 409
    TaxSourceAdapter(business.db).import_source(
        business.manager, business.book["id"], filename="规费.xlsx", data=xlsx({"运输明细": ROWS}),
        source_system="客户运输台账", external_id="S-FEE-PG", version=2,
        sheet_name="运输明细", column_map=COLS, rule_version="tax-source-v1",
    )
    with pytest.raises(AccountingError) as stale:
        service.review(business.reviewer, business.book["id"], batch["id"], expected_version=1, reason="来源升级后复核")
    assert stale.value.status == 409
    with pytest.raises(AccountingError) as validate:
        service.validate(business.reviewer, business.book["id"], batch["id"])
    assert validate.value.status == 409


def test_pg_fee_batch_rls_hides_other_firm(case):
    from app.accounting.domain import AccountingError

    business, source = case
    service = TaxFeeService(business.db)
    batch = service.generate(business.manager, business.book["id"], period="2026-10",
                             source_file_ids=[source["id"]], policy=POLICY)
    suffix = uuid4().hex[:12]
    outsider = Actor(user_id=f"tax06-outsider-{suffix}", firm_id=f"tax06-other-{suffix}")
    import psycopg
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                     (outsider.user_id, "outside", f"outside-{suffix}@example.test"))
        conn.execute("INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
                     (outsider.firm_id, "外部虚构事务所", outsider.user_id))
        conn.execute("INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
                     (outsider.firm_id, outsider.user_id))
    with pytest.raises(AccountingError) as hidden:
        service.get(outsider, business.book["id"], batch["id"])
    assert hidden.value.status == 404


def test_pg_fleet_metrics_separates_unallocated_cost_and_unknown_profit(case):
    from app.reports.fleet import FleetMetricsService
    from app.tax.costs import TaxCostGenerate, TaxCostPolicyWrite, TaxCostReview, TaxCostService, TaxCostSourceWrite

    business, source = case
    fee = TaxFeeService(business.db).generate(
        business.manager, business.book["id"], period="2026-10",
        source_file_ids=[source["id"]], policy=POLICY,
    )
    TaxFeeService(business.db).review(
        business.reviewer, business.book["id"], fee["id"], expected_version=1, reason="PG 规费复核",
    )
    costs = TaxCostService(business.db)
    written = costs.add_source(
        business.manager, business.book["id"],
        TaxCostSourceWrite(
            kind="repair", source_key="REP-PG-1", source_version=1, period="2026-10",
            subject_label="V9", original_ref="repair-pg.pdf", sha256="a" * 64, amount="20.00",
            payload={"invoice_ref": "REP-PG-1", "vehicle_external_id": "V9", "coverage": "per_vehicle"},
        ),
    )
    batch = costs.generate(
        business.manager, business.book["id"],
        TaxCostGenerate(
            period="2026-10", kind="repair", source_ids=[written["id"]],
            policy=TaxCostPolicyWrite(
                version="cost-v1", expense_account_code="6602.03", credit_account_code="2202",
            ),
            reason="PG 成本批次",
        ),
    )
    reviewed = costs.review(
        business.reviewer, business.book["id"], batch["id"],
        TaxCostReview(expected_version=1, review_reason="PG 成本复核"),
    )
    assert reviewed["status"] == "reviewed"
    view = FleetMetricsService(business.db).overview(business.manager, business.book["id"], "2026-10")
    assert view["totals"]["revenue_cents"] == 22500
    assert view["totals"]["unallocated_cost_cents"] == 2000
    assert view["totals"]["net_profit_cents"] is None
    assert sum(v["contribution_cents"] for v in view["vehicles"]) == 22500
