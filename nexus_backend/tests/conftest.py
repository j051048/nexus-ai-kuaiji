"""Close runtime pools created by tests, including legacy worker fixtures.

Module fixtures retain their database until their existing teardown completes.
Function-created databases are closed after each test, so a long PostgreSQL
suite cannot accumulate background diagnostic threads or idle connections.
"""

from contextvars import ContextVar

import pytest

from app.accounting.database import Database


_fixture_owner = ContextVar("database_fixture_owner", default=None)


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
    created = []
    original = Database.__init__
    with pytest.MonkeyPatch.context() as patch:

        def tracked(instance, *args, **kwargs):
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


@pytest.fixture(autouse=True)
def function_database_resources(database_resources):
    start = len(database_resources)
    yield
    for database in reversed(database_resources[start:]):
        database.close()
    del database_resources[start:]
