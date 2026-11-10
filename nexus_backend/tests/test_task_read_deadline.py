import time
from types import SimpleNamespace
import pytest
from app.accounting.database import Database


def test_parallel_deadline_limits_pool_wait_without_changing_pool():
    from app.accounting.time_budget import database_deadline
    from psycopg_pool import PoolTimeout

    seen = []

    def getconn(*, timeout=None):
        seen.append(timeout)
        raise PoolTimeout()

    db = Database.__new__(Database)
    db._closed, db.is_postgres = False, True
    pool = SimpleNamespace(getconn=getconn, timeout=30)
    db._postgres_pool = lambda: pool
    with database_deadline(time.monotonic() + 0.1):
        with pytest.raises(Exception):
            db.connect()
    assert 0 < seen[0] <= 0.1
    assert pool.timeout == 30
    with pytest.raises(Exception):
        db.connect()
    assert seen[1] is None


def test_expired_deadline_never_borrows_connection():
    from app.accounting.time_budget import database_deadline

    db = Database.__new__(Database)
    db._closed, db.is_postgres = False, True

    def forbidden():
        pytest.fail("expired task must not enter pool")

    db._postgres_pool = forbidden
    with database_deadline(time.monotonic() - 1):
        with pytest.raises(TimeoutError):
            db.connect()
