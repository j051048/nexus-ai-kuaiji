"""P02: book readers see bound formal facts without becoming ledger managers."""

import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError, Actor
from app.ledgers.models import PeriodCreate
from app.ledgers.service import LedgersService
from app.opening.models import (
    AccountCreate,
    AccountRevision,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.service import ReportService
from app.subledger.service import SubledgerService
from scripts.pg_suite_target import load_suite_target, verify_suite_target
from test_n03_notification_postgres import p0_pg, p0_case  # noqa: F401


@pytest.fixture
def formal_case(p0_case, request):
    c = p0_case
    c.reader = Actor("p02-book-reader-" + uuid4().hex, c.manager.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,'只读投影测试',%s)",
            (c.reader.user_id, c.reader.user_id + "@example.test"),
        )
        conn.execute(
            "INSERT INTO accounting.memberships VALUES(%s,%s,'reviewer')",
            (c.reader.firm_id, c.reader.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.book_access VALUES(%s,%s,%s,'reviewer')",
            (c.book["id"], c.reader.firm_id, c.reader.user_id),
        )
    c.opening = OpeningService(c.db)
    c.ledger = "ledger_" + c.book["id"]
    c.policy = c.opening.create_policy(
        c.manager,
        PolicyCreate(
            ledger_id=c.ledger,
            standard_kind="small_enterprise",
            taxpayer_kind="small_scale",
            effective_from="2026-01",
            reason="P02当前月核算政策",
        ),
    )
    c.accounts = [
        c.opening.create_account(
            c.manager,
            AccountCreate(
                ledger_id=c.ledger,
                code=code,
                name=name,
                category=kind,
                normal_balance=normal,
                effective_from="2026-01",
                reason="P02当前月正式科目",
            ),
        )
        for code, name, kind, normal in [
            ("1002", "银行存款", "asset", "debit"),
            ("4001", "实收资本", "equity", "credit"),
        ]
    ]

    def package(period):
        return c.opening.create_package(
            c.manager,
            OpeningPackageCreate(
                ledger_id=c.ledger, target_period=period, policy_id=c.policy["id"]
            ),
        )["package"]

    def version(package_id):
        return c.opening.import_version(
            c.manager,
            package_id,
            OpeningImport(
                idempotency_key=uuid4().hex,
                reason="P02非零期初",
                lines=[
                    OpeningLineInput(
                        account_id=a["id"],
                        account_version=1,
                        year_opening="100.00" if index == 0 else "-100.00",
                        ytd_debit="0.00",
                        ytd_credit="0.00",
                        opening="100.00" if index == 0 else "-100.00",
                    )
                    for index, a in enumerate(c.accounts)
                ],
            ),
        )["version"]

    c.package = package("2026-10")
    c.old_version = version(c.package["id"])
    c.version = version(c.package["id"])
    c.opening.validate_version(c.manager, c.version["id"])
    command = OpeningCommand(expected_version=c.version["version"], reason="P02独立核对期初")
    c.opening.submit_version(c.manager, c.version["id"], command)
    c.opening.approve_version(c.reviewer, c.version["id"], command)
    c.opening.freeze_version(c.reviewer, c.version["id"], command)
    c.posting = PostingService(c.db)
    c.posting.bind_opening(
        c.manager,
        OpeningBind(
            book_id=c.book["id"], package_version_id=c.version["id"], reason="P02授权账套绑定"
        ),
    )
    c.hidden_package = package("2026-11")
    c.hidden_version = version(c.hidden_package["id"])
    c.hidden_book = LedgersService(c.db).create_period(
        c.manager,
        c.book["id"],
        PeriodCreate(
            period="2026-11",
            name="未授权的下一月份",
            source_book_id=c.book["id"],
            idempotency_key=uuid4().hex,
        ),
    )["book_id"]
    c.opening.validate_version(c.manager, c.hidden_version["id"])
    hidden_command = OpeningCommand(expected_version=1, reason="下一月份独立冻结")
    c.opening.submit_version(c.manager, c.hidden_version["id"], hidden_command)
    c.opening.approve_version(c.reviewer, c.hidden_version["id"], hidden_command)
    c.opening.freeze_version(c.reviewer, c.hidden_version["id"], hidden_command)
    c.posting.bind_opening(
        c.manager,
        OpeningBind(
            book_id=c.hidden_book,
            package_version_id=c.hidden_version["id"],
            reason="下一月份独立绑定不得继承读权限",
        ),
    )
    c.future_account = c.opening.create_account(
        c.manager,
        AccountCreate(
            ledger_id=c.ledger,
            code="9998",
            name="未授权未来月份科目",
            effective_from="2027-01",
            reason="P02不得泄露未来科目",
        ),
    )
    c.opening.revise_account(
        c.manager,
        c.accounts[0]["id"],
        AccountRevision(
            ledger_id=c.ledger,
            expected_version=1,
            code=getattr(request, "param", "1002"),
            name="未来银行更名",
            category="asset",
            normal_balance="debit",
            effective_from="2026-11",
            reason="P02不得泄露未来版本",
        ),
    )
    return c


def test_ordinary_book_reviewer_reads_actual_nonzero_formal_facts(formal_case):
    c = formal_case
    with c.db.transaction(actor=c.reader) as conn:
        assert not conn.execute(
            "SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?",
            (c.ledger, c.reader.user_id),
        ).fetchone()
    expected = c.posting.trial_balance(c.manager, c.book["id"])
    actual = c.posting.trial_balance(c.reader, c.book["id"])
    assert actual == expected
    assert {row["code"]: row["closing_cents"] for row in actual["rows"]} == {
        "1002": 10000,
        "4001": -10000,
    }
    service = SubledgerService(c.db)
    for read in (service.control_reconciliation, service.bank_reconciliation):
        assert read(c.reader, c.book["id"]) == read(c.manager, c.book["id"])


def test_native_formal_projection_hides_unbound_versions_and_future_accounts(formal_case):
    c = formal_case
    with c.db.transaction(actor=c.reader) as conn:
        versions = conn.execute("SELECT id FROM opening_package_versions").fetchall()
        assert {row[0] for row in versions} == {c.version["id"]}
        assert len(conn.execute("SELECT * FROM opening_lines").fetchall()) == 2
        assert conn.execute("SELECT * FROM opening_packages").fetchall() == []
        assert conn.execute("SELECT * FROM accounting_policies").fetchall() == []
        account_versions = conn.execute(
            "SELECT account_id,version FROM ledger_account_versions"
        ).fetchall()
        assert {(row[0], row[1]) for row in account_versions} == {(a["id"], 1) for a in c.accounts}
        assert {row[0] for row in conn.execute("SELECT id FROM ledger_accounts")} == {
            a["id"] for a in c.accounts
        }
    with c.db.transaction(actor=c.foreign) as conn:
        assert (
            conn.execute(
                "SELECT * FROM opening_package_versions WHERE id=?", (c.version["id"],)
            ).fetchall()
            == []
        )
        assert (
            conn.execute(
                "SELECT * FROM ledger_account_versions WHERE ledger_id=?", (c.ledger,)
            ).fetchall()
            == []
        )
    for denied in (c.other_book["id"], c.hidden_book):
        with pytest.raises(AccountingError) as error:
            c.posting.trial_balance(c.reader, denied)
        assert error.value.status == 404


@pytest.mark.parametrize("formal_case", ["1002", "9997"], indirect=True)
def test_reports_use_authorized_period_accounts_not_future_current_version(formal_case):
    c = formal_case
    reports = ReportService(c.db)
    expected = reports.create(c.manager, c.book["id"], "2026-10")["content"]
    actual = reports.create(c.reader, c.book["id"], "2026-10")["content"]
    assert actual == expected
    assert actual["cash_flow"]["opening_cash_cents"] == 10000
    assert actual["cash_flow"]["closing_cash_cents"] == 10000


def test_formal_read_does_not_grant_writes_and_revocation_is_immediate(formal_case):
    c = formal_case
    with c.db.transaction(write=True, actor=c.reader) as conn:
        assert (
            conn.execute(
                "UPDATE ledger_accounts SET status='inactive' WHERE id=?", (c.accounts[0]["id"],)
            ).rowcount
            == 0
        )
        assert (
            conn.execute(
                "UPDATE opening_lines SET opening_cents=0 WHERE package_version_id=?",
                (c.version["id"],),
            ).rowcount
            == 0
        )
        assert (
            conn.execute(
                "UPDATE opening_package_versions SET reason='篡改冻结依据' WHERE id=?",
                (c.version["id"],),
            ).rowcount
            == 0
        )
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], c.reader.user_id),
        )
    with c.db.transaction(actor=c.reader) as conn:
        for table in (
            "opening_package_versions",
            "opening_lines",
            "ledger_accounts",
            "ledger_account_versions",
        ):
            assert conn.execute("SELECT * FROM " + table).fetchall() == []
    with pytest.raises(AccountingError) as error:
        c.posting.trial_balance(c.reader, c.book["id"])
    assert error.value.status == 404


