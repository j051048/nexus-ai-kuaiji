"""Read/export contracts reuse formal posting, never provisional draft balances."""

import csv
import io

import pytest

from app.accounting.domain import AccountingError
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


@pytest.fixture
def formal(business):
    case = business
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"], package_version_id=opening["version_id"], reason="展示冻结期初"
        ),
    )
    voucher = posting.post_manual(
        case.manager,
        ManualVoucher(
            command_key="formal-display-001",
            book_id=case.book["id"],
            voucher_date="2026-10-16",
            description="=危险公式与中文说明",
            lines=[
                ManualLine(
                    account_id=opening["expense"]["id"],
                    account_version=1,
                    debit="0.30",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"], account_version=1, debit="0.00", credit="0.30"
                ),
            ],
        ),
    )
    return case, posting, opening, voucher


def test_read_models_include_scope_and_real_voucher_identity(formal):
    case, posting, opening, voucher = formal
    scope = dict(
        book_id=case.book["id"],
        firm_id=case.manager.firm_id,
        company_id=case.company["id"],
        period="2026-10",
    )
    assert posting.trial_balance(case.manager, case.book["id"])["scope"] == scope
    ledger = posting.account_ledger(case.manager, case.book["id"], opening["bank"]["id"])
    assert ledger["scope"] == scope
    assert ledger["entries"][0]["voucher_id"] == voucher["voucher"]["id"]
    assert (
        posting.daily_journal(case.manager, case.book["id"])[0]["voucher_id"]
        == voucher["voucher"]["id"]
    )
    assert (
        posting.aux_ledger(case.manager, case.book["id"], {"project": "missing"})["scope"] == scope
    )


def test_account_from_another_ledger_is_not_an_empty_valid_ledger(formal):
    case, posting, _, _ = formal
    with pytest.raises(AccountingError) as caught:
        posting.account_ledger(case.manager, case.book["id"], "foreign-account")
    assert caught.value.status == 404


def test_export_uses_formal_cents_and_escapes_spreadsheet_formulas(formal):
    from app.posting.presentation import export_formal_book

    case, posting, opening, _ = formal
    content = export_formal_book(
        posting, case.manager, case.book["id"], "account_ledger", account_id=opening["bank"]["id"]
    )
    rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
    assert any("0.30" in row for row in rows)
    assert any("9999.70" in row for row in rows)
    assert any("'=危险公式与中文说明" in row for row in rows)
    assert not any(cell.startswith("=危险") for row in rows for cell in row)


@pytest.mark.parametrize("actor", ["finance", "employee"])
def test_export_rechecks_review_permission_not_just_read(formal, actor):
    from app.posting.presentation import export_formal_book

    case, posting, _, _ = formal
    with pytest.raises(AccountingError) as caught:
        export_formal_book(posting, case.actors[actor], case.book["id"], "trial_balance")
    assert caught.value.status in (403, 404)


def test_export_does_not_create_delivery_or_mutate_formal_vouchers(formal):
    from app.posting.presentation import export_formal_book

    case, posting, _, voucher = formal
    before = posting.voucher_detail(case.manager, voucher["voucher"]["id"])
    export_formal_book(posting, case.manager, case.book["id"], "trial_balance")
    assert posting.voucher_detail(case.manager, voucher["voucher"]["id"]) == before
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0


def test_filtered_export_matches_visible_as_of_balance(formal):
    from app.posting.presentation import export_formal_book

    case, posting, opening, _ = formal
    content = export_formal_book(
        posting,
        case.manager,
        case.book["id"],
        "account_ledger",
        account_id=opening["bank"]["id"],
        as_of="2026-10-15",
    )
    assert "10000.00" in content and "9999.70" not in content


@pytest.mark.parametrize("as_of", ["2026-09-30", "2026-11-01", "2026-10-99", "nonsense"])
def test_read_date_must_be_inside_bound_accounting_period(formal, as_of):
    case, posting, opening, _ = formal
    with pytest.raises(AccountingError):
        posting.trial_balance(case.manager, case.book["id"], as_of)
    with pytest.raises(AccountingError):
        posting.account_ledger(case.manager, case.book["id"], opening["bank"]["id"], as_of)
