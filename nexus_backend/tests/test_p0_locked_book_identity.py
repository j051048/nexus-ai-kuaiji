"""F03 closed and formally bound book identities cannot be reassigned."""

import sqlite3

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.ledgers.models import PeriodCreate, PeriodLink
from app.ledgers.service import LedgersService
from test_business import business
from test_p0_close_dependencies import close_case, command


def other_same_company_ledger(case):
    target = case.ledger.create_book(
        case.manager,
        BookCreate(
            name="虚构企业另一长期账套",
            period="2026-11",
            company_id=case.company["id"],
        ),
    )
    return target


@pytest.mark.parametrize("locked", [False, True])
def test_formally_bound_or_locked_period_cannot_be_relinked(close_case, locked):
    case = close_case
    if locked:
        case.close.review(case.reviewer, case.run["close"]["id"], command())
        case.close.lock(case.reviewer, case.run["close"]["id"], command())
    target = other_same_company_ledger(case)
    with pytest.raises(AccountingError) as rejected:
        LedgersService(case.db).link_period(
            case.manager,
            target["id"],
            PeriodLink(
                source_book_id=case.book["id"],
                expected_ledger_id=case.opening["ledger_id"],
                reason="拒绝改绑已有正式依据的期间",
            ),
        )
    assert rejected.value.status == 409
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]
            == case.opening["ledger_id"]
        )


def test_new_period_can_be_created_in_same_ledger_after_prior_month_lock(close_case):
    case = close_case
    case.close.review(case.reviewer, case.run["close"]["id"], command())
    case.close.lock(case.reviewer, case.run["close"]["id"], command())
    result = LedgersService(case.db).create_period(
        case.manager,
        case.book["id"],
        PeriodCreate(
            period="2026-11",
            name="虚构次月正常期间",
            source_book_id=case.book["id"],
            idempotency_key="p0-next-month-after-lock",
        ),
    )
    assert result["book_id"] != case.book["id"]
    assert result["ledger_id"] == case.opening["ledger_id"]


@pytest.mark.parametrize("field", ["period", "company_id", "firm_id"])
def test_locked_book_scope_identity_is_frozen(close_case, field):
    case = close_case
    case.close.review(case.reviewer, case.run["close"]["id"], command())
    case.close.lock(case.reviewer, case.run["close"]["id"], command())
    replacement = {"period": "2026-11", "company_id": case.other["id"], "firm_id": "p0-other-firm"}[
        field
    ]
    with pytest.raises(sqlite3.IntegrityError, match="Locked book identity"):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(f"UPDATE books SET {field}=? WHERE id=?", (replacement, case.book["id"]))


def test_locked_book_name_metadata_remains_editable(close_case):
    case = close_case
    case.close.review(case.reviewer, case.run["close"]["id"], command())
    case.close.lock(case.reviewer, case.run["close"]["id"], command())
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute("UPDATE books SET name='虚构账套显示名称调整' WHERE id=?", (case.book["id"],))
        assert (
            conn.execute("SELECT name FROM books WHERE id=?", (case.book["id"],)).fetchone()[0]
            == "虚构账套显示名称调整"
        )
