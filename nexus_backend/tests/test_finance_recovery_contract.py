import json
import pytest
from scripts.release_recovery_hold import INVENTORY_TABLES, REQUIRED_COLUMNS, pending_inventory
from test_n03_recovery import InventoryConnection


def test_finance_recovery_requires_assets_sources_and_allocations():
    for table in (
        "periodic_assets",
        "periodic_runs",
        "periodic_drafts",
        "periodic_draft_lines",
        "subledger_items",
        "subledger_allocations",
        "subledger_allocation_requests",
        "subledger_allocation_reversals",
    ):
        assert table in INVENTORY_TABLES
    assert "reconciliation_json" in REQUIRED_COLUMNS["periodic_runs"]
    assert "posted_voucher_id" in REQUIRED_COLUMNS["periodic_drafts"]


def test_finance_source_drift_changes_inventory_without_exposing_evidence():
    conn = InventoryConnection()
    row = dict.fromkeys(REQUIRED_COLUMNS.get("periodic_runs", ()), None)
    row.update(id="run", reconciliation_json='{"private-asset-evidence":1}')
    conn.tables["periodic_runs"] = [row]
    first = pending_inventory(conn)
    assert first["tables"]["periodic_runs"]
    assert "private-asset-evidence" not in json.dumps(first)
    row["reconciliation_json"] = '{"private-asset-evidence":2}'
    assert first["sha256"] != pending_inventory(conn)["sha256"]


def test_pre_reconciliation_schema_cannot_release():
    conn = InventoryConnection()
    conn.tables["periodic_runs"] = [{"id": "old"}]
    with pytest.raises(ValueError, match="periodic_runs.*reconciliation_json"):
        pending_inventory(conn)
