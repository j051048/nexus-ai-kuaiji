"""SQLite structural guards only; actor authorization is tested in PostgreSQL."""

import json
import sqlite3
from uuid import uuid4

import pytest

from app.accounting.models import BookCreate
from test_business import business  # noqa: F401
from test_tax_workpaper import tax, profile  # noqa: F401


def seed(tax):
    p = tax.tax.set_profile(tax.manager, tax.book["id"], profile())
    values = {
        "id": uuid4().hex,
        "firm_id": tax.manager.firm_id,
        "company_id": tax.book["company_id"],
        "book_id": tax.book["id"],
        "period": "2026-10",
        "version": 1,
        "status": "draft",
        "profile_id": p["id"],
        "output_net_cents": 0,
        "output_tax_cents": 0,
        "input_net_cents": 0,
        "input_tax_cents": 0,
        "tax_cents": 0,
        "surtax_cents": 0,
        "credit_carry_cents": 0,
        "ledger_cents": 0,
        "variance_cents": 0,
        "variance_reason": "",
        "lines_json": "[]",
        "source_digest": "0" * 64,
        "created_by": tax.actor_accountant.user_id,
        "created_at": "2026-10-28T00:00:00+00:00",
    }
    return values


def insert(tax, values):
    with tax.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO tax_workpapers("
            + ",".join(values)
            + ") VALUES("
            + ",".join("?" for _ in values)
            + ")",
            tuple(values.values()),
        )


@pytest.mark.parametrize("field", ["company_id", "profile_id", "period"])
def test_sqlite_native_scope_is_bound(tax, field):
    from app.accounting.service import AccountingService

    values = seed(tax)
    other = AccountingService(tax.db).create_book(
        tax.manager, BookCreate(name="虚构范围乙", period="2026-10")
    )
    other_profile = tax.tax.set_profile(tax.manager, other["id"], profile())
    values[field] = {
        "company_id": other["company_id"],
        "profile_id": other_profile["id"],
        "period": "2026-11",
    }[field]
    with pytest.raises(sqlite3.IntegrityError, match="scope|profile|period"):
        insert(tax, values)


def test_sqlite_empty_snapshot_cannot_review(tax):
    values = seed(tax)
    insert(tax, values)
    with pytest.raises(sqlite3.IntegrityError, match="source snapshot"):
        with tax.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE tax_workpapers SET status='reviewed',reviewed_by=?,reviewed_at='now',review_reason='真实独立复核' WHERE id=?",
                (tax.manager.user_id, values["id"]),
            )


def test_sqlite_snapshot_column_and_freeze(tax):
    values = seed(tax)
    with tax.db.transaction() as conn:
        assert "source_snapshot_json" in [
            r["name"] for r in conn.execute("PRAGMA table_info(tax_workpapers)")
        ]
    # Empty legacy draft remains readable; its frozen evidence cannot be replaced.
    insert(tax, values)
    with pytest.raises(sqlite3.IntegrityError, match="source snapshot"):
        with tax.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE tax_workpapers SET source_snapshot_json='{}' WHERE id=?", (values["id"],)
            )


@pytest.mark.parametrize("change", ["tax", "lines", "variance", "bool", "float", "string"])
def test_sqlite_source_totals_and_materialized_fields_agree(tax, change):
    values = seed(tax)
    with tax.db.transaction() as conn:
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (tax.book["id"],)
        ).fetchone()["ledger_id"]
    source = {
        "schema": "n02-tax-v2",
        "period": values["period"],
        "scope": {
            "firm_id": values["firm_id"],
            "company_id": values["company_id"],
            "book_id": values["book_id"],
            "ledger_id": ledger,
            "period": values["period"],
        },
        "profile": {"id": values["profile_id"]},
        "vat_accounts": [],
        "invoices": [],
        "ledger": {"vouchers": [], "lines": []},
        "totals": [0] * 8,
    }
    if change == "tax":
        values["tax_cents"] = 1
    elif change == "lines":
        values["lines_json"] = '[{"forged":true}]'
    elif change == "variance":
        values["variance_cents"] = 1
    else:
        source["totals"][0] = {"bool": True, "float": 0.0, "string": "0"}[change]
    values["source_snapshot_json"] = json.dumps(source)
    with pytest.raises(sqlite3.IntegrityError, match="snapshot|variance"):
        insert(tax, values)
