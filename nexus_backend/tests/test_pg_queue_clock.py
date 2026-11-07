from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pg_queue_clock import wait_until_due


def fixture_clock(counts):
    reads, elapsed = [], [0.0]

    @contextmanager
    def connect(url):
        assert url == "isolated"

        def execute(sql, args=None):
            reads.append((sql, args))
            if sql.startswith("SELECT"):
                value = counts.pop(0) if len(counts) > 1 else counts[0]
                return SimpleNamespace(fetchone=lambda: (value,))

        yield SimpleNamespace(execute=execute)

    def sleep(seconds):
        elapsed[0] += seconds

    return connect, reads, lambda: elapsed[0], sleep


def test_clock_waits_for_all_exact_jobs_without_changing_queue_state():
    connect, reads, clock, sleep = fixture_clock([0, 1, 2])
    wait_until_due(connect, "isolated", "ocr_jobs", ["a", "b"], clock=clock, sleep=sleep)
    assert clock() == 0.02
    assert reads[0] == ("SET TRANSACTION READ ONLY", None)
    assert len(reads) == 4
    assert all(sql.startswith("SELECT") and args == (["a", "b"],) for sql, args in reads[1:])


def test_clock_mismatch_is_bounded_and_fails_without_claiming():
    connect, reads, clock, sleep = fixture_clock([0])
    with pytest.raises(AssertionError, match="database due time"):
        wait_until_due(
            connect, "isolated", "assistant_turns", ["a"], timeout=0.02, clock=clock, sleep=sleep
        )
    assert clock() == 0.02
    assert not any("UPDATE" in sql or "claim" in sql for sql, _ in reads)


@pytest.mark.parametrize(
    "table,ids", [("untrusted_sql", ["a"]), ("ocr_jobs", []), ("ocr_jobs", ["a", "a"])]
)
def test_clock_rejects_unbounded_or_wrong_identity(table, ids):
    def connect(url):
        pytest.fail("Invalid input must not open a connection")

    with pytest.raises(ValueError):
        wait_until_due(connect, "isolated", table, ids)


@pytest.mark.parametrize("explicit", [None, "2099-01-01T00:00:00+00:00"])
def test_sql_enqueue_uses_database_due_time_and_preserves_explicit_delay(explicit):
    from test_queue_postgres import enqueue

    stored = []

    def execute(sql, values=None):
        if sql == "SELECT clock_timestamp()":
            from datetime import datetime, timezone

            return SimpleNamespace(fetchone=lambda: (datetime(2020, 1, 1, tzinfo=timezone.utc),))
        if sql.startswith("SELECT id FROM ocr_batches"):
            return SimpleNamespace(fetchone=lambda: ("batch",))
        assert sql.startswith("INSERT INTO ocr_jobs(")
        columns = sql.split("(", 1)[1].split(")", 1)[0].split(",")
        stored.append(dict(zip(columns, values)))

    @contextmanager
    def transaction(**kwargs):
        yield SimpleNamespace(execute=execute)

    actor = SimpleNamespace(firm_id="firm", user_id="employee")
    case = SimpleNamespace(
        employee=actor, company="company", db=SimpleNamespace(transaction=transaction)
    )
    result = enqueue(
        case,
        event_id="event",
        attachment_id="attachment",
        **({"available_at": explicit} if explicit else {}),
    )
    expected = explicit or "2020-01-01T00:00:00+00:00"
    assert stored[0]["available_at"] == result["available_at"] == expected
