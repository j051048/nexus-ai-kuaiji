"""OPT03-A: audit_events are append-only for both SQLite and PostgreSQL."""

from __future__ import annotations

import sqlite3

import pytest

from test_directory import case as sqlite_case  # noqa: F401
from test_postgres_integration import ADMIN_URL, APP_URL, case as pg_case, postgres  # noqa: F401


def _book_id(c):
    return c.book["id"] if isinstance(c.book, dict) else c.book


def _insert_sqlite_audit(c):
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                c.actor.firm_id,
                _book_id(c),
                c.actor.user_id,
                "opt03.test",
                "audit-row",
                '{"test": true}',
                "2026-11-17T00:00:00+00:00",
            ),
        )
        return conn.execute("SELECT id FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()[0]


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_sqlite_audit_events_reject_mutation(sqlite_case, operation):
    c = sqlite_case
    audit_id = _insert_sqlite_audit(c)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with c.db.transaction(write=True, actor=c.actor) as conn:
            if operation == "update":
                conn.execute(
                    "UPDATE audit_events SET action='tamper' WHERE id=?", (audit_id,)
                )
            else:
                conn.execute("DELETE FROM audit_events WHERE id=?", (audit_id,))
    with c.db.transaction(actor=c.actor) as conn:
        assert conn.execute(
            "SELECT action FROM audit_events WHERE id=?", (audit_id,)
        ).fetchone()[0] == "opt03.test"


@pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要独立测试 PostgreSQL 的管理员及受限运行 DSN")
@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
def test_postgres_audit_events_reject_owner_mutation(pg_case, operation):
    c = pg_case
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(%s,%s,%s,'opt03.test','audit-row','{}','2026-11-17T00:00:00+00:00')",
            (c.actor.firm_id, c.book, c.actor.user_id),
        )
        with pytest.raises(c.psycopg.errors.CheckViolation, match="append-only"):
            if operation == "update":
                conn.execute("UPDATE accounting.audit_events SET action='tamper'")
            elif operation == "delete":
                conn.execute("DELETE FROM accounting.audit_events")
            else:
                conn.execute("TRUNCATE accounting.audit_events")
