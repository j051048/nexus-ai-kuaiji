import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.accounting.database import Database
from test_business import business  # noqa: F401
from test_tax09_costs import _generate, _insurance, _repair


path = Path(__file__).resolve().parents[2] / "scripts" / "backup_accounting.py"
spec = spec_from_file_location("backup_accounting_tax09", path)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_cost_sources_batches_and_rows_survive_full_restore(business, tmp_path):
    case = business
    repair = _repair(case)
    _insurance(case)
    batch = _generate(case, [repair], "repair")
    assert batch["row_count"] == 1

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(case.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        sources = conn.execute(
            "SELECT kind,source_key,source_version,period,amount_cents,status,payload_json "
            "FROM tax_cost_sources ORDER BY kind,source_key",
        ).fetchall()
        assert [(row["kind"], row["source_key"]) for row in sources] == [
            ("insurance", "P-1"), ("repair", "FP-001"),
        ]
        assert json.loads(sources[1]["payload_json"]) == repair["payload"]
        batch_row = conn.execute(
            "SELECT status,version,kind,period,amount_cents,row_count,draft_id "
            "FROM tax_cost_batches WHERE id=?", (batch["id"],),
        ).fetchone()
        assert batch_row["status"] == "draft"
        assert batch_row["version"] == 1
        assert batch_row["kind"] == "repair"
        assert batch_row["period"] == "2026-10"
        assert batch_row["amount_cents"] == 10000
        assert batch_row["row_count"] == 1
        assert batch_row["draft_id"] == batch["draft_id"]
        rows = conn.execute(
            "SELECT source_id,amount_cents,status,line_ordinal FROM tax_cost_batch_rows WHERE batch_id=?",
            (batch["id"],),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["source_id"] == repair["id"]
        assert rows[0]["amount_cents"] == 10000
        assert rows[0]["status"] == "adopted"
        assert rows[0]["line_ordinal"] is not None
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
            "('tax_cost_sources','tax_cost_batches','tax_cost_batch_rows')"
        ).fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM tax_cost_sources").fetchone()[0] == 2
