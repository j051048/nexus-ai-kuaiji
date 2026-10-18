"""Opt-in real PostgreSQL regression; only a dedicated loopback test_ database.

These deliberately do not consume the suite's generic/inherited PostgreSQL
URLs. Set the two ACCOUNTING_OBSERVABILITY_TEST_* URLs explicitly to exercise
the runtime role and SECURITY DEFINER boundary in an isolated local database.
"""

from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.accounting.service import AccountingService
from app.observability.service import FirmObservabilityService


@pytest.fixture(scope="module")
def health_postgres():
    admin_url = os.getenv("ACCOUNTING_OBSERVABILITY_TEST_POSTGRES_URL")
    app_url = os.getenv("ACCOUNTING_OBSERVABILITY_TEST_DATABASE_URL")
    if not admin_url or not app_url:
        pytest.skip("需显式配置独立本机 test_ PostgreSQL 管理与受限 API 测试连接")
    targets = [urlsplit(value) for value in (admin_url, app_url)]
    for target in targets:
        if (
            target.scheme not in {"postgres", "postgresql"}
            or target.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not target.path.startswith("/test_")
            or target.query
            or target.fragment
        ):
            pytest.fail("可观测性 PostgreSQL 回归只允许本机独立 test_ 数据库，无连接参数覆盖")
    if any((target.hostname, target.port, target.path) != (targets[0].hostname, targets[0].port, targets[0].path) for target in targets[1:]):
        pytest.fail("管理连接与受限 API 连接必须指向同一本机测试数据库")
    import psycopg
    from scripts.migrate_accounting import migrate

    migrate(admin_url)
    db = Database(app_url)
    yield SimpleNamespace(db=db, admin_url=admin_url, psycopg=psycopg)
    db.close()


@pytest.fixture
def health_case(health_postgres):
    c = health_postgres
    suffix = uuid4().hex
    firm, other_firm = "health-firm-" + suffix, "health-other-" + suffix
    owner, reviewer, inactive = ("health-" + role + "-" + suffix for role in ("owner", "reviewer", "inactive"))
    other_owner, worker = "health-other-owner-" + suffix, "private-worker-" + suffix
    stamp = datetime.now(timezone.utc).isoformat()
    with c.psycopg.connect(c.admin_url) as conn:
        for user_id in (owner, reviewer, inactive, other_owner):
            conn.execute("INSERT INTO accounting.users(id,name,active) VALUES(%s,'合成可观测性测试人员',%s)", (user_id, 0 if user_id == inactive else 1))
        for firm_id, created_by in ((firm, owner), (other_firm, other_owner)):
            conn.execute("INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'合成隔离事务所',%s)", (firm_id, created_by))
        for firm_id, user_id, role in ((firm, owner, "owner"), (firm, reviewer, "reviewer"), (firm, inactive, "admin"), (other_firm, other_owner, "owner")):
            conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,%s)", (firm_id, user_id, role))
        conn.execute("INSERT INTO accounting.worker_heartbeats VALUES(%s,%s,%s,'active',2)", (worker, stamp, stamp))
    yield SimpleNamespace(**vars(c), owner=Actor(owner, firm), reviewer=Actor(reviewer, firm), inactive=Actor(inactive, firm), switched=Actor(owner, other_firm), worker=worker)
    with c.psycopg.connect(c.admin_url) as conn:
        conn.execute("DELETE FROM accounting.worker_heartbeats WHERE worker_id=%s", (worker,))


def test_real_restricted_runtime_snapshot_reads_only_bounded_worker_health(health_case):
    c = health_case
    snapshot = FirmObservabilityService(c.db, AccountingService(c.db)).snapshot(c.owner)
    assert snapshot["worker_health"]["available"] is True
    assert set(snapshot["worker_health"]) == {"available", "last_heartbeat_at", "stale_after_seconds"}
    assert snapshot["workers"] == []
    assert c.worker not in json.dumps(snapshot)
    assert "inflight_count" not in json.dumps(snapshot)
    # The fix must not accidentally grant the runtime access to global rows.
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(actor=c.owner) as conn:
            conn.execute("SELECT worker_id,inflight_count FROM worker_heartbeats")


def test_database_function_rechecks_current_manager_without_service_guard(health_case):
    c = health_case
    for actor in (c.reviewer, c.inactive, c.switched, Actor("", "")):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            with c.db.transaction(actor=actor) as conn:
                conn.execute("SELECT accounting.firm_worker_health()")
    with c.psycopg.connect(c.admin_url) as conn:
        for role in ("accounting_worker", "accounting_system"):
            assert not conn.execute("SELECT has_function_privilege(%s,'accounting.firm_worker_health()','EXECUTE')", (role,)).fetchone()[0]
