"""Bounded database round trips without caching authorization between requests."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg_pool import PoolTimeout, TooManyRequests

from app.accounting.database import DatabaseCapacityError
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import FirmCreate
from test_business import business
from test_business_api import client, auth


@contextmanager
def count_statements(db, monkeypatch):
    statements = []
    original = db.transaction

    class Traced:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, statement, parameters=None):
            statements.append(statement)
            return (
                self.conn.execute(statement, parameters)
                if parameters is not None
                else self.conn.execute(statement)
            )

        def __getattr__(self, name):
            return getattr(self.conn, name)

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield Traced(conn)

    with monkeypatch.context() as patch:
        patch.setattr(db, "transaction", transaction)
        yield statements


def test_workspace_resolution_order_and_explicit_scope_are_live(business, monkeypatch):
    c = business
    user = c.actors["employee"].user_id
    staff_z = c.directory.create_firm(c.manager.user_id, FirmCreate(name="Z 工作空间"))["id"]
    staff_a = c.directory.create_firm(c.manager.user_id, FirmCreate(name="A 工作空间"))["id"]
    with c.db.transaction(write=True) as conn:
        for firm in (staff_z, staff_a):
            conn.execute(
                "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'reviewer')", (firm, user)
            )
    with count_statements(c.db, monkeypatch) as statements:
        assert c.directory.resolve_actor(user) == Actor(user, staff_a)
    assert len(statements) == 1
    assert c.directory.me(user)["current_firm_id"] == staff_a
    assert c.directory.resolve_actor(user, c.manager.firm_id) == c.actors["employee"]
    with pytest.raises(AccountingError, match="工作空间授权"):
        c.directory.resolve_actor(user, "unknown-firm")
    with c.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM memberships WHERE user_id=? AND firm_id=?", (user, staff_a))
    assert c.directory.resolve_actor(user) == Actor(user, staff_z)
    with c.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM memberships WHERE user_id=?", (user,))
    assert c.directory.resolve_actor(user) == c.actors["employee"]
    with c.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM company_memberships WHERE user_id=?", (user,))
    with pytest.raises(AccountingError, match="尚未获得"):
        c.directory.resolve_actor(user)
    with c.db.transaction(write=True) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (c.manager.user_id,))
    with pytest.raises(AccountingError, match="停用"):
        c.directory.resolve_actor(c.manager.user_id)


@pytest.mark.parametrize("extra_companies", [1, 10, 100])
def test_context_round_trips_do_not_grow_with_company_count(business, monkeypatch, extra_companies):
    c = business
    with c.db.transaction(write=True) as conn:
        for index in range(extra_companies):
            company = uuid4().hex
            conn.execute(
                "INSERT INTO companies(id,name) VALUES(?,?)", (company, f"扩展企业{index:03}")
            )
            conn.execute(
                "INSERT INTO firm_clients(firm_id,company_id) VALUES(?,?)",
                (c.manager.firm_id, company),
            )
            conn.execute(
                "INSERT INTO departments(id,company_id,name) VALUES(?,?,'业务部')",
                (uuid4().hex, company),
            )
    with count_statements(c.db, monkeypatch) as statements:
        result = c.service.context(c.manager)
    assert len(statements) <= 6
    assert len(result["companies"]) == 2 + extra_companies
    assert result["scopes"] == {
        "own": True,
        "department": False,
        "company": False,
        "accounting": True,
    }
    assert all(not company["can_submit"] for company in result["companies"])


def test_context_retains_employee_and_ledger_only_boundaries(business):
    c = business
    employee = c.service.context(c.actors["employee"])
    assert [row["id"] for row in employee["companies"]] == [c.company["id"]]
    row = employee["companies"][0]
    assert row["can_submit"] and not row["ledger_available"]
    assert {d["id"] for d in row["departments"]} == {c.department["id"], c.other_department["id"]}
    assert row["submission_policy"] == {
        "mode": "department",
        "version": 0,
        "reason": "",
        "updated_at": None,
    }
    approver = c.service.context(c.actors["approver"])
    assert set(approver["companies"][0]["approver_department_ids"]) == {
        c.department["id"],
        c.other_department["id"],
    }
    accountant = c.service.context(c.actors["accountant"])
    assert len(accountant["companies"]) == 1
    row = accountant["companies"][0]
    assert row["ledger_available"] and not row["departments"] and not row["can_submit"]
    with c.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM book_access WHERE user_id=?", (c.actors["accountant"].user_id,))
    assert c.service.context(c.actors["accountant"])["companies"] == []


@pytest.mark.parametrize("error_type", [PoolTimeout, TooManyRequests])
def test_capacity_errors_become_retryable_http_without_leaking_dsn(client, monkeypatch, error_type):
    from app.accounting.database import Database

    headers = auth(client, "employee")
    db = client.app.state.service.db

    class BusyPool:
        def getconn(self):
            raise error_type("postgresql://private-password@private-host")

    fake = SimpleNamespace(_closed=False, is_postgres=True, _postgres_pool=lambda: BusyPool())
    monkeypatch.setattr(db, "connect", lambda: Database.connect(fake))
    response = client.get("/api/business/context", headers=headers)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["code"] == "database_busy"
    assert "private" not in response.text
    # Invalid authentication still fails before capacity acquisition.
    assert client.get("/api/business/context").status_code == 401


def test_other_pool_failure_is_not_misreported_as_capacity():
    from app.accounting.database import Database

    class BrokenPool:
        def getconn(self):
            raise ValueError("configuration contains secret")

    fake = SimpleNamespace(_closed=False, is_postgres=True, _postgres_pool=lambda: BrokenPool())
    with pytest.raises(RuntimeError) as caught:
        Database.connect(fake)
    assert not isinstance(caught.value, DatabaseCapacityError)
    assert "secret" not in str(caught.value)
