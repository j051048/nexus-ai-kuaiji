"""TAX10: real PostgreSQL proof for repair cost batches."""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService
from app.tax.costs import (
    TaxCostGenerate,
    TaxCostPolicyWrite,
    TaxCostReview,
    TaxCostService,
    TaxCostSourceWrite,
)
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
pytestmark = []

POLICY = TaxCostPolicyWrite(version="cost-v1", expense_account_code="6602.03", credit_account_code="2202")


@pytest.fixture(scope="module")
def pg():
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("TAX10 PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database
    database.close()


@pytest.fixture
def case(pg):
    import psycopg

    db = pg
    suffix = uuid4().hex[:12]
    firm = "tax10-firm-" + suffix
    maker = Actor(user_id=f"tax10-maker-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"tax10-reviewer-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((maker, "owner"), (reviewer, "reviewer")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"tax10-{role}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构TAX10事务所", maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'reviewer')",
            (firm, reviewer.user_id),
        )
    book = AccountingService(db).create_book(maker, BookCreate(name="虚构TAX10账套", period="2026-10"))
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    return SimpleNamespace(db=db, manager=maker, reviewer=reviewer, book=book)


def _repair(case, key="FP-PG-1", version=1):
    return TaxCostService(case.db).add_source(
        case.manager,
        case.book["id"],
        TaxCostSourceWrite(
            kind="repair", source_key=key, source_version=version, period="2026-10",
            subject_label="京A1", original_ref="原件/维修-PG.pdf", sha256="a" * 64,
            amount="100.00",
            payload={
                "invoice_ref": key, "vehicle_external_id": "V1", "coverage": "per_vehicle",
                "rate_cents": 5000, "quantity_milli": 2000,
            },
        ),
    )


def test_pg_repair_batch_is_balanced_and_independently_reviewed(case):
    source = _repair(case)
    service = TaxCostService(case.db)
    batch = service.generate(
        case.manager,
        case.book["id"],
        TaxCostGenerate(
            period="2026-10", kind="repair", source_ids=[source["id"]],
            policy=POLICY, reason="PG 核定维修批次",
        ),
    )
    draft = AccountingService(case.db).get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert batch["amount_cents"] == 10000
    assert len(draft["lines"]) == 2 and draft["issues"] == []
    reviewed = service.review(
        case.reviewer, case.book["id"], batch["id"],
        TaxCostReview(expected_version=1, review_reason="PG 独立复核"),
    )
    assert reviewed["status"] == "reviewed" and reviewed["draft_status"] == "approved"
    approved = AccountingService(case.db).get_draft(case.manager, case.book["id"], batch["draft_id"])
    assert approved["status"] == "approved"
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) c FROM tax_cost_batches").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM tax_cost_batch_rows").fetchone()["c"] == 1


def test_pg_source_upgrade_and_outsider_are_rejected(case):
    import psycopg

    source = _repair(case)
    batch = TaxCostService(case.db).generate(
        case.manager, case.book["id"],
        TaxCostGenerate(period="2026-10", kind="repair", source_ids=[source["id"]],
                        policy=POLICY, reason="PG 来源升级批次"),
    )
    _repair(case, version=2)
    with pytest.raises(AccountingError) as stale:
        TaxCostService(case.db).review(
            case.reviewer, case.book["id"], batch["id"],
            TaxCostReview(expected_version=1, review_reason="来源升级后复核"),
        )
    assert stale.value.status == 409
    suffix = uuid4().hex[:12]
    outsider = Actor(user_id=f"tax10-outside-{suffix}", firm_id=f"tax10-other-{suffix}")
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (outsider.user_id, "outside", f"tax10-outside-{suffix}@example.test"),
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
        TaxCostService(case.db).get_batch(outsider, case.book["id"], batch["id"])
    assert hidden.value.status == 404
