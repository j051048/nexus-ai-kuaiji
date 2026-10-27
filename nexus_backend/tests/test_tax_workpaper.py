"""增值税底稿：税率必须来自企业档案、差异必须解释、制单人不得复核。"""

import json

import pytest

from app.accounting.domain import AccountingError
from app.tax.workpaper import TaxWorkpaperService
from test_business import business  # noqa: F401  (pytest fixture re-export)


@pytest.fixture
def tax(business):
    business.tax = TaxWorkpaperService(business.db)
    business.actor_accountant = business.actors["accountant"]
    return business


def profile(**changes):
    return {
        "effective_from": "2026-01",
        "taxpayer_type": "small_scale",
        "vat_rate_bp": 300,
        "surtax_rate_bp": 1200,
        "filing_cycle": "monthly",
        "reason": "小规模纳税人 3% 征收率，附加税按增值税 12% 计提",
        **changes,
    }


def invoices(case, rows, book=None, tag="1"):
    book = book or case.book
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO invoice_import_batches(id,book_id,firm_id,company_id,filename,storage_key,"
            "sha256,size_bytes,row_count,direction,rows_json,notes_json,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"batch-{tag}",
                book["id"],
                case.manager.firm_id,
                book.get("company_id") or case.company["id"],
                "2026-10.xlsx",
                f"invoice/{tag}.xlsx",
                "a" * 64,
                1024,
                len(rows),
                "mixed",
                json.dumps(rows, ensure_ascii=False),
                "[]",
                "finance",
                "2026-10-05T00:00:00+00:00",
            ),
        )


def row(direction, amount, tax, day="05"):
    return {
        "date": f"2026-10-{day}",
        "direction": direction,
        "amount_cents": amount,
        "tax_cents": tax,
        "total_cents": amount + tax,
        "counterparty": "客户甲" if direction == "output" else "供应商乙",
    }


def test_generate_without_profile_refuses_instead_of_guessing(tax):
    invoices(tax, [row("output", 100_000, 3_000)])
    with pytest.raises(AccountingError) as error:
        tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert error.value.status == 409
    assert "税务档案" in error.value.detail


def test_generate_without_invoice_rows_refuses(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    with pytest.raises(AccountingError) as error:
        tax.tax.generate(tax.manager, tax.book["id"], "2026-10")
    assert error.value.status == 409
    assert "发票" in error.value.detail


def test_small_scale_tax_and_variance_follow_integer_basis_points(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000), row("input", 40_000, 1_200)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert paper["status"] == "draft"
    assert paper["version"] == 1
    assert paper["output_net"] == "1000.00"
    assert paper["tax"] == "30.00"
    assert paper["surtax"] == "3.60"
    assert paper["credit_carry"] == "0.00"
    assert paper["ledger"] == "0.00"
    assert paper["variance"] == "30.00"
    assert paper["filing"] == "not_filed"
    assert len(paper["lines"]) == 2
    assert len(paper["source_digest"]) == 64


def test_general_taxpayer_offsets_input_tax_and_reports_carry(tax):
    tax.tax.set_profile(
        tax.manager, tax.book["id"], profile(taxpayer_type="general", vat_rate_bp=1300)
    )
    invoices(tax, [row("output", 200_000, 26_000), row("input", 100_000, 30_000)])
    paper = tax.tax.generate(tax.manager, tax.book["id"], "2026-10")
    assert paper["tax"] == "0.00"
    assert paper["credit_carry"] == "40.00"
    assert paper["surtax"] == "0.00"


def test_own_workpaper_cannot_be_reviewed_by_its_maker(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    with pytest.raises(AccountingError) as error:
        tax.tax.review(tax.actor_accountant, tax.book["id"], paper["id"], "已核对", 1)
    assert error.value.status == 403
    assert "制单人" in error.value.detail


def test_variance_requires_explanation_before_review(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    reviewed = tax.tax.review(tax.manager, tax.book["id"], paper["id"], "账面尚未计提，下月补", 1)
    assert reviewed["status"] == "reviewed"
    assert reviewed["reviewed_by"] == tax.manager.user_id
    assert reviewed["variance_reason"] == "账面尚未计提，下月补"


def test_draft_workpaper_cannot_be_exported(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    with pytest.raises(AccountingError) as error:
        tax.tax.csv(tax.actor_accountant, tax.book["id"], paper["id"])
    assert error.value.status == 409
    tax.tax.review(tax.manager, tax.book["id"], paper["id"], "账表差异已说明", 1)
    text = tax.tax.csv(tax.actor_accountant, tax.book["id"], paper["id"])
    assert text.startswith("\ufeff")
    assert "1000.00" in text and "30.00" in text


def test_period_cannot_be_closed_by_a_plain_update(tax):
    """关期必须走复核回执；服务层的“已关闭拒绝”因此不是唯一一道门。"""
    with pytest.raises(Exception) as error:
        with tax.db.transaction(write=True) as conn:
            conn.execute("UPDATE periods SET status='closed' WHERE book_id=?", (tax.book["id"],))
    assert "reviewed close receipt" in str(error.value)


def test_repeated_generate_appends_versions_without_mutating_old(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    first = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    second = tax.tax.generate(tax.actor_accountant, tax.book["id"], "2026-10")
    assert (first["version"], second["version"]) == (1, 2)
    listed = tax.tax.list(tax.actor_accountant, tax.book["id"], "2026-10")
    assert [item["version"] for item in listed["items"]] == [2, 1]
