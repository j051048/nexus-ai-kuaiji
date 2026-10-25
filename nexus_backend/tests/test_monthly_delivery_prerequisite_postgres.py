"""Real PG coverage of the monthly delivery applicability prerequisite.

Use the same explicit disposable-loopback guard as monthly delivery, never a
deployment DSN. The replacement-reference checks run through the application
role and retain the existing company, actor, and foreign-key guards.
"""

from uuid import uuid4

import pytest

from app.opening.models import (
    AccountCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.subledger.models import ControlApplicability
from app.subledger.service import SubledgerService
from test_monthly_delivery_postgres import p0_case, p0_pg  # noqa: F401


@pytest.fixture
def declaration_case(p0_case):
    c = p0_case
    with c.db.transaction(actor=c.manager) as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
    opening = OpeningService(c.db)
    policy = opening.create_policy(
        c.manager,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            effective_from="2026-01",
            reason="本机交付前置零余额政策",
        ),
    )
    accounts = [
        opening.create_account(
            c.manager,
            AccountCreate(
                ledger_id=ledger_id,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from="2026-01",
                reason="本机交付前置科目",
            ),
        )
        for code, name, category, normal in (
            ("1002", "银行存款", "asset", "debit"),
            ("2202", "应付账款", "liability", "credit"),
        )
    ]
    package = opening.create_package(
        c.manager,
        OpeningPackageCreate(ledger_id=ledger_id, target_period="2026-10", policy_id=policy["id"]),
    )
    imported = opening.import_version(
        c.manager,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="monthly-prerequisite-" + uuid4().hex,
            reason="真实零余额期初，不伪造锁期",
            lines=[
                OpeningLineInput(
                    account_id=account["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="0.00",
                    ytd_credit="0.00",
                    opening="0.00",
                )
                for account in accounts
            ],
        ),
    )
    version_id = imported["version"]["id"]
    opening.validate_version(c.manager, version_id)
    command = OpeningCommand(expected_version=1, reason="本机交付前置独立核准")
    opening.submit_version(c.manager, version_id, command)
    opening.approve_version(c.reviewer, version_id, command)
    opening.freeze_version(c.reviewer, version_id, command)
    PostingService(c.db).bind_opening(
        c.manager,
        OpeningBind(book_id=c.book["id"], package_version_id=version_id, reason="本机绑定零期初"),
    )
    return c, SubledgerService(c.db)


def _declare(c, service):
    return service.set_control_applicability(
        c.manager,
        ControlApplicability(
            book_id=c.book["id"],
            status="not_applicable",
            effective_from="2026-10",
            reason="已核验零余额，当前没有子账事项",
        ),
    )


def _insert_native(c, row):
    with c.db.transaction(
        write=True, actor=c.manager, company_id=row["company_id"], write_scope="company"
    ) as conn:
        conn.execute(
            "INSERT INTO subledger_control_applicability("
            + ",".join(row)
            + ") VALUES("
            + ",".join("?" for _ in row)
            + ")",
            tuple(row.values()),
        )


def test_pg_first_not_applicable_declaration_is_accepted_with_no_supersedes(declaration_case):
    c, service = declaration_case
    row = _declare(c, service)
    assert row["status"] == "not_applicable"
    assert row["supersedes_id"] is None
    assert row["confirmed_by"] == c.manager.user_id
    assert row["firm_id"] == c.manager.firm_id
    assert row["company_id"] == c.book["company_id"]
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT revision FROM close_dependency_revisions WHERE scope_kind='ledger' AND scope_id=?",
                (row["ledger_id"],),
            ).fetchone()[0]
            > 0
        )


def test_pg_native_replacement_reference_accepts_same_ledger(declaration_case):
    c, service = declaration_case
    previous = _declare(c, service)
    replacement = dict(previous)
    replacement.update(
        id=uuid4().hex,
        effective_from="2026-11",
        supersedes_id=previous["id"],
        reason="同一企业同一长期账套的下一期声明",
    )
    _insert_native(c, replacement)
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT supersedes_id FROM subledger_control_applicability WHERE id=?",
                (replacement["id"],),
            ).fetchone()[0]
            == previous["id"]
        )


def test_pg_native_replacement_reference_rejects_other_company_ledger(declaration_case):
    c, service = declaration_case
    previous = _declare(c, service)
    with c.db.transaction(actor=c.manager) as conn:
        other_ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.other_book["id"],)
        ).fetchone()[0]
    forged = dict(previous)
    forged.update(
        id=uuid4().hex,
        ledger_id=other_ledger,
        company_id=c.other_book["company_id"],
        effective_from="2026-11",
        supersedes_id=previous["id"],
        reason="负向用例：试图引用另一个企业的历史声明",
    )
    with pytest.raises(c.psycopg.errors.CheckViolation, match="Supersedes reference crosses"):
        _insert_native(c, forged)
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT id FROM subledger_control_applicability WHERE id=?", (forged["id"],)
            ).fetchall()
            == []
        )
