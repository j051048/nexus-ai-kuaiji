"""Pooler-compatible setup retains transaction, identity and rollback boundaries."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.accounting.domain import Actor
from test_postgres_integration import ADMIN_URL, APP_URL, case, peer, postgres  # noqa: F401

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要独立本机 PostgreSQL 管理员和受限测试 DSN"
)


def test_identity_values_cannot_escape_setup_and_are_cleared_on_return(case):
    injected = Actor("'; RESET ROLE; SELECT 1; --", "quote'\\firm")
    with case.db.transaction(actor=injected) as conn:
        values = conn.execute(
            "SELECT current_user,accounting.actor_user(),accounting.actor_firm(),"
            "current_setting('transaction_read_only'),current_setting('transaction_isolation')"
        ).fetchone()
        assert values[:] == (
            "accounting_app",
            injected.user_id,
            injected.firm_id,
            "on",
            "repeatable read",
        )
        assert not conn.execute("SELECT id FROM books").fetchall()
    with closing(case.db.connect()) as conn:
        assert conn.execute(
            "SELECT current_user=session_user,"
            "coalesce(current_setting('app.user_id',true),'')='',"
            "coalesce(current_setting('app.firm_id',true),'')=''"
        ).fetchone()[:] == (True, True, True)
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchone()


def test_read_only_transaction_stays_repeatable_and_cannot_write(case):
    with case.db.transaction(actor=case.actor) as conn:
        before = conn.execute("SELECT name FROM books WHERE id=?", (case.book,)).fetchone()[0]
        with case.psycopg.connect(ADMIN_URL) as writer:
            writer.execute(
                "UPDATE accounting.books SET name=%s WHERE id=%s", (before + "-changed", case.book)
            )
        assert (
            conn.execute("SELECT name FROM books WHERE id=?", (case.book,)).fetchone()[0] == before
        )
    with case.db.transaction(actor=case.actor) as conn:
        assert (
            conn.execute("SELECT name FROM books WHERE id=?", (case.book,)).fetchone()[0]
            == before + "-changed"
        )
    with pytest.raises(case.psycopg.errors.ReadOnlySqlTransaction):
        with case.db.transaction(actor=case.actor) as conn:
            conn.execute("UPDATE books SET name='forbidden' WHERE id=?", (case.book,))


def test_setup_failure_never_yields_domain_connection_or_identity(case, monkeypatch):
    from psycopg import ClientCursor

    execute = ClientCursor.execute
    reached = []

    def fail_final_setup(self, query, params=None, **kwargs):
        # The first statements execute; the final statement fails on the server.
        return execute(self, query + "; SELECT 1/0", params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ClientCursor, "execute", fail_final_setup)
        with pytest.raises(case.psycopg.errors.DivisionByZero):
            with case.db.transaction(actor=case.actor):
                reached.append(True)
    assert reached == []
    with closing(case.db.connect()) as conn:
        assert conn.execute("SELECT current_user=session_user").fetchone()[0]
        assert not conn.execute("SELECT current_setting('app.user_id',true)").fetchone()[0]
    with case.db.transaction(actor=case.actor) as conn:
        assert conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchone()


def test_setup_does_not_accept_arbitrary_roles_or_nest_transactions(case):
    with closing(case.db.connect()) as conn:
        with pytest.raises(ValueError, match="事务角色"):
            conn.begin(role="accounting_app; RESET ROLE")
        assert conn.execute("SELECT current_user=session_user").fetchone()[0]
    with case.db.transaction(actor=case.actor) as conn:
        with pytest.raises(RuntimeError, match="未完成事务"):
            conn.begin(write=True, role="accounting_system")
        assert conn.execute("SELECT current_user").fetchone()[0] == "accounting_app"
        assert conn.execute("SELECT current_setting('transaction_read_only')").fetchone()[0] == "on"


def test_revocation_is_rechecked_in_next_borrowed_transaction(case):
    actor = peer(case, firm_role="reviewer", book_role="reviewer")
    with case.db.transaction(actor=actor) as conn:
        assert conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchone()
    with case.psycopg.connect(ADMIN_URL) as writer:
        writer.execute("DELETE FROM accounting.book_access WHERE user_id=%s", (actor.user_id,))
    with case.db.transaction(actor=actor) as conn:
        assert not conn.execute("SELECT id FROM books WHERE id=?", (case.book,)).fetchall()


def test_unchanged_identity_does_not_wait_for_accounting_write_lock(case):
    from app.accounting.directory import DirectoryService

    directory = DirectoryService(case.db)
    with case.db.transaction(actor=case.actor) as conn:
        row = conn.execute(
            "SELECT name,email FROM users WHERE id=?", (case.actor.user_id,)
        ).fetchone()
    identity = SimpleNamespace(
        user_id=case.actor.user_id, display_name=row["name"], email=row["email"]
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        with case.db.transaction(write=True, actor=case.actor):
            # The unchanged profile must finish while the global accounting
            # write lock is still held by another connection.
            future = executor.submit(directory.register_identity, identity)
            try:
                future.result(timeout=2)
                completed_while_locked = True
            except TimeoutError:
                completed_while_locked = False
        future.result(timeout=5)
    assert completed_while_locked
