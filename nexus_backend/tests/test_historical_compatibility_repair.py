"""Historical SQL bytes are immutable; only forward metadata repair is allowed."""

from pathlib import Path

import pytest

from app.accounting.database import migration_compatibility, migration_digest
from scripts.migrate_accounting import classify_migrations


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "postgres"
REPAIR = MIGRATIONS / "191_historical_compatibility_labels.sql"
HISTORICAL = {
    87: "949a3933621d8686b24c05df01ee7b54ed927099e1accd1696666efdfc0936ba",
    138: "3896fcbe7ea8d26b96060597b5a7ebb638cdc2acfdefb822486845e133a5494f",
    148: "25fdd7d0130767833aba38a2984e32ce915a93f5643b3208299bae30758b4dbe",
    149: "b2c895897a8bd715052034754f5c8836f985919a1ebf99e35258e086bec5441a",
    150: "f77ea8509a8246a624ae83ebc068a6b6977a64e35f1d225bd73a231b5906f8c0",
}


@pytest.mark.parametrize("version,digest", HISTORICAL.items())
def test_original_sql_identity_and_conservative_classification_stay_frozen(version, digest):
    (path,) = MIGRATIONS.glob(f"{version:03d}_*.sql")
    assert migration_digest(path) == digest
    assert migration_compatibility(path) == "breaking"


def test_forward_repair_itself_blocks_unsafe_old_image_rollback():
    assert REPAIR.is_file(), "Add a forward repair; never rewrite historical SQL"
    assert migration_compatibility(REPAIR) == "breaking"


def test_status_cannot_report_green_when_compatibility_is_wrong():
    assert classify_migrations(
        {87: HISTORICAL[87]},
        [(87, HISTORICAL[87])],
        recorded_compatibility={87: "incremental"},
        expected_compatibility={87: "breaking"},
    ) == [(87, "兼容性标签不符")]


def test_status_still_prioritizes_missing_and_changed_checksum():
    assert classify_migrations(
        {87: "wrong"},
        [(87, HISTORICAL[87]), (138, HISTORICAL[138])],
        recorded_compatibility={87: "incremental"},
        expected_compatibility={87: "breaking", 138: "breaking"},
    ) == [(87, "校验不符"), (138, "缺失")]


def test_status_accepts_correct_classification_and_detects_missing_metadata():
    files = [(87, HISTORICAL[87])]
    assert classify_migrations(
        {87: HISTORICAL[87]},
        files,
        recorded_compatibility={87: "breaking"},
        expected_compatibility={87: "breaking"},
    ) == [(87, "已应用")]
    assert classify_migrations(
        {87: HISTORICAL[87]},
        files,
        recorded_compatibility={},
        expected_compatibility={87: "breaking"},
    ) == [(87, "兼容性标签不符")]
