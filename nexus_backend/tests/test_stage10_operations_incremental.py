from datetime import timedelta
import pytest
from app.accounting.models import BookCreate
from app.accounting.seed import LOCAL_ACTOR
from app.operations.telemetry import TelemetryService
from test_model_cost_workflow import cost_case
from test_intelligence_workflow import ai_case
from test_stage7_proposal_queue import proposal_cost_case
from test_stage7_operations import ops_case, ready, open_codes


def test_dirty_batch_is_bounded_and_rechecks_deadlines(ops_case):
    c = ops_case
    ready(c)
    with c.db.transaction(write=True) as conn:
        before = conn.execute("SELECT COUNT(*) FROM ops_company_dirty").fetchone()[0]
        assert before >= 1
        assert c.operations.refresh_pending(conn, batch_limit=1, deadline_limit=0) == 1
        assert conn.execute("SELECT COUNT(*) FROM ops_company_dirty").fetchone()[0] == before - 1
        assert c.operations.refresh_pending(conn, batch_limit=100, deadline_limit=0) <= 100
        assert c.operations.refresh_pending(conn, batch_limit=100, deadline_limit=0) == 0
    c.clock.advance(61)
    with c.db.transaction(write=True) as conn:
        assert c.operations.refresh_pending(conn, batch_limit=1, deadline_limit=1) == 1
    assert "worker_unavailable" in open_codes(c.operations.overview(c.manager, c.company["id"]))


def test_heartbeat_never_requires_company_scan(ops_case, monkeypatch):
    c = ops_case
    def fail(*args):
        raise AssertionError("heartbeat cannot compute company aggregates")
    monkeypatch.setattr(c.operations, "_refresh_alerts", fail)
    with c.db.transaction(write=True) as conn:
        assert c.operations.heartbeat(conn, "stage10-heartbeat", 0, "idle")
        assert conn.execute("SELECT state FROM worker_heartbeats WHERE worker_id='stage10-heartbeat'").fetchone()[0] == "idle"


def test_dirty_rows_contain_only_scope_and_fair_first_change(ops_case):
    c = ops_case
    with c.db.transaction(write=True) as conn:
        value = dict(conn.execute("SELECT * FROM ops_company_dirty WHERE firm_id=? AND company_id=?", (c.manager.firm_id, c.company["id"])).fetchone())
        assert set(value) == {"firm_id", "company_id", "dirty_at", "revision"}
        conn.execute("UPDATE model_company_policies SET queue_wait_minutes=queue_wait_minutes+1,version=version+1 WHERE firm_id=? AND company_id=?", (c.manager.firm_id, c.company["id"]))
        updated = dict(conn.execute("SELECT * FROM ops_company_dirty WHERE firm_id=? AND company_id=?", (c.manager.firm_id, c.company["id"])).fetchone())
        assert updated["revision"] > value["revision"] and updated["dirty_at"] == value["dirty_at"]


def test_manager_telemetry_is_current_process_and_does_not_attribute_unmarked_transactions(ops_case):
    c = ops_case
    telemetry = TelemetryService(c.db, c.operations)
    value = telemetry.overview(c.manager, c.company["id"])
    assert value["transactions"]["scope"] == "current_process" and value["transactions"]["count"] >= 1
    with pytest.raises(Exception):
        telemetry.overview(c.actors["employee"], c.company["id"])
    with pytest.raises(Exception):
        telemetry.overview(c.actors["finance"], c.company["id"])
    assert value["limits"]["global_model_concurrency"] == 2
