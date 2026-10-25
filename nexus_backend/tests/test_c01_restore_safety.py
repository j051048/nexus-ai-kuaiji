"""Recovery isolation cannot fail open on inaccessible state or a second hold."""

from contextlib import contextmanager
import sqlite3

import pytest

from app.accounting.database import Database
from test_accounting import service


def test_restore_hold_checks_all_instances_not_only_first(service):
    with service.db.transaction(write=True) as conn:
        for instance, hold in (("earlier-released", 0), ("new-restored", 1)):
            conn.execute(
                "INSERT INTO restore_state(instance_id,hold,restore_id,reason,updated_by,updated_at) "
                "VALUES(?,?,?,'local drill','system','2026-10-25')",
                (instance, hold, instance),
            )
    with pytest.raises(SystemExit, match="恢复隔离"):
        service.db.assert_restore_hold()


@pytest.mark.parametrize("error", [PermissionError("private dsn"), RuntimeError("offline")])
def test_restore_state_unavailable_blocks_startup(service, monkeypatch, error):
    @contextmanager
    def inaccessible():
        raise error
        yield

    monkeypatch.setattr(service.db, "connect", inaccessible)
    with pytest.raises(SystemExit, match="隔离状态") as caught:
        service.db.assert_restore_hold()
    assert "private dsn" not in str(caught.value)


def test_only_explicit_missing_sqlite_hold_table_is_legacy_compatible(tmp_path):
    path = tmp_path / "pre-migration.db"
    sqlite3.connect(path).close()
    db = Database.__new__(Database)
    db.connect = lambda: sqlite3.connect(path)
    db.assert_restore_hold()


def test_postgres_hold_read_sets_restricted_role_and_search_path():
    class Connection:
        begun = False
        closed = False

        def close(self):
            self.closed = True

        def begin(self, *, write, role, user_id, firm_id):
            assert write is False and role == "accounting_app" and user_id == firm_id == ""
            self.begun = True

        def execute(self, statement):
            if not self.begun:
                raise PermissionError("role and search_path not installed")
            return self

        def fetchone(self):
            return None

    db = Database.__new__(Database)
    db.is_postgres = True
    db.validation_role = "accounting_app"
    connection = Connection()
    db.connect = lambda: connection
    db.assert_restore_hold()
    assert connection.begun
    assert connection.closed


@pytest.mark.parametrize("value", [2, "invalid"])
def test_corrupt_sqlite_restore_state_cannot_enable_serving(service, value):
    with service.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO restore_state VALUES('corrupt',?,'r','invalid test fixture','system','2026-10-25')",
            (value,),
        )
    with pytest.raises(SystemExit, match="恢复隔离"):
        service.db.assert_restore_hold()


def test_postgres_missing_hold_table_is_corruption_after_validated_migrations(service, monkeypatch):
    class MissingTable(Exception):
        sqlstate = "42P01"

    def unavailable():
        raise MissingTable()

    monkeypatch.setattr(service.db, "connect", unavailable)
    with pytest.raises(SystemExit, match="隔离状态"):
        service.db.assert_restore_hold()
