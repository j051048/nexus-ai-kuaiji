"""Freeze previously applied PostgreSQL migrations independently of the debt ledger."""

import hashlib
from pathlib import Path

import pytest

from app.accounting.database import migration_digest

POSTGRES_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "postgres"

# Verified against commit 3fc1ab6e11761ba36490f1858b85fcff356441cc.
# Keep these constants independent of the current files and exception ledgers:
# even a comment added to an applied migration changes its recorded checksum.
APPLIED_MIGRATION_DIGESTS = {
    "148_projection_company_guard.sql": (
        "25fdd7d0130767833aba38a2984e32ce915a93f5643b3208299bae30758b4dbe"
    ),
    "149_projection_actorless_lock.sql": (
        "b2c895897a8bd715052034754f5c8836f985919a1ebf99e35258e086bec5441a"
    ),
    "150_projection_scope_guard.sql": (
        "f77ea8509a8246a624ae83ebc068a6b6977a64e35f1d225bd73a231b5906f8c0"
    ),
}


@pytest.mark.parametrize("filename,expected_digest", APPLIED_MIGRATION_DIGESTS.items())
def test_applied_migration_keeps_original_normalized_digest(filename, expected_digest):
    path = POSTGRES_MIGRATIONS / filename
    source = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    actual_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

    assert actual_digest == expected_digest, (
        f"{filename}: applied migration changed; restore its original content instead "
        "of replacing the database checksum or updating this historical constant"
    )
    assert migration_digest(path) == expected_digest
