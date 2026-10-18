"""Regression: firm observability uses bounded health, not global worker rows."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError, Actor
from app.accounting.models import CompanyCreate, FirmCreate
from app.observability.service import FirmObservabilityService
from test_business import business


def heartbeat(case, worker_id, *, state="idle", age_seconds=0, inflight=0):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    with case.db.transaction(write=True, system=True) as conn:
        conn.execute(
            "INSERT INTO worker_heartbeats(worker_id,started_at,heartbeat_at,state,inflight_count) "
            "VALUES(?,?,?,?,?)",
            (worker_id, stamp, stamp, state, inflight),
        )
    return stamp


def test_snapshot_exposes_only_health_not_shared_worker_identity_or_load(business):
    c = business
    stamp = heartbeat(c, "private-host-process-for-other-firm", state="active", inflight=2)
    heartbeat(c, "private-stale-process", age_seconds=3600)
    result = FirmObservabilityService(c.db, c.service).snapshot(c.manager)
    assert result["worker_health"] == {
        "available": True,
        "last_heartbeat_at": stamp,
        "stale_after_seconds": 90,
    }
    # Old clients still access .workers.filter/.length during a rolling deploy.
    assert result["workers"] == []
    serialized = json.dumps(result)
    assert "private-host-process" not in serialized
    assert "private-stale-process" not in serialized
    assert "inflight_count" not in serialized
    assert "worker_id" not in serialized


@pytest.mark.parametrize("state,age", [("idle", 91), ("active", 3600), ("stopped", 0)])
def test_old_or_stopped_heartbeats_are_not_available(business, state, age):
    c = business
    stamp = heartbeat(c, "unavailable-worker", state=state, age_seconds=age)
    health = FirmObservabilityService(c.db, c.service).snapshot(c.manager)["worker_health"]
    assert health == {
        "available": False,
        "last_heartbeat_at": stamp,
        "stale_after_seconds": 90,
    }


def test_no_heartbeats_is_an_explicit_unavailable_state(business):
    c = business
    health = FirmObservabilityService(c.db, c.service).snapshot(c.manager)["worker_health"]
    assert health == {
        "available": False,
        "last_heartbeat_at": None,
        "stale_after_seconds": 90,
    }


@pytest.mark.parametrize("role", ["employee", "finance", "company_admin", "accountant"])
def test_non_firm_manager_is_denied_before_any_worker_query(business, monkeypatch, role):
    c = business
    service = FirmObservabilityService(c.db, c.service)
    def must_not_query_workers(conn):
        raise AssertionError("unauthorized callers must not read shared health")
    monkeypatch.setattr(service, "_worker_health", must_not_query_workers)
    with pytest.raises(AccountingError) as failure:
        service.snapshot(c.actors[role])
    assert failure.value.status == 403


def test_manager_cannot_switch_to_another_firm_and_projection_stays_scoped(business):
    c = business
    identity = SimpleNamespace(user_id="other-firm-owner", display_name="另所管理人", email="other-firm-owner@example.test")
    c.directory.register_identity(identity)
    firm = c.directory.create_firm(identity.user_id, FirmCreate(name="另一个隔离事务所"))
    manager = Actor(identity.user_id, firm["id"])
    company = c.directory.create_company(manager, CompanyCreate(name="另所客户"))
    service = FirmObservabilityService(c.db, c.service)
    with pytest.raises(AccountingError) as failure:
        service.snapshot(Actor(c.manager.user_id, firm["id"]))
    assert failure.value.status == 403
    current = service.snapshot(c.manager)
    other = service.snapshot(manager)
    assert current["projection"]["total"] == 2
    assert other["projection"]["total"] == 1
    assert company["id"] not in json.dumps(current)
    assert c.company["id"] not in json.dumps(other)


@pytest.mark.parametrize("json_string", [False, True])
def test_postgres_branch_calls_only_the_guarded_aggregate(json_string):
    expected = {"available": True, "last_heartbeat_at": "2026-10-18T12:00:00+00:00", "stale_after_seconds": 90}
    statements = []
    class RestrictedConnection:
        def execute(self, sql):
            statements.append(sql)
            if sql != "SELECT accounting.firm_worker_health()":
                raise AssertionError("runtime must not read worker_heartbeats directly")
            return SimpleNamespace(fetchone=lambda: [json.dumps(expected) if json_string else expected])
    service = FirmObservabilityService(SimpleNamespace(is_postgres=True), SimpleNamespace(access=None))
    assert service._worker_health(RestrictedConnection()) == expected
    assert statements == ["SELECT accounting.firm_worker_health()"]
