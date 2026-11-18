from __future__ import annotations

import hashlib
import sqlite3

from scripts.opt_policy_coverage import inspect_database


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_policy_coverage_lists_ledgers_without_effective_policy(tmp_path):
    path = tmp_path / "coverage.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ledgers(id TEXT PRIMARY KEY, firm_id TEXT, company_id TEXT, name TEXT);
        CREATE TABLE accounting_policies(
          id TEXT PRIMARY KEY, ledger_id TEXT, status TEXT,
          effective_from TEXT, effective_to TEXT
        );
        INSERT INTO ledgers VALUES
          ('l-active','f','c1','有生效政策'),
          ('l-draft','f','c2','只有草稿'),
          ('l-expired','f','c3','政策已过期'),
          ('l-future','f','c4','政策未生效'),
          ('l-none','f','c5','没有政策');
        INSERT INTO accounting_policies VALUES
          ('p1','l-active','active','2026-01-01',NULL),
          ('p2','l-draft','draft','2026-01-01',NULL),
          ('p3','l-expired','active','2025-01-01','2025-12-31'),
          ('p4','l-future','active','2027-01-01',NULL);
        """
    )
    conn.commit()
    conn.close()

    before = _digest(path)
    result = inspect_database(path, "2026-11-18")

    assert result["missing_count"] == 4
    assert [row["ledger_id"] for row in result["ledgers"]] == [
        "l-draft",
        "l-expired",
        "l-future",
        "l-none",
    ]
    assert all("标准" not in str(row) for row in result["ledgers"])
    assert _digest(path) == before
