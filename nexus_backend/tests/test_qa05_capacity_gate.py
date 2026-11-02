"""Capacity acceptance must not silently turn rejected requests into success."""

import inspect

from tools.capacity import qa_mixed


def test_capacity_acceptance_defaults_to_no_shedding():
    parameter = inspect.signature(qa_mixed.run).parameters.get("allow_shed")
    assert parameter is not None and parameter.default is False


def test_admission_settings_are_observable_without_tenant_counts():
    from app.accounting.database import Database

    class Pool:
        max_waiting = 64
        timeout = 5.0

        def get_stats(self):
            return {
                "pool_size": 4,
                "pool_max": 4,
                "pool_available": 1,
                "requests_num": 12,
                "requests_waiting": 50,
            }

    db = object.__new__(Database)
    db._pool = Pool()
    snapshot = db.pool_snapshot()
    assert snapshot["pool_max_waiting"] == 64
    assert snapshot["pool_timeout_seconds"] == 5.0
    assert "requests_waiting" not in snapshot and "requests_num" not in snapshot
