from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from app.accounting.database import Database
from app.reports.profit import (
    ProfitDrilldownService,
    ProfitMappingItem,
    ProfitMappingWrite,
)
from test_business import business  # noqa: F401
from test_tax13_leases import (  # noqa: F401
    POLICY,
    _advance_account,
    _confirm,
    _create,
    _recognize,
)
from test_tax14_basis import _generate as _generate_basis


path = Path(__file__).resolve().parents[2] / "scripts" / "backup_accounting.py"
spec = spec_from_file_location("backup_accounting_tax_f", path)
recovery = module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_lease_basis_and_mapping_survive_full_restore(business, tmp_path):
    case = business
    _advance_account(case)
    contract = _create(case, monthly="1000.00")
    _confirm(case, contract["id"], "business", "2026-10", "1000.00", "SERVICE-F-1")
    _confirm(case, contract["id"], "receipt", "2026-10", "1200.00", "BANK-F-1")
    recognized = _recognize(case, contract["id"], "2026-10")
    october = [
        row
        for row in recognized["plan"]
        if row["period"] == "2026-10" and row["kind"] == "revenue"
    ][0]
    basis = _generate_basis(case)
    mapping = ProfitDrilldownService(case.db).save_mapping(
        case.manager,
        case.book["id"],
        ProfitMappingWrite(
            items=[
                ProfitMappingItem(
                    line_code="revenue",
                    line_name="营业收入",
                    line_kind="revenue",
                    account_prefix="6001",
                    sign=1,
                ),
                ProfitMappingItem(
                    line_code="admin",
                    line_name="管理费用",
                    line_kind="admin",
                    account_prefix="6602",
                    sign=-1,
                ),
            ]
        ),
    )
    assert mapping["version"] == 1

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(case.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        saved = conn.execute(
            "SELECT version,status,monthly_cents,approved_version FROM lease_contracts WHERE id=?",
            (contract["id"],),
        ).fetchone()
        assert saved["version"] == 1
        assert saved["status"] == "active"
        assert saved["monthly_cents"] == 100000
        assert saved["approved_version"] == 1

        plan = conn.execute(
            "SELECT period,kind,status,amount_cents,draft_id,advance_cents "
            "FROM lease_plan_rows WHERE contract_id=? AND period='2026-10'",
            (contract["id"],),
        ).fetchone()
        assert plan["status"] == "recognized"
        assert plan["amount_cents"] == 100000
        assert plan["draft_id"] == october["draft_id"]
        assert plan["advance_cents"] == 100000

        confirmations = conn.execute(
            "SELECT kind,amount_cents,reference FROM lease_confirmations "
            "WHERE contract_id=? ORDER BY kind",
            (contract["id"],),
        ).fetchall()
        assert [(row["kind"], row["amount_cents"], row["reference"]) for row in confirmations] == [
            ("business", 100000, "SERVICE-F-1"),
            ("receipt", 120000, "BANK-F-1"),
        ]

        saved_basis = conn.execute(
            "SELECT status,version,base_cents,tax_cents,manual_evidence "
            "FROM tax_basis_reviews WHERE id=?",
            (basis["id"],),
        ).fetchone()
        assert saved_basis["status"] == "draft"
        assert saved_basis["version"] == 1
        assert saved_basis["base_cents"] == 10000000
        assert saved_basis["tax_cents"] == 206000
        assert "人工核定" in saved_basis["manual_evidence"]

        mappings = conn.execute(
            "SELECT version,line_code,line_kind,account_prefix,active "
            "FROM report_line_mappings WHERE line_code IN ('revenue','admin') ORDER BY version,line_code",
        ).fetchall()
        assert len([row for row in mappings if row["active"] == 1]) == 2
        assert max(row["version"] for row in mappings) == 1
        assert conn.execute("SELECT COUNT(*) FROM tax_cost_sources").fetchone()[0] == 0
