"""税务底稿：数据库守卫、来源失效、非正式账基准与不支持场景的诚实阻断。"""

import sqlite3

import pytest

from app.accounting.domain import AccountingError
from test_business import business  # noqa: F401  (pytest fixture re-export)
from test_tax_workpaper import invoices, profile, row, tax  # noqa: F401


def test_database_refuses_inserting_a_reviewed_workpaper(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    with pytest.raises(sqlite3.IntegrityError):
        with tax.db.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO tax_workpapers(id,firm_id,company_id,book_id,period,version,status,"
                "profile_id,output_net_cents,output_tax_cents,input_net_cents,input_tax_cents,"
                "tax_cents,surtax_cents,credit_carry_cents,ledger_cents,variance_cents,"
                "variance_reason,lines_json,source_digest,created_by,created_at,reviewed_by,"
                "reviewed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "forged",
                    tax.actor_accountant.firm_id,
                    tax.book["company_id"],
                    tax.book["id"],
                    "2026-10",
                    paper["version"] + 1,
                    "reviewed",
                    paper["profile_id"],
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    "",
                    "[]",
                    "0" * 64,
                    tax.actor_accountant.user_id,
                    "2026-10-31T00:00:00+00:00",
                    tax.manager.user_id,
                    "2026-10-31T00:00:00+00:00",
                ),
            )


def test_database_refuses_rewriting_reviewed_amounts(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    tax.tax.review(tax.manager, tax.book["id"], paper["id"], "核对无误", paper["version"])
    with pytest.raises(sqlite3.IntegrityError):
        with tax.db.transaction(write=True) as conn:
            conn.execute("UPDATE tax_workpapers SET tax_cents=1 WHERE id=?", (paper["id"],))


def test_database_refuses_deleting_a_workpaper(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    with pytest.raises(sqlite3.IntegrityError):
        with tax.db.transaction(write=True) as conn:
            conn.execute("DELETE FROM tax_workpapers WHERE id=?", (paper["id"],))


def test_review_refuses_a_stale_source_snapshot(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    invoices(tax, [row("output", 5_000, 150, day="09")], tag="2")
    with pytest.raises(AccountingError) as error:
        tax.tax.review(tax.manager, tax.book["id"], paper["id"], "核对无误", paper["version"])
    assert error.value.status == 409
    assert "来源" in error.value.detail


def test_generate_refuses_unsupported_rows_instead_of_dropping_them(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    red = {**row("output", 100_000, 3_000), "red": True}
    invoices(tax, [row("output", 100_000, 3_000), red])
    with pytest.raises(AccountingError) as error:
        tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert error.value.status == 409
    assert "红字" in error.value.detail


def test_review_requires_book_review_capability(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    with pytest.raises(AccountingError) as error:
        tax.tax.review(tax.actors["employee"], tax.book["id"], paper["id"], "核对无误", 1)
    assert error.value.status in (403, 404)
