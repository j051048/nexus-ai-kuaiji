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
        business.manager,
        business.book["id"],
        TransportSubjectCreate(
            kind="vehicle", external_id="RESTORE-V1", display_name="恢复测试车辆"
        ),
    )
    adapter = TaxSourceAdapter(business.db)
    bound = adapter.bind_output(
        business.manager,
        business.book["id"],
        source["id"],
        output_kind="workpaper",
        output_id="W-RESTORE",
        output_version=1,
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
        business.manager, business.book["id"], source["id"]
    )
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
        restored_subject = conn.execute(
            "SELECT external_id,display_name FROM transport_subjects WHERE id=?", (subject["id"],)
        ).fetchone()
        assert restored_subject["external_id"] == "RESTORE-V1"
        assert restored_subject["display_name"] == "恢复测试车辆"
        binding = conn.execute(
            "SELECT source_fingerprint,frozen,invalidated_at FROM tax_source_bindings WHERE id=?",
            (bound["id"],),
        ).fetchone()
        assert binding["source_fingerprint"] == source["fingerprint"]
        assert binding["frozen"] == 0
        assert binding["invalidated_at"] is None
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
                "('transport_subjects','transport_relations','transport_assignments','transport_aliases',"
                "'tax_source_files','tax_source_bindings')"
            ).fetchone()[0]
            == 6
        )


def test_fee_receivables_allocations_and_refunds_survive_database_restore(business, tmp_path):
    """A restored database keeps receivable state, allocation, refund and source bytes."""
    from app.tax.fees import TaxFeeService
    from app.ledgers.models import TransportAssignmentCreate
    from app.ledgers.service import LedgersService
    from test_tax06_fees import POLICY, _bank, _masters, _source

    _driver, _vehicle, driver2, vehicle2 = _masters(business)
    LedgersService(business.db).create_transport_assignment(
        business.manager,
        business.book["id"],
        TransportAssignmentCreate(
            vehicle_id=vehicle2["id"],
            driver_id=driver2["id"],
            shift="full",
            contract_external_id="C1",
            valid_from="2026-10-05",
            valid_to="2026-10-05",
        ),
    )
    source = _source(business)
    service = TaxFeeService(business.db)
    batch = service.generate(
        business.manager,
        business.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        business.actors["accountant"],
        business.book["id"],
        batch["id"],
        expected_version=1,
        reason="恢复测试独立复核",
    )
    receivable = service.list_receivables(business.manager, business.book["id"])["items"][0]
    amount = int(receivable["amount_cents"])
    income = _bank(business, "income", amount, "BANK-RESTORE-IN")
    allocation = service.allocate_receivable(
        business.manager,
        business.book["id"],
        receivable["receivable_id"],
        bank_source_id=income,
        amount_cents=amount,
        idempotency_key="restore-allocation",
    )
    expense = _bank(business, "expense", 3000, "BANK-RESTORE-OUT")
    refund = service.refund_receivable(
        business.manager,
        business.book["id"],
        receivable["receivable_id"],
        bank_source_id=expense,
        amount_cents=3000,
        reason="恢复测试退款",
        idempotency_key="restore-refund",
    )
    assert refund["receivable_status"] == "open"

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(business.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        item = conn.execute(
            "SELECT status,amount_cents FROM fee_receivable_items WHERE id=?",
            (receivable["receivable_id"],),
        ).fetchone()
        assert item["status"] == "open"
        assert int(item["amount_cents"]) == amount
        rows = conn.execute(
            "SELECT amount_cents,reversed_at FROM fee_receivable_allocations WHERE id=?",
            (allocation["allocation_id"],),
        ).fetchone()
        assert int(rows["amount_cents"]) == amount and rows["reversed_at"] is None
        refund_row = conn.execute(
            "SELECT amount_cents,reason FROM fee_receivable_refunds WHERE id=?",
            (refund["refund_id"],),
        ).fetchone()
        assert int(refund_row["amount_cents"]) == 3000
        assert refund_row["reason"] == "恢复测试退款"
    import hashlib

    filename, original = TaxSourceAdapter(copied).download_original(
        business.manager, business.book["id"], source["id"]
    )
    assert filename == source["filename"]
    assert hashlib.sha256(original).hexdigest() == source["sha256"]


def test_fsc_contract_and_import_envelope_survive_restore(business, tmp_path):
    from hashlib import sha256

    from app.fsc.contracts import FscContractService
    from app.fsc.imports import FscImportService, ImportEnvelopeWrite
    from test_fsc04_contracts import confirmed_profile, contract_payload, second_manager

    confirmed_profile(business, business.company["id"])
    contracts = FscContractService(business.db)
    draft = contracts.create_draft(business.manager, business.company["id"], contract_payload())
    contracts.confirm(second_manager(business), draft["id"], draft["version"])
    active = contracts.activate(business.manager, draft["id"], draft["version"])

    imports = FscImportService(business.db)
    envelope = imports.import_envelope(
        business.manager,
        business.book["id"],
        ImportEnvelopeWrite(
            source_system="recovery_source",
            source_object="rows",
            external_id="RECOVERY-1",
            source_version=1,
            filename="recovery.xlsx",
            media_type="application/vnd.ms-excel",
            sha256=sha256(b"recovery-envelope").hexdigest(),
            size_bytes=128,
            mapping_version="map-v1",
            rule_version="rule-v1",
            unit="CNY",
            currency="CNY",
            rows=[
                {
                    "external_id": "R1",
                    "date": "2026-10-02",
                    "amount_cents": 1000,
                    "quantity_milli": 1,
                    "unit": "CNY",
                    "currency": "CNY",
                }
            ],
        ),
    )
    imports.confirm(business.manager, envelope["id"], 1)

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(business.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    restored_contracts = FscContractService(copied)
    active_rows = restored_contracts.active_matrix(business.manager)["items"]
    assert any(
        row["id"] == active["id"] and row["processor_code"] == "taxi_fee" for row in active_rows
    )
    restored_imports = FscImportService(copied)
    detail = restored_imports.detail(business.manager, envelope["id"])
    assert detail["status"] == "confirmed"
    assert detail["source_version"] == 1
    assert detail["rows"][0]["external_id"] == "R1"
    with copied.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
                "('fsc_industry_contracts','fsc_industry_contract_events','fsc_import_envelopes','fsc_import_envelope_events')"
            ).fetchone()[0]
            == 4
        )


