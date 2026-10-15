"""Live grant checks are bounded without changing resource/error visibility."""

import pytest
from contextlib import contextmanager
from types import SimpleNamespace

from app.accounting.access import AccessControl
from app.accounting.domain import Actor, AccountingError
from test_business import business  # noqa: F401


@pytest.mark.parametrize(
    "name,capability,status",
    [
        ("manager", "export", 200),
        ("accountant", "review", 200),
        ("accountant", "manage", 403),
        ("finance", "edit", 200),
        ("finance", "export", 403),
        ("employee", "read", 404),
        ("employee", "manage", 403),
        ("company_admin", "read", 404),
        ("company_admin", "manage", 200),
    ],
)
def test_book_grant_boundaries_need_one_query(business, name, capability, status):
    c, access = business, AccessControl()
    actor = c.manager if name == "manager" else c.actors[name]
    with c.db.transaction(actor=actor) as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        if status == 200:
            value = access.book(conn, actor, c.book["id"], capability)
            assert value["id"] == c.book["id"]
            assert not any(key.startswith("_access_") for key in value)
        else:
            with pytest.raises(AccountingError) as error:
                access.book(conn, actor, c.book["id"], capability)
            assert error.value.status == status
        conn.set_trace_callback(None)
    assert len(statements) == 1


def test_company_live_grants_errors_and_result_contract(business):
    c, access = business, AccessControl()
    actor = c.actors["accountant"]
    with c.db.transaction(write=True, actor=c.manager) as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        value = access.company(conn, actor, c.company["id"])
        conn.set_trace_callback(None)
        assert len(statements) == 1
        original = conn.execute("SELECT * FROM companies WHERE id=?", (c.company["id"],)).fetchone()
        assert value == dict(original)
        with pytest.raises(AccountingError) as error:
            access.company(conn, actor, c.company["id"], manage=True)
        assert error.value.status == 403
        conn.execute("DELETE FROM book_access WHERE user_id=?", (actor.user_id,))
        with pytest.raises(AccountingError) as error:
            access.company(conn, actor, c.company["id"])
        assert error.value.status == 404
        conn.execute("UPDATE users SET active=0 WHERE id=?", (actor.user_id,))
        for lookup in (
            lambda: access.firm_role(conn, actor),
            lambda: access.company(conn, actor, "missing-company"),
            lambda: access.book(conn, actor, "missing-book"),
        ):
            with pytest.raises(AccountingError) as error:
                lookup()
            assert error.value.status == 403
            assert error.value.detail == "账号未激活或已停用"


def test_active_unassigned_user_is_distinct_from_missing_or_disabled_user(business):
    c, access = business, AccessControl()
    active = Actor(c.actors["employee"].user_id, "unassigned-firm")
    missing = Actor("missing-user", active.firm_id)
    with c.db.transaction(actor=c.manager) as conn:
        assert access.firm_role(conn, active) is None
        with pytest.raises(AccountingError) as error:
            access.firm_role(conn, missing)
        assert error.value.status == 403
        for actor in (active, c.actors["accountant"]):
            with pytest.raises(AccountingError) as error:
                access.book(conn, actor, c.other_book["id"])
            assert error.value.status == 404


def test_unchanged_identity_has_no_write_and_changed_identity_rechecks_disable(
    business, monkeypatch
):
    c = business
    identity = SimpleNamespace(
        user_id="employee", display_name="employee", email="EMPLOYEE@example.test"
    )
    original, writes = c.db.transaction, []

    @contextmanager
    def traced(*args, **kwargs):
        if kwargs.get("write"):
            writes.append(True)
        with original(*args, **kwargs) as conn:
            yield conn

    with monkeypatch.context() as patch:
        patch.setattr(c.db, "transaction", traced)
        c.directory.register_identity(identity)
    assert writes == []
    identity.display_name = "更正后的姓名"
    c.directory.register_identity(identity)
    with original(actor=c.actors["employee"]) as conn:
        assert (
            conn.execute("SELECT name FROM users WHERE id='employee'").fetchone()[0]
            == identity.display_name
        )

    @contextmanager
    def revoke_before_write(*args, **kwargs):
        if kwargs.get("write"):
            with original(write=True) as conn:
                conn.execute("UPDATE users SET active=0 WHERE id='employee'")
        with original(*args, **kwargs) as conn:
            yield conn

    identity.display_name = "不可覆盖的旧身份"
    with monkeypatch.context() as patch:
        patch.setattr(c.db, "transaction", revoke_before_write)
        with pytest.raises(AccountingError, match="账号已停用"):
            c.directory.register_identity(identity)
    with original() as conn:
        row = conn.execute("SELECT name,active FROM users WHERE id='employee'").fetchone()
        assert row[0] == "更正后的姓名" and row[1] == 0


def test_unchanged_identity_never_replaces_live_profile_authorization(business):
    c = business
    identity = SimpleNamespace(
        user_id="employee", display_name="employee", email="employee@example.test"
    )
    c.directory.register_identity(identity)
    with c.db.transaction(write=True) as conn:
        conn.execute("UPDATE users SET active=0 WHERE id=?", (identity.user_id,))
    for lookup in (
        lambda: c.directory.me(identity.user_id, c.manager.firm_id),
        lambda: c.directory.resolve_actor(identity.user_id, c.manager.firm_id),
    ):
        with pytest.raises(AccountingError) as error:
            lookup()
        assert error.value.status == 403