def test_ordinary_reviewers_complete_nonzero_http_month_close_without_ledger_manager(tmp_path):
    from c01_identity_fixture import _private_write
    from p02_session_fixture import create_session
    from p02_business_fixture import call, seed_nonzero_flow, complete_close_delivery

    target = load_suite_target()
    if target is None:
        pytest.fail("P02 requires an explicit isolated PG16/17 runner")
    target = verify_suite_target(target)
    import psycopg

    with psycopg.connect(target["admin"]) as conn:
        major = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    config = tmp_path / f"pg{major}.json"
    _private_write(config, json.dumps(target).encode("utf-8"))
    try:
        with create_session(config, tmp_path / "flow") as session:
            state = seed_nonzero_flow(session.client, session.headers, session.ids)
            with psycopg.connect(target["admin"]) as conn:
                conn.execute(
                    "DELETE FROM accounting.ledger_managers WHERE ledger_id=%s AND user_id=%s",
                    ("ledger_" + state["book_id"], session.ids["users"]["reviewer"]),
                )
            for role in ("accountant", "reviewer", "customer"):
                trial = call(
                    session.client,
                    "GET",
                    f"/api/books/{state['book_id']}/trial-balance",
                    session.headers[role],
                ).json()
                assert {r["code"]: r["closing_cents"] for r in trial["rows"]} == {
                    "1002": 973190,
                    "2202": -1000000,
                    "6602.02": 26810,
                }
            complete_close_delivery(session.client, session.headers, session.ids, state)
            assert state["close_status"] == "locked" and state["report_status"] == "published"
            assert state["customer_acknowledged"] is True
    finally:
        config.unlink(missing_ok=True)