def test_fsc13_closing_authority_and_external_evidence_survive_restore(business, tmp_path):
    from app.periodic.authority import ClosingAuthorityService
    from test_fsc13_closing_authority import _confirm, _evidence

    _confirm(business, "internal")
    evidence = ClosingAuthorityService(business.db).bind(business.manager, _evidence(business))
    assert evidence["status"] == "evidence_only"

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    value = recovery.backup(business.db.path, storage, target)
    assert recovery.verify(target) == value
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    authority = ClosingAuthorityService(copied)
    summary = authority.summary(business.manager, business.book["id"])
    assert summary["authority"]["mode"] == "internal"
    assert summary["authority"]["confirmed"] is True
    assert len(summary["evidence"]) == 1
    assert summary["evidence"][0]["id"] == evidence["id"]
    assert summary["evidence"][0]["content_fingerprint"] == evidence["content_fingerprint"]


def test_fsc15_repair_rate_versions_survive_database_restore(business, tmp_path):
    from app.tax.costs import RepairRateDraft, RepairRateService

    rates = RepairRateService(business.db)
    draft = rates.create_draft(
        business.manager,
        business.book["id"],
        RepairRateDraft(
            vehicle_type="RESTORE-RATE",
            rate_cents=5000,
            valid_from="2026-10",
            valid_to="2026-12",
            coverage_scope="基础保养（不含轮胎）",
            source_ref="客户核定费率/恢复",
            unit="per_vehicle",
            currency="CNY",
            tax_included=False,
            reason="恢复测试客户核定费率",
        ),
    )
    rates.confirm(business.actors["accountant"], business.book["id"], draft["id"], 1)
    active = rates.activate(business.manager, business.book["id"], draft["id"], 1)
    assert active["status"] == "active"

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(business.db.path, storage, target)
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        row = conn.execute(
            "SELECT status,version,logical_id,fingerprint,confirmed_by,activated_at "
            "FROM repair_rate_cards WHERE id=?",
            (draft["id"],),
        ).fetchone()
    assert row["status"] == "active"
    assert row["version"] == 1
    assert row["logical_id"] == draft["logical_id"]
    assert row["fingerprint"] == active["fingerprint"]
    assert row["confirmed_by"] == business.actors["accountant"].user_id
    assert row["activated_at"] == active["activated_at"]


