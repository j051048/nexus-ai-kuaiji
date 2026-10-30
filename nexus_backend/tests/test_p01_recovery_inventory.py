"""P01-R1: the release digest binds the N02 frozen source envelope itself."""

import copy
import hashlib
import json
import re

import pytest

from scripts import release_recovery_hold as release


class TaxInventoryConnection:
    """Project exactly the columns requested by the release verifier."""

    def __init__(self, snapshot, *, omit_snapshot=False):
        self.tax = dict.fromkeys(release.REQUIRED_COLUMNS["tax_workpapers"], None)
        self.tax.update(
            id="synthetic-tax-paper",
            company_id="company-a",
            book_id="book-a",
            period="2026-10",
            version=1,
            status="reviewed",
            source_digest="a" * 64,
            source_snapshot_json=json.dumps(snapshot, sort_keys=True),
        )
        if omit_snapshot:
            self.tax.pop("source_snapshot_json")
        self.rows = []

    def execute(self, statement, arguments=()):
        if isinstance(statement, str):
            assert "information_schema.columns" in statement
            table = arguments[0]
            columns = (
                self.tax
                if table == "tax_workpapers"
                else release.REQUIRED_COLUMNS.get(table, ("id", "state"))
            )
            self.rows = [(name,) for name in columns]
        else:
            projection, table = statement.as_string().split(" FROM accounting.")
            names = re.findall(r'"([a-z_0-9]+)"', projection)
            self.rows = (
                [tuple(self.tax[name] for name in names)]
                if table.strip('"') == "tax_workpapers"
                else []
            )
        return self

    def __iter__(self):
        return iter(self.rows)

    def fetchall(self):
        return self.rows


SNAPSHOT = {
    "schema": "n02-tax-v2",
    "company_id": "company-a",
    "book_id": "book-a",
    "invoices": [{"id": "private-invoice-id", "version": 1, "net_cents": 100}],
    "profile": {"id": "profile-a", "vat_rate_bp": 100},
}


def test_tax_release_inventory_requires_the_frozen_source_envelope():
    assert "source_snapshot_json" in release.REQUIRED_COLUMNS["tax_workpapers"]


def test_tax_source_envelope_is_hashed_without_disclosing_invoice_content():
    conn = TaxInventoryConnection(SNAPSHOT)
    result = release.pending_inventory(conn)
    row = result["tables"]["tax_workpapers"][0]
    assert (
        row["source_snapshot_json"]
        == hashlib.sha256(conn.tax["source_snapshot_json"].encode()).hexdigest()
    )
    assert "private-invoice-id" not in json.dumps(result)


@pytest.mark.parametrize("changed_field", ["version", "net_cents"])
def test_changed_frozen_tax_source_invalidates_release_even_if_stored_digest_unchanged(
    changed_field,
):
    changed = copy.deepcopy(SNAPSHOT)
    changed["invoices"][0][changed_field] += 1
    before = release.pending_inventory(TaxInventoryConnection(SNAPSHOT))
    after = release.pending_inventory(TaxInventoryConnection(changed))
    assert before["sha256"] != after["sha256"]


def test_pre_n02_tax_schema_cannot_supply_complete_release_proof():
    with pytest.raises(ValueError, match="tax_workpapers.*source_snapshot_json"):
        release.pending_inventory(TaxInventoryConnection(SNAPSHOT, omit_snapshot=True))
