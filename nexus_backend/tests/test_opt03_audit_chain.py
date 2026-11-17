"""OPT03-B: audit hash chain SQLite acceptance."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.verify_audit_chain import verify_sqlite  # noqa: E402
from test_directory import case as sqlite_case  # noqa: F401


def _book_id(c):
    return c.book["id"] if isinstance(c.book, dict) else c.book


def _insert(c, action="opt03.chain"):
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                c.actor.firm_id,
                _book_id(c),
                c.actor.user_id,
                action,
                "audit-" + action,
                '{"kind": "synthetic"}',
                "2026-11-17T00:00:00+00:00",
            ),
        )


def _rows(c):
    with c.db.transaction(actor=c.actor) as conn:
        return conn.execute("SELECT * FROM audit_events ORDER BY firm_id,id").fetchall()


def test_sqlite_chain_recomputes_and_detects_content_tamper(sqlite_case):
    c = sqlite_case
    for index in range(3):
        _insert(c, f"opt03.chain.{index}")
    rows = [row for row in _rows(c) if row["action"].startswith("opt03.chain.")]
    assert len(rows) == 3
    assert all(row["row_hash"] and row["prev_hash"] is not None for row in rows)
    assert verify_sqlite(c.db.path) == []

    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("DROP TRIGGER audit_events_append_only_update")
        conn.execute("DROP TRIGGER audit_events_append_only_delete")
        conn.execute(
            "UPDATE audit_events SET details='{\"tampered\": true}' WHERE id=?",
            (rows[1]["id"],),
        )
    breaks = verify_sqlite(c.db.path)
    assert breaks and breaks[0].row_id == rows[1]["id"]
    assert breaks[0].reason == "row-hash-mismatch"


def test_sqlite_chain_detects_deleted_middle_row(sqlite_case):
    c = sqlite_case
    for index in range(3):
        _insert(c, f"opt03.delete.{index}")
    rows = [row for row in _rows(c) if row["action"].startswith("opt03.delete.")]
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("DROP TRIGGER audit_events_append_only_update")
        conn.execute("DROP TRIGGER audit_events_append_only_delete")
        conn.execute("DELETE FROM audit_events WHERE id=?", (rows[1]["id"],))
    breaks = verify_sqlite(c.db.path)
    assert breaks and breaks[0].row_id == rows[2]["id"]
    assert breaks[0].reason == "prev-hash-mismatch"


MIGRATION_177 = (
    Path(__file__).resolve().parents[1] / "migrations" / "sqlite" / "177_audit_hash_chain.sql"
)


def test_sqlite_hash_backfill_applies_to_existing_rows(tmp_path):
    import hashlib

    from scripts.verify_audit_chain import verify_sqlite

    db = tmp_path / "backfill.sqlite3"
    conn = sqlite3.connect(db)
    conn.create_function(
        "accounting_sha256",
        1,
        lambda v: hashlib.sha256(str(v).encode()).hexdigest(),
        deterministic=True,
    )
    conn.executescript(
        """
        CREATE TABLE audit_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          firm_id TEXT NOT NULL, book_id TEXT NOT NULL, company_id TEXT,
          user_id TEXT NOT NULL, action TEXT NOT NULL, object_id TEXT,
          details TEXT, created_at TEXT NOT NULL
        );
        CREATE TRIGGER audit_events_append_only_update
        BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT,'audit_events are append-only'); END;
        """
    )
    for index in range(3):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,company_id,user_id,action,object_id,details,created_at) "
            "VALUES('f1','b1','c1','u1',?,?,'{}','2026-11-17T00:00:00+00:00')",
            (f"opt03.backfill.{index}", f"row-{index}"),
        )
    conn.commit()
    conn.executescript(MIGRATION_177.read_text(encoding="utf-8"))
    conn.commit()
    rows = conn.execute("SELECT prev_hash,row_hash FROM audit_events ORDER BY id").fetchall()
    conn.close()
    assert all(prev is not None and row_hash for prev, row_hash in rows)
    assert verify_sqlite(str(db)) == []
