"""OPT03-B: PostgreSQL audit hash-chain acceptance."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.verify_audit_chain import verify_postgres  # noqa: E402
from test_postgres_integration import ADMIN_URL, case as pg_case, postgres  # noqa: F401


def _insert(c, count=3, action="opt03.chain"):
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        for index in range(count):
            conn.execute(
                "INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(%s,%s,%s,%s,%s,'{}','2026-11-17T00:00:00+00:00')",
                (c.actor.firm_id, c.book, c.actor.user_id, f"{action}.{index}", f"row-{index}"),
            )


def _ids(c, action):
    with c.psycopg.connect(ADMIN_URL) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT id FROM accounting.audit_events WHERE action LIKE %s ORDER BY id",
                (action + ".%",),
            )
        ]


@pytest.mark.skipif(not ADMIN_URL, reason="需要独立测试 PostgreSQL 管理员 DSN")
def test_pg_chain_recomputes_and_detects_content_tamper(pg_case):
    c = pg_case
    _insert(c, 3, "opt03.pg.chain")
    ids = _ids(c, "opt03.pg.chain")
    assert len(ids) == 3
    assert verify_postgres(ADMIN_URL, c.actor.firm_id) == []

    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("ALTER TABLE accounting.audit_events DISABLE TRIGGER USER")
        conn.execute(
            "UPDATE accounting.audit_events SET details='{\"tampered\": true}' WHERE id=%s",
            (ids[1],),
        )
        conn.execute("ALTER TABLE accounting.audit_events ENABLE TRIGGER USER")

    breaks = verify_postgres(ADMIN_URL, c.actor.firm_id)
    assert breaks and breaks[0].row_id == ids[1]
    assert breaks[0].reason == "row-hash-mismatch"


@pytest.mark.skipif(not ADMIN_URL, reason="需要独立测试 PostgreSQL 管理员 DSN")
def test_pg_chain_detects_deleted_middle_row(pg_case):
    c = pg_case
    _insert(c, 3, "opt03.pg.delete")
    ids = _ids(c, "opt03.pg.delete")
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("ALTER TABLE accounting.audit_events DISABLE TRIGGER USER")
        conn.execute("DELETE FROM accounting.audit_events WHERE id=%s", (ids[1],))
        conn.execute("ALTER TABLE accounting.audit_events ENABLE TRIGGER USER")
    breaks = verify_postgres(ADMIN_URL, c.actor.firm_id)
    assert breaks and breaks[0].row_id == ids[2]
    assert breaks[0].reason == "prev-hash-mismatch"


@pytest.mark.skipif(not ADMIN_URL, reason="需要独立测试 PostgreSQL 管理员 DSN")
def test_pg_chain_serializes_concurrent_inserts(pg_case):
    c = pg_case

    def writer(worker: int):
        with c.psycopg.connect(ADMIN_URL) as conn:
            for index in range(10):
                conn.execute(
                    "INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,'{}','2026-11-17T00:00:00+00:00')",
                    (
                        c.actor.firm_id,
                        c.book,
                        c.actor.user_id,
                        f"opt03.pg.concurrent.{worker}.{index}",
                        f"{worker}-{index}",
                    ),
                )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(writer, [1, 2]))
    assert verify_postgres(ADMIN_URL, c.actor.firm_id) == []
