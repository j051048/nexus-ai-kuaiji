"""SQL queue fixture eligibility is independent of the Python host clock."""

from datetime import datetime, timedelta, timezone

import pytest

from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_queue_postgres import (
    ADMIN_URL,
    APP_URL,
    WORKER_URL,
    claim,
    enqueue,
    finish_job,
    queue_case,  # noqa: F401
)

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or not WORKER_URL, reason="Requires isolated PG roles"
)


@pytest.mark.parametrize("skew_seconds", [-30, 30])
def test_immediate_sql_fixture_uses_database_clock(queue_case, monkeypatch, skew_seconds):
    import test_queue_postgres as fixture

    class SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=skew_seconds)

    monkeypatch.setattr(fixture, "datetime", SkewedDatetime)
    job = enqueue(queue_case)
    claimed = claim(queue_case)
    assert claimed is not None and claimed["id"] == job["id"]
    assert claimed["attempt_count"] == 1
    assert finish_job(queue_case, claimed)


def test_explicit_delayed_sql_fixture_remains_unclaimable(queue_case):
    with queue_case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        later = conn.execute("SELECT clock_timestamp() + interval '1 hour'").fetchone()[0]
    job = enqueue(queue_case, available_at=later.astimezone(timezone.utc).isoformat())
    assert job["available_at"] == later.astimezone(timezone.utc).isoformat()
    assert claim(queue_case) is None
