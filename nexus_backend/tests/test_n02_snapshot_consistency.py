"""Even an authentic source digest must not approve mismatched denormalized rows."""

import csv
import io

import pytest

from app.accounting.domain import AccountingError
from test_business import business  # noqa: F401
from test_tax_workpaper import invoices, profile, row, tax  # noqa: F401


@pytest.mark.parametrize(
    "field,value",
    [
        ("tax_cents", 3001),
        ("variance_cents", 1),
        ("output_net_cents", 999),
        ("lines_json", "[]"),
    ],
)
def test_recompute_checks_stored_amounts_and_lines_not_just_digest(tax, field, value):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], tax.book["period"])
    with tax.db.transaction(actor=tax.manager) as conn:
        book = tax.tax.access.book(conn, tax.manager, tax.book["id"], "review")
        stored = dict(
            conn.execute("SELECT * FROM tax_workpapers WHERE id=?", (paper["id"],)).fetchone()
        )
        stored[field] = value
        # Exercise the service validator independently of SQL shape guards:
        # legacy or corrupted snapshots must still fail at the publish boundary.
        with pytest.raises(AccountingError) as error:
            tax.tax._assert_current(conn, book, stored)
        assert error.value.status == 409


def test_csv_tax_summary_uses_tax_column(tax):
    tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    invoices(tax, [row("output", 100_000, 3_000)])
    paper = tax.tax.generate(tax.actor_accountant, tax.book["id"], tax.book["period"])
    tax.tax.review(
        tax.manager, tax.book["id"], paper["id"], "独立核验来源和列语义", paper["version"]
    )
    body = tax.tax.csv(
        tax.manager, tax.book["id"], paper["id"], paper["version"], paper["source_digest"]
    )
    lines = list(csv.reader(io.StringIO(body.lstrip("\ufeff"))))
    summary = next(r for r in lines if len(r) > 1 and r[1] == "应纳税额")
    assert len(summary) == len(lines[0]) == 7
    assert summary[4] == "" and summary[5] == paper["tax"]