def test_fsc16_package_contract_and_usage_survive_database_restore(business, tmp_path):
    from app.tax.packages import (
        PackageConsume,
        PackageContractDraft,
        PackageRelease,
        RepairPackageService,
    )

    packages = RepairPackageService(business.db)
    book_id = business.book["id"]
    draft = packages.create_draft(
        business.manager,
        book_id,
        PackageContractDraft(
            contract_no="RESTORE-PKG",
            coverage_scope="恢复测试包干合同（不含事故维修）",
            vehicle_external_ids=["V1", "V2"],
            valid_from="2026-10",
            valid_to="2026-12",
            limit_cents=10000,
            policy="contract_cumulative",
            reason="恢复测试客户核定包干合同",
        ),
    )
    packages.confirm(business.actors["accountant"], book_id, draft["id"], 1)
    active = packages.activate(business.manager, book_id, draft["id"], 1)
    first = packages.consume(
        business.manager,
        book_id,
        draft["id"],
        PackageConsume(
            source_key="restore-consume",
            source_version=1,
            period="2026-10",
            amount_cents=6000,
            vehicle_external_id="V1",
            idempotency_key="restore-consume",
        ),
    )
    packages.release(
        business.manager,
        book_id,
        first["id"],
        PackageRelease(
            amount_cents=2000,
            reason="恢复测试部分释放",
            idempotency_key="restore-release",
        ),
    )

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(business.db.path, storage, target)
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        contract = conn.execute(
            "SELECT status,version,logical_id,fingerprint,limit_cents "
            "FROM repair_package_contracts WHERE id=?",
            (draft["id"],),
        ).fetchone()
        usage = conn.execute(
            "SELECT kind,amount_cents FROM repair_package_usage WHERE contract_id=? ORDER BY kind",
            (draft["id"],),
        ).fetchall()
    assert contract["status"] == "active"
    assert contract["version"] == 1
    assert contract["logical_id"] == active["logical_id"]
    assert contract["fingerprint"] == active["fingerprint"]
    assert contract["limit_cents"] == 10000
    assert [(row["kind"], row["amount_cents"]) for row in usage] == [
        ("consume", 6000),
        ("release", 2000),
    ]


def test_fsc17_asset_vehicle_link_and_aux_survive_database_restore(business, tmp_path):
    from app.periodic.links import AssetVehicleLinkService, AssetVehicleLinkWrite
    from app.periodic.service import PeriodicService
    from test_fsc17_links import _asset, _vehicle

    case = business
    asset_id = _asset(case, "恢复测试车辆资产")
    vehicle = _vehicle(case, "RESTORE-FSC17", "恢复测试车辆")
    links = AssetVehicleLinkService(case.db)
    linked = links.link(
        case.manager,
        asset_id,
        AssetVehicleLinkWrite(vehicle_external_id=vehicle["external_id"]),
    )
    assert linked["status"] == "active"
    draft = PeriodicService(case.db).generate_asset(case.manager, asset_id)
    with case.db.transaction() as conn:
        line = conn.execute(
            "SELECT aux_json,source_ref FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no LIMIT 1",
            (draft["draft"]["id"],),
        ).fetchone()
    assert "RESTORE-FSC17" in line["aux_json"]
    assert ";vehicle:RESTORE-FSC17@v1" in line["source_ref"]

    storage = tmp_path / "private"
    storage.mkdir()
    target = tmp_path / "backup"
    recovery.backup(case.db.path, storage, target)
    restored = tmp_path / "restored"
    recovery.restore(target, restored)

    copied = Database(restored / "database.sqlite")
    with copied.transaction() as conn:
        link = conn.execute(
            "SELECT status,asset_id,vehicle_external_id,source_version,source_fingerprint FROM periodic_asset_vehicle_links WHERE asset_id=?",
            (asset_id,),
        ).fetchone()
        saved = conn.execute(
            "SELECT aux_json,source_ref FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no LIMIT 1",
            (draft["draft"]["id"],),
        ).fetchone()
    assert link["status"] == "active"
    assert link["vehicle_external_id"] == "RESTORE-FSC17"
    assert link["source_version"] == 1
    assert link["source_fingerprint"] == linked["source_fingerprint"]
    assert json.loads(saved["aux_json"])["vehicle_external_id"] == "RESTORE-FSC17"
    assert saved["source_ref"].endswith(";vehicle:RESTORE-FSC17@v1")
