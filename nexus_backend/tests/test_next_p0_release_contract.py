"""Frozen migration exceptions and public artifact admission are narrow contracts."""

import json
from pathlib import Path

import pytest

from app.accounting.database import migration_compatibility, migration_digest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "dialect,version", [("postgres", 205), ("postgres", 206), ("postgres", 207), ("sqlite", 129)]
)
def test_frozen_next_headers_remain_breaking_with_exact_exception(dialect, version):
    (path,) = (ROOT / "nexus_backend/migrations" / dialect).glob(f"{version:03d}_*.sql")
    entries = json.loads(
        (ROOT / "nexus_backend/tests/migration_structure_debt.json").read_text(encoding="utf-8")
    )["entries"]
    (row,) = [x for x in entries if (x["dialect"], x["file"]) == (dialect, path.name)]
    assert row["sha256"] == migration_digest(path)
    assert migration_compatibility(path) == "breaking"


@pytest.mark.parametrize(
    "name",
    [
        "user=a password=secret dbname=b",
        "tmp/host=localhost dbname=test",
        "proof/config-private.json",
        ".codex/test.json",
    ],
)
def test_private_paths_cannot_be_committed_or_uploaded(name):
    from scripts.check_public_artifacts import check_paths

    with pytest.raises(ValueError) as error:
        check_paths([name])
    assert "secret" not in str(error.value)


def test_public_sources_and_sanitized_reports_remain_allowed():
    from scripts.check_public_artifacts import check_paths

    check_paths(["docs/NEXT_P0_ACCEPTANCE.md", "src/App.tsx", "config/test_identity_assets.json"])
