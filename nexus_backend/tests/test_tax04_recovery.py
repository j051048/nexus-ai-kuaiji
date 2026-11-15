from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json

from app.accounting.database import Database
from app.ledgers.models import TransportSubjectCreate
from app.ledgers.service import LedgersService
from app.tax.sources import TaxSourceAdapter
from test_tax04_sources import _import
from test_business import business  # noqa: F401

path = Path(__file__).resolve().parents[2] / "scripts" / "backup_accounting.py"
spec = spec_from_file_location("backup_accounting_tax04", path)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_source_rows_fingerprint_and_binding_survive_full_database_restore(business, tmp_path):
    source = _import(business, version=1)
    subject = LedgersService(business.db).create_transport_subject(
        business.manager, business.book["id"],
        TransportSubjectCreate(kind="vehicle", external_id="RESTORE-V1", display_name="恢复测试车辆"),
    )
    adapter = TaxSourceAdapter(business.db)
    bound = adapter.bind_output(
        business.manager, business.book["id"], source["id"],
        output_kind="workpaper", output_id="W-RESTORE", output_version=1,
    )
    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(business.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    import hashlib
    filename, original = TaxSourceAdapter(copied).download_original(
        business.manager, business.book["id"], source["id"])
    assert filename == source["filename"]
    assert hashlib.sha256(original).hexdigest() == source["sha256"]
    with copied.transaction() as conn:
        file_row = conn.execute(
            "SELECT fingerprint,rows_json,amount_total_cents,quantity_milli FROM tax_source_files WHERE id=?",
            (source["id"],),
        ).fetchone()
        assert file_row["fingerprint"] == source["fingerprint"]
        rows = json.loads(file_row["rows_json"])
        assert [row["status"] for row in rows].count("adopted") == 2
        assert file_row["amount_total_cents"] == 3000
        assert file_row["quantity_milli"] == 3500
        restored_subject = conn.execute("SELECT external_id,display_name FROM transport_subjects WHERE id=?", (subject["id"],)).fetchone()
        assert restored_subject["external_id"] == "RESTORE-V1"
        assert restored_subject["display_name"] == "恢复测试车辆"
        binding = conn.execute(
            "SELECT source_fingerprint,frozen,invalidated_at FROM tax_source_bindings WHERE id=?",
            (bound["id"],),
        ).fetchone()
        assert binding["source_fingerprint"] == source["fingerprint"]
        assert binding["frozen"] == 0
        assert binding["invalidated_at"] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
            "('transport_subjects','transport_relations','transport_assignments','transport_aliases',"
            "'tax_source_files','tax_source_bindings')"
        ).fetchone()[0] == 6
