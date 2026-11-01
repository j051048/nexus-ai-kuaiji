"""Actual PostgreSQL metadata upgrade, rollback boundary and restricted roles.

Only the explicit loopback isolation runner may reconstruct a pre-191 fixture.
No production credentials, no application data edits, no model calls.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from threading import Barrier

import pytest

from test_historical_compatibility_repair import HISTORICAL, REPAIR


ADMIN = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
WORKER = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="Use the explicit loopback PostgreSQL isolation runner",
)


def inventory(conn):
    return conn.execute(
        "SELECT version,checksum,applied_at,compatibility FROM accounting.schema_migrations ORDER BY version"
    ).fetchall()


@contextmanager
def rollback_fixture():
    import psycopg

    with psycopg.connect(ADMIN, autocommit=True) as conn:
        conn.execute("BEGIN")
        try:
            yield conn
        finally:
            conn.execute("ROLLBACK")


def test_pg_upgrade_repairs_once_preserving_history_under_two_migrators():
    import psycopg
    from scripts.migrate_accounting import migrate, status

    # Reconstruct ONLY the new metadata-only migration's predecessor locally.
    # No historical checksum or application row is edited in this fixture.
    with psycopg.connect(ADMIN) as conn:
        from app.accounting.database import migration_files

        known = {int(p.name.split("_", 1)[0]) for p in migration_files("postgres")}
        assert {r[0] for r in inventory(conn)} <= known
        conn.execute("DELETE FROM accounting.schema_migrations WHERE version=191")
        conn.execute(
            "UPDATE accounting.schema_migrations SET compatibility='incremental' WHERE version=ANY(%s)",
            (list(HISTORICAL),),
        )
        before = inventory(conn)
        assert {r[0] for r in before} >= HISTORICAL.keys()
    before_status = dict(status(ADMIN))
    assert {v: before_status[v] for v in HISTORICAL} == dict.fromkeys(HISTORICAL, "兼容性标签不符")
    assert before_status[191] == "缺失"
    barrier = Barrier(2)

    def upgrade(_):
        barrier.wait(timeout=10)
        return migrate(ADMIN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(upgrade, range(2)))
    with psycopg.connect(ADMIN) as conn:
        after = inventory(conn)
    assert {r[0]: r[3] for r in after if r[0] in HISTORICAL} == dict.fromkeys(
        HISTORICAL, "breaking"
    )
    assert sorted(map(tuple, receipts)) == [(), (191,)]
    assert [r[:3] for r in after if r[0] != 191] == [r[:3] for r in before]
    assert {r[0]: r[3] for r in after if r[0] not in {*HISTORICAL, 191}} == {
        r[0]: r[3] for r in before if r[0] not in HISTORICAL
    }
    assert migrate(ADMIN) == []
    assert all(state == "已应用" for _, state in status(ADMIN))


def test_pg_strict_status_rejects_label_drift_with_all_migrations_present(monkeypatch, capsys):
    import psycopg
    import sys
    from scripts.migrate_accounting import main, status

    with psycopg.connect(ADMIN) as conn:
        conn.execute(
            "UPDATE accounting.schema_migrations SET compatibility='incremental' WHERE version=87"
        )
        before = inventory(conn)
    try:
        mismatched = [(v, s) for v, s in status(ADMIN) if s != "已应用"]
        assert mismatched == [(87, "兼容性标签不符")]
        monkeypatch.setenv("ACCOUNTING_ADMIN_DATABASE_URL", ADMIN)
        monkeypatch.setattr(sys, "argv", ["migrate_accounting.py", "status", "--strict"])
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 1
        assert "087: 兼容性标签不符" in capsys.readouterr().out
        with psycopg.connect(ADMIN) as conn:
            assert inventory(conn) == before
    finally:
        with psycopg.connect(ADMIN) as conn:
            conn.execute(
                "UPDATE accounting.schema_migrations SET compatibility='breaking' WHERE version=87"
            )


def test_pg_reapplying_to_correct_metadata_is_a_true_noop():
    with rollback_fixture() as conn:
        before = inventory(conn)
        conn.execute(REPAIR.read_text(encoding="utf-8"), prepare=False)
        conn.execute(REPAIR.read_text(encoding="utf-8"), prepare=False)
        assert inventory(conn) == before


@pytest.mark.parametrize("versions", [(87,), (138, 148, 150), tuple(HISTORICAL)])
def test_pg_partial_metadata_drift_is_repaired_without_touching_other_rows(versions):
    with rollback_fixture() as conn:
        baseline = inventory(conn)
        conn.execute(
            "UPDATE accounting.schema_migrations SET compatibility='incremental' WHERE version=ANY(%s)",
            (list(versions),),
        )
        conn.execute(REPAIR.read_text(encoding="utf-8"), prepare=False)
        assert inventory(conn) == baseline


@pytest.mark.parametrize("compatibility", ["incremental", "breaking"])
@pytest.mark.parametrize("version", HISTORICAL)
def test_pg_wrong_historical_checksum_aborts_whole_repair(version, compatibility):
    import psycopg

    with rollback_fixture() as conn:
        conn.execute(
            "UPDATE accounting.schema_migrations SET compatibility='incremental' WHERE version=ANY(%s)",
            (list(HISTORICAL),),
        )
        # Deliberate isolated corruption; the surrounding transaction always rolls back.
        conn.execute(
            "UPDATE accounting.schema_migrations SET checksum=%s,compatibility=%s WHERE version=%s",
            ("f" * 64, compatibility, version),
        )
        corrupted = inventory(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(REPAIR.read_text(encoding="utf-8"), prepare=False)
        assert inventory(conn) == corrupted  # no partially repaired labels


@pytest.mark.parametrize("version", HISTORICAL)
def test_pg_missing_historical_record_aborts_whole_repair(version):
    import psycopg

    with rollback_fixture() as conn:
        conn.execute(
            "UPDATE accounting.schema_migrations SET compatibility='incremental' WHERE version=ANY(%s)",
            (list(HISTORICAL),),
        )
        conn.execute("DELETE FROM accounting.schema_migrations WHERE version=%s", (version,))
        missing = inventory(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(REPAIR.read_text(encoding="utf-8"), prepare=False)
        assert inventory(conn) == missing


@pytest.mark.parametrize("principal", ["app", "worker"])
def test_pg_repair_is_unavailable_to_runtime_roles(principal):
    import psycopg

    url, role = (APP, "accounting_app") if principal == "app" else (WORKER, "accounting_worker")
    for query in (
        "UPDATE accounting.schema_migrations SET compatibility='incremental' WHERE version=87",
        REPAIR.read_text(encoding="utf-8"),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(url) as conn:
                conn.execute("SET LOCAL ROLE " + role)
                conn.execute(query, prepare=False)


def test_pg_old_image_cannot_ignore_the_breaking_repair(monkeypatch):
    import app.accounting.database as module

    original = module.migration_files
    monkeypatch.setattr(
        module,
        "migration_files",
        lambda dialect: [
            p
            for p in original(dialect)
            if dialect != "postgres" or int(p.name.split("_", 1)[0]) < 191
        ],
    )
    with pytest.raises(RuntimeError, match="未知的迁移版本"):
        module.Database(APP)
