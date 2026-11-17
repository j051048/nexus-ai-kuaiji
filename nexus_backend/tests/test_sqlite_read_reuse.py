from app.accounting.database import Database
from app.accounting.domain import Actor


def test_read_reuse_keeps_connection_per_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_SQLITE_READ_REUSE", "1")
    db = Database(tmp_path / "reuse.db")
    actor = Actor("user-1", "firm-1")
    try:
        with db.transaction(actor=actor) as first:
            first.execute("SELECT COUNT(*) FROM users").fetchone()
        with db.transaction(actor=actor) as second:
            second.execute("SELECT COUNT(*) FROM users").fetchone()
        assert first is second
    finally:
        db.close()


def test_read_reuse_is_opt_in_and_closes_on_database_close(tmp_path, monkeypatch):
    monkeypatch.delenv("ACCOUNTING_SQLITE_READ_REUSE", raising=False)
    db = Database(tmp_path / "closed.db")
    actor = Actor("user-1", "firm-1")
    with db.transaction(actor=actor) as conn:
        conn.execute("SELECT 1").fetchone()
        closed_id = id(conn)
    db.close()
    assert closed_id
