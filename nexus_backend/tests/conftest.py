"""Close runtime pools created by tests, including legacy worker fixtures.

Module fixtures retain their database until their existing teardown completes.
Function-created databases are closed after each test, so a long PostgreSQL
suite cannot accumulate background diagnostic threads or idle connections.

SQLite tests start from a per-session schema template: applying every migration
costs ~1.4s of the ~2.6s per-test setup (E01 baseline), while copying a
checkpointed file costs milliseconds. Set ``ACCOUNTING_TEST_SCHEMA_TEMPLATE=0``
for suites that must observe first-run migration behaviour.
"""

from contextvars import ContextVar
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.accounting.database import Database, validate_database_locator


_fixture_owner = ContextVar("database_fixture_owner", default=None)
_template: Path | None = None
_template_directory: Path | None = None


def _build_template(original) -> Path:
    global _template_directory
    directory = Path(tempfile.mkdtemp(prefix="accounting-schema-"))
    # The session fixture owns this directory; leaking one per run used to leave
    # hundreds of empty schema directories in the working tree.
    _template_directory = directory
    path = directory / "template.db"
    database = Database.__new__(Database)
    original(database, path)
    database.close()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    finally:
        connection.close()
    return path


def _wants_template(target: object) -> bool:
    if os.getenv("ACCOUNTING_TEST_SCHEMA_TEMPLATE", "1") == "0":
        return False
    if not isinstance(target, (str, Path)):
        return False
    value = str(target)
    if value == ":memory:" or value.startswith(("postgres://", "postgresql://")):
        return False
    path = Path(value)
    return not path.exists() or path.stat().st_size == 0


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    # Dynamic getfixturevalue() can initialize a module fixture while a
    # function fixture is running. Its own SubRequest remains the authority
    # for cleanup, rather than the surrounding test's construction timestamp.
    token = _fixture_owner.set(request)
    try:
        yield
    finally:
        _fixture_owner.reset(token)


@pytest.fixture(scope="session", autouse=True)
def database_resources():
    global _template
    created = []
    original = Database.__init__
    with pytest.MonkeyPatch.context() as patch:

        def tracked(instance, *args, **kwargs):
            global _template
            target = args[0] if args else kwargs.get("path")
            if target is not None:
                validate_database_locator(str(target))
            if _wants_template(target):
                if _template is None:
                    _template = _build_template(original)
                destination = Path(str(target))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_template, destination)
            original(instance, *args, **kwargs)
            owner = _fixture_owner.get()
            if owner is not None:
                owner.addfinalizer(instance.close)
            else:
                created.append(instance)

        patch.setattr(Database, "__init__", tracked)
        yield created
        for database in reversed(created):
            database.close()
        if _template_directory is not None:
            shutil.rmtree(_template_directory, ignore_errors=True)


@pytest.fixture(autouse=True)
def function_database_resources(database_resources):
    start = len(database_resources)
    yield
    for database in reversed(database_resources[start:]):
        database.close()
    del database_resources[start:]
