"""A SQL caller cannot reuse a transaction identity by caching BEGIN or tracing."""

from app.accounting.database import Database


def identity(conn):
    return conn.execute("SELECT accounting_transaction_id()").fetchone()[0]


def test_transaction_identity_changes_on_reused_connection_and_rollback(tmp_path):
    db = Database(tmp_path / "transaction-identity.db")
    conn = db.connect()
    try:
        assert identity(conn) == ""
        conn.execute("BEGIN")
        first = identity(conn)
        assert first and identity(conn) == first
        conn.commit()
        assert identity(conn) == ""
        conn.execute("BEGIN")  # Same cached SQL on the same connection.
        second = identity(conn)
        assert second and second != first
        conn.rollback()
        assert identity(conn) == ""
        conn.execute("/* harmless diagnostic comment */ BEGIN")
        third = identity(conn)
        assert third and third not in {first, second}
        conn.rollback()
    finally:
        conn.close()


def test_external_query_tracing_cannot_disable_internal_transaction_identity(tmp_path):
    db = Database(tmp_path / "transaction-tracing.db")
    conn = db.connect()
    queries = []
    try:
        conn.set_trace_callback(queries.append)
        conn.execute("BEGIN")
        first = identity(conn)
        conn.execute("SELECT 1")
        assert identity(conn) == first
        conn.commit()
        conn.set_trace_callback(None)
        conn.execute("BEGIN")
        assert identity(conn) and identity(conn) != first
        conn.rollback()
        assert any("SELECT 1" in sql for sql in queries)
        assert identity(conn) == ""
    finally:
        conn.close()


def test_implicit_begin_and_savepoint_keep_only_current_transaction_identity(tmp_path):
    db = Database(tmp_path / "transaction-savepoint.db")
    conn = db.connect()
    try:
        conn.execute("CREATE TEMP TABLE synthetic_values(value INTEGER)")
        conn.execute("INSERT INTO synthetic_values VALUES(1)")
        first = identity(conn)
        assert first
        conn.execute("SAVEPOINT partial_change")
        conn.execute("INSERT INTO synthetic_values VALUES(2)")
        conn.execute("ROLLBACK TO partial_change")
        conn.execute("RELEASE partial_change")
        assert identity(conn) == first
        conn.commit()
        conn.execute("INSERT INTO synthetic_values VALUES(1)")
        second = identity(conn)
        assert second and second != first
        conn.rollback()
        conn.execute("SAVEPOINT outer_scope")
        third = identity(conn)
        assert third and third not in {first, second}
        conn.execute("RELEASE outer_scope")
        assert identity(conn) == ""
    finally:
        conn.close()
