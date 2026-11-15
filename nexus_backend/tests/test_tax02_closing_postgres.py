"""TAX02: real PostgreSQL proof that both directions and auxiliary dimensions close.

The administrator bootstraps synthetic identities; all business writes use the
runtime application role through services. This is a service-level PG test, not
a production claim.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.accounting.directory import DirectoryService
from app.periodic.models import ClosingGenerate
from app.posting.service import PostingService
from test_posting_service import admin, frozen_opening
from test_b04_pilot import setup_b03
from test_tax02_closing import _account, _approve_and_post, _closing, _line, _post

ADMIN_URL = __import__("os").getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = __import__("os").getenv("ACCOUNTING_TEST_DATABASE_URL")

pytestmark = []


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate
    from scripts.pg_suite_target import load_suite_target, verify_suite_target

    target = load_suite_target()
    if target is None:
        pytest.skip("TAX02 PG requires an explicit isolated PostgreSQL runner")
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
    firm = "tax02-firm-" + suffix
    maker = Actor(user_id=f"tax02-maker-{suffix}", firm_id=firm)
    with psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
            (maker.user_id, "税二制单", f"tax02-{suffix}@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构TAX02事务所", maker.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'owner')",
            (firm, maker.user_id),
        )
    service = AccountingService(db)
    book = service.create_book(maker, BookCreate(name="虚构TAX02账套", period="2026-10"))
    directory = DirectoryService(db)
    result = SimpleNamespace(
        db=db,
        psycopg=psycopg,
        manager=maker,
        book=book,
        directory=directory,
        actors={"manager": maker},
        firm=firm,
    )
    return result


def _opening(case):
    import test_posting_service

    def ledger_of(c):
        with c.db.transaction(actor=c.manager) as conn:
            return conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)
            ).fetchone()["ledger_id"]

    old = test_posting_service.ledger_of
    test_posting_service.ledger_of = ledger_of
    try:
        return setup_b03(case)
    finally:
        test_posting_service.ledger_of = old


def test_pg_closing_includes_both_balances_and_auxiliary_dimension(case):
    opening, posting = _opening(case)
    income = _account(case, "500101", "主营业务收入", "income", "credit")
    profit = _account(case, "3103", "本年利润", "equity", "credit")
    _post(
        case,
        posting,
        [
            _line(opening["bank"], debit="100.00"),
            _line(income, credit="100.00", aux={"project": "A"}),
        ],
        "tax02-pg-income",
    )
    _post(
        case,
        posting,
        [
            _line(opening["bank"], debit="10.00", aux={"project": "A"}),
            _line(opening["expense"], credit="10.00", aux={"project": "A"}),
        ],
        "tax02-pg-expense",
    )
    draft = _closing(case, income, opening["expense"], profit)
    assert draft["draft"]["status"] == "draft"
    lines = draft["lines"]
    profit_line = next(line for line in lines if line["account_id"] == profit["id"])
    assert profit_line["credit_cents"] - profit_line["debit_cents"] == 11000
    income_line = next(line for line in lines if line["account_id"] == income["id"])
    assert income_line["debit_cents"] == 10000
    expense_line = next(line for line in lines if line["account_id"] == opening["expense"]["id"])
    assert expense_line["debit_cents"] == 1000
    import json

    assert json.loads(income_line["aux_json"]).get("project") == "A"
    assert json.loads(expense_line["aux_json"]).get("project") == "A"

    posted = _approve_and_post(case, draft)
    assert posted["draft"]["status"] == "posted"
    with case.db.transaction(actor=case.manager) as conn:
        rows = conn.execute(
            "SELECT l.account_id,SUM(l.debit_cents) AS debit,SUM(l.credit_cents) AS credit "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? GROUP BY l.account_id",
            (case.book["id"],),
        ).fetchall()
    balances = {row["account_id"]: int(row["debit"]) - int(row["credit"]) for row in rows}
    assert balances[income["id"]] == 0
    assert balances[opening["expense"]["id"]] == 0
    assert balances[profit["id"]] == -11000


def test_pg_closing_revalidates_source_after_draft(case):
    opening, posting = _opening(case)
    income = _account(case, "500102", "其他业务收入", "income", "credit")
    profit = _account(case, "3104", "本年利润B", "equity", "credit")
    _post(
        case,
        posting,
        [_line(opening["bank"], debit="50.00"), _line(income, credit="50.00")],
        "tax02-pg-source",
    )
    draft = _closing(case, income, opening["expense"], profit)
    _post(
        case,
        posting,
        [_line(opening["bank"], debit="5.00"), _line(opening["expense"], credit="5.00")],
        "tax02-pg-late",
    )
    service = __import__("app.periodic.service", fromlist=["PeriodicService"]).PeriodicService(case.db)
    from app.periodic.models import DraftCommand
    from app.accounting.domain import AccountingError

    with pytest.raises(AccountingError) as error:
        service.approve(
            admin(case),
            draft["draft"]["id"],
            DraftCommand(expected_version=draft["draft"]["version"], reason="过期草稿"),
        )
    assert error.value.status == 409
