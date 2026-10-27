"""Q-S02: continuity has to survive the window the caller asked for.

Two failure modes motivate this file:

* a range that starts after a broken month cannot look complete just because the
  broken boundary sits outside the window, and
* a balanced opening package may move value between two auxiliary objects of the
  same account, so continuity has to be per account *and* auxiliary object.
"""

import pytest

import app.posting.presentation as presentation
from app.accounting.domain import AccountingError
from app.opening.models import AccountRevision
from app.opening.service import OpeningService
from app.posting.presentation import export_range_trial_balance
from period_fixtures import restore_period_state
from test_business import business
from test_posting_service import ledger_of
from test_q03_continuous_books import bound, post, second_month, two_months

ACCOUNT = (
    "SELECT a.id FROM ledger_accounts a JOIN ledger_account_versions v "
    "ON v.account_id=a.id AND v.version=a.current_version "
    "WHERE a.ledger_id=? AND v.code=?"
)


def close(case, *book_ids):
    for book_id in book_ids:
        restore_period_state(case.db, book_id, "closed")


def broken_boundary(case):
    """October closes at 10000.30 but November is opened at 10000.00."""

    posting, _ = bound(case)
    post(case, posting, case.book["id"], "qs02-oct-001", "2026-10-15", "0.30", "0.30")
    book = second_month(
        case,
        "2026-11",
        [("1002", "10000.00"), ("2202", "-10000.00")],
        key="qs02-nov-opening",
    )
    close(case, case.book["id"], book["id"])
    return posting, book


def test_range_window_does_not_hide_a_broken_prior_month(business):
    posting, _ = broken_boundary(business)
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-11", "2026-11"
    )
    assert result["complete"] is False
    assert [item["period"] for item in result["coverage"]] == ["2026-11"]
    assert {(blocker["code"], blocker["period"]) for blocker in result["blockers"]} == {
        ("opening_mismatch", "2026-11")
    }


def test_range_with_a_matching_prior_month_stays_complete(business):
    posting, _, book = two_months(business)
    close(business, business.book["id"], book["id"])
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-11", "2026-11"
    )
    assert result["blockers"] == []
    assert result["complete"] is True


def test_range_detects_an_auxiliary_identity_swap_with_equal_account_totals(business):
    posting, _ = bound(business)
    post(business, posting, business.book["id"], "qs02-oct-001", "2026-10-15", "0.30", "0.30")
    book = second_month(
        business,
        "2026-11",
        [
            ("1002", "10000.30", {"counterparty": "乙户"}),
            ("2202", "-10000.30"),
        ],
        key="qs02-nov-opening",
    )
    close(business, business.book["id"], book["id"])
    result = posting.range_trial_balance(
        business.manager, ledger_of(business), "2026-10", "2026-11"
    )
    rows = {row["code"]: row for row in result["rows"]}
    # The account-level total is untouched; only the auxiliary identity moved.
    assert rows["1002"]["closing_cents"] == 1000030
    assert result["complete"] is False
    assert any(blocker["code"] == "opening_mismatch" for blocker in result["blockers"])


def test_range_export_rejects_an_account_revision_during_render(business, monkeypatch):
    posting, _, book = two_months(business)
    close(business, business.book["id"], book["id"])
    ledger_id = ledger_of(business)
    with business.db.transaction() as conn:
        account_id = conn.execute(ACCOUNT, (ledger_id, "1002")).fetchone()["id"]
    opening = OpeningService(business.db)
    render = presentation._render_csv

    def racing_render(header_rows, rows):
        opening.revise_account(
            business.manager,
            account_id,
            AccountRevision(
                ledger_id=ledger_id,
                code="1002",
                name="银行存款（改名）",
                effective_from="2026-11",
                reason="Q-S02 渲染期间改名",
                expected_version=1,
            ),
        )
        return render(header_rows, rows)

    monkeypatch.setattr(presentation, "_render_csv", racing_render)
    with pytest.raises(AccountingError) as conflict:
        export_range_trial_balance(posting, business.manager, ledger_id, "2026-10", "2026-11")
    assert conflict.value.status == 409
    assert "已变化" in conflict.value.detail
