from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.accounting.database import Database
from app.ledgers.models import TransportAssignmentCreate
from app.ledgers.service import LedgersService
from app.tax.fees import TaxFeeService
from test_business import business  # noqa: F401
from test_tax06_fees import POLICY, _masters, _source


path = Path(__file__).resolve().parents[2] / "scripts" / "backup_accounting.py"
spec = spec_from_file_location("backup_accounting_tax06", path)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_fee_batch_rows_review_and_line_sources_survive_full_restore(business, tmp_path):
    case = business
    _driver, _vehicle, driver2, vehicle2 = _masters(case)
    LedgersService(case.db).create_transport_assignment(
        case.manager, case.book["id"], TransportAssignmentCreate(
            vehicle_id=vehicle2["id"], driver_id=driver2["id"], shift="full",
            contract_external_id="C1", valid_from="2026-10-05", valid_to="2026-10-05",
        ),
    )
    source = _source(case)
    service = TaxFeeService(case.db)
    created = service.generate(
        case.manager, case.book["id"], period="2026-10",
        source_file_ids=[source["id"]], policy=POLICY,
    )
    reviewed = service.review(
        case.actors["accountant"], case.book["id"], created["id"],
        expected_version=1, reason="恢复测试独立复核",
    )
    assert reviewed["status"] == "reviewed"

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(case.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        batch = conn.execute(
            "SELECT status,version,total_cents,row_count,skipped_count,source_fingerprint "
            "FROM tax_fee_batches WHERE id=?", (created["id"],),
        ).fetchone()
        assert batch["status"] == "reviewed"
        assert batch["version"] == 2
        assert batch["total_cents"] == 22500
        assert batch["row_count"] == 3
        assert batch["skipped_count"] == 0
        assert batch["source_fingerprint"] == created["source_fingerprint"]
        rows = conn.execute(
            "SELECT external_id,status,net_cents,reason,line_ordinal "
            "FROM tax_fee_batch_rows WHERE batch_id=? ORDER BY external_id",
            (created["id"],),
        ).fetchall()
        assert [(row["external_id"], row["status"]) for row in rows] == [
            ("L1", "adopted"), ("L2", "adopted"), ("L4", "adopted"),
        ]
        assert rows[1]["reason"] == "季节性减免"
        adopted = [row for row in rows if row["status"] == "adopted"]
        assert [row["net_cents"] for row in adopted] == [9000, 8500, 5000]
        assert all(row["line_ordinal"] is not None for row in adopted)
        sources = conn.execute(
            "SELECT role,amount_cents,draft_id FROM draft_line_sources "
            "WHERE book_id=? ORDER BY ordinal", (case.book["id"],),
        ).fetchall()
        assert len(sources) == 6
        assert [row["role"] for row in sources] == [
            "receivable", "revenue", "receivable", "revenue", "receivable", "revenue",
        ]
        assert [row["amount_cents"] for row in sources] == [9000, 9000, 8500, 8500, 5000, 5000]
        assert all(row["draft_id"] == created["draft_id"] for row in sources)
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
            "('tax_fee_batches','tax_fee_batch_rows','draft_line_sources')"
        ).fetchone()[0] == 3
