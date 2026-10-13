"""Dynamically requested module fixtures outlive the requesting first test."""

import pytest

from app.accounting.database import Database


@pytest.fixture(scope="module")
def delayed_module_database(tmp_path_factory):
    return Database(tmp_path_factory.mktemp("delayed-module") / "fixture.db")


@pytest.fixture
def dynamically_requested_database(request):
    return request.getfixturevalue("delayed_module_database")


def test_first_dynamic_request_commits_fixture_evidence(dynamically_requested_database):
    with dynamically_requested_database.transaction(write=True) as conn:
        conn.execute("CREATE TABLE fixture_lifetime_probe(value INTEGER NOT NULL)")
        conn.execute("INSERT INTO fixture_lifetime_probe VALUES(1)")


def test_second_request_can_use_same_module_database(dynamically_requested_database):
    assert not dynamically_requested_database._closed
    with dynamically_requested_database.transaction() as conn:
        assert conn.execute("SELECT value FROM fixture_lifetime_probe").fetchone()[0] == 1
