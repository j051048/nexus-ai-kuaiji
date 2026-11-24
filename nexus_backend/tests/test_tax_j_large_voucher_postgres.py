"""TAX05: 516-line controlled voucher on real PostgreSQL."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.models import ApprovalRequest, BookCreate, DraftUpdate, ImportRequest, SourceRow
from app.accounting.service import AccountingService, Actor
from scripts.pg_suite_target import load_suite_target, verify_suite_target

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
        pytest.skip("TAX J PG requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"])
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def test_516_line_voucher_balances_and_independent_review_on_pg(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "taxj-firm-" + suffix
    maker = Actor(user_id=f"taxj-maker-{suffix}", firm_id=firm)
    reviewer = Actor(user_id=f"taxj-reviewer-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        for actor, role in ((maker, "owner"), (reviewer, "reviewer")):
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, role, f"taxj-{role}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构TAXJ事务所", maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'reviewer')",
            (firm, reviewer.user_id),
        )
    service = AccountingService(db)
    book = service.create_book(maker, BookCreate(name="虚构TAXJ账套", period="2026-10"))
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
            (book["id"], firm, reviewer.user_id),
        )
    key = "taxj-516"
    service.import_sources(maker, book["id"], ImportRequest(source_name=key, rows=[SourceRow(
        external_id=key, date="2026-10-01", description="受控大凭证", counterparty="合成货车队",
        amount="25800.00", direction="expense", evidence_ref="合成证据",
    )]))
    assert service.suggestions(maker, book["id"])["created"] == 1
    draft = service.get_book(maker, book["id"])["drafts"][0]
    debit = [{"account_code": "6602.02", "debit": _money(10000), "credit": "0"} for _ in range(258)]
    credit = [{"account_code": "1002", "debit": "0", "credit": _money(10000)} for _ in range(258)]
    updated = service.update_draft(maker, book["id"], draft["id"], DraftUpdate(
        expected_version=draft["version"], description="516 行受控大凭证", lines=debit + credit,
    ))
    assert len(updated["lines"]) == 516
    assert updated["issues"] == []
    approved = service.approve(reviewer, book["id"], updated["id"], ApprovalRequest(expected_version=updated["version"]))
    assert approved["status"] == "approved"
    with psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute("SELECT count(*) FROM accounting.draft_lines WHERE draft_id=%s", (draft["id"],)).fetchone()
        assert row[0] == 516
