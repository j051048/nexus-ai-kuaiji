"""A–D data survive an actual backup/restore; empty-table checks do not count."""

import sqlite3

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from scripts import backup_accounting
from taxi_ad_recovery_business import (
    seed_taxi_recovery,
    taxi_snapshot,
    verify_taxi_recovery_services,
)
from test_business import business  # noqa: F401


def test_populated_taxi_source_review_export_unknown_and_hold_survive_restore(business, tmp_path):
    evidence = seed_taxi_recovery(
        business.db, business.manager, business.actors["accountant"], business.company["id"]
    )
    with business.db.transaction(actor=business.manager) as conn:
        expected = taxi_snapshot(conn, evidence)
    storage = tmp_path / "private-originals"
    storage.mkdir()
    backup, restored = tmp_path / "snapshot", tmp_path / "restored"
    manifest = backup_accounting.backup(business.db.path, storage, backup)
    assert backup_accounting.verify(backup) == manifest
    proof = backup_accounting.restore(backup, restored)
    assert proof["outbound_disabled"] is True and proof["worker_autostart"] is False
    assert proof["in_flight_jobs_hold"] is True and proof["serving_enabled"] is False
    with sqlite3.connect(restored / "database.sqlite") as conn:
        assert (
            conn.execute("SELECT hold FROM restore_state WHERE instance_id='restored'").fetchone()[
                0
            ]
            == 1
        )
        assert taxi_snapshot(conn, evidence) == expected
    copied = Database(restored / "database.sqlite")
    try:
        verify_taxi_recovery_services(copied, business.manager, evidence)
        with pytest.raises(AccountingError):
            verify_taxi_recovery_services(copied, business.actors["employee"], evidence)
        with copied.transaction(actor=business.manager) as conn:
            assert taxi_snapshot(conn, evidence) == expected
    finally:
        copied.close()
