"""Permanent PostgreSQL runtime, FORCE RLS and worker-capability checks."""
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.operations.efficiency import EfficiencyService
from app.operations.efficiency_models import SessionStart, SessionCommand
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres, insert_event
from test_queue_postgres import WORKER_URL, worker

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要独立 PG 管理员、API 及 worker 测试连接")


def start(c, actor=None, book=None):
    return EfficiencyService(c.db).start(actor or c.reviewer, SessionStart(book_id=book or c.book, activity="review", idempotency_key=uuid4().hex))


def command(row, action="pause", key=None):
    return SessionCommand(action=action, expected_version=row["version"], idempotency_key=key or uuid4().hex)


def test_pg_sessions_force_rls_and_server_bounded_replay(business_case):
    c = business_case
    efficiency = EfficiencyService(c.db)
    row = start(c)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.human_work_sessions SET last_active_at=(clock_timestamp()-interval '6 hours')::text WHERE id=%s", (row["id"],))
    request = command(row, "heartbeat")
    result = efficiency.command(c.reviewer, row["id"], request)
    assert result["credited_seconds"] == 60 and result["version"] == 2
    assert efficiency.command(c.reviewer, row["id"], request) == result
    with c.db.transaction(actor=c.viewer) as conn:
        assert not conn.execute("SELECT * FROM human_work_sessions WHERE id=?", (row["id"],)).fetchall()
    with c.psycopg.connect(ADMIN_URL) as conn:
        for table in ("human_work_sessions", "human_work_commands", "ops_company_dirty", "ops_company_refresh"):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("accounting." + table,)).fetchone() == (True, True)


def test_pg_employee_own_scope_and_revocation_before_commands(business_case):
    c = business_case
    efficiency = EfficiencyService(c.db)
    event = insert_event(c)
    row = efficiency.start(c.employee, SessionStart(event_id=event, activity="business_entry", idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError):
        efficiency.command(c.colleague, row["id"], command(row))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.employee) as conn:
            conn.execute("UPDATE human_work_sessions SET credited_seconds=20000 WHERE id=?", (row["id"],))
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s", (c.company, c.employee.user_id))
    assert efficiency.own(c.employee)["total"] == 0
    with pytest.raises(AccountingError):
        efficiency.command(c.employee, row["id"], command(row))


def test_pg_native_capability_denies_other_actor_and_fake_time(business_case):
    c = business_case
    row = start(c)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.employee) as conn:
            conn.execute("SELECT accounting.advance_human_work_session(?,?,?,?,?)", (row["id"], row["version"], "heartbeat", uuid4().hex, "a" * 64))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("INSERT INTO human_work_sessions(id,firm_id,company_id,user_id,book_id,activity,state,credited_seconds,started_at,last_active_at,updated_at,version,start_key,start_fingerprint) VALUES(?,?,?,?,?,'review','active',10000,clock_timestamp()::text,clock_timestamp()::text,clock_timestamp()::text,1,?,?)", (uuid4().hex, c.owner.firm_id, c.company, c.employee.user_id, c.book, uuid4().hex, "a" * 64))


def test_pg_incremental_queue_worker_only_counts_and_no_payload_reads(business_case):
    c = business_case
    insert_event(c)
    with c.psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute("SELECT revision,dirty_at FROM accounting.ops_company_dirty WHERE firm_id=%s AND company_id=%s", (c.owner.firm_id, c.company)).fetchone()
        assert row and row[0] >= 1
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute("SELECT accounting.ops_refresh_dirty(?,?)", (1, 0))
    with worker(c) as conn:
        result = conn.execute("SELECT accounting.ops_refresh_dirty(%s,%s)", (1, 0)).fetchone()
        assert next(iter(result.values())) <= 1
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with worker(c) as conn:
            conn.execute("SELECT * FROM accounting.ops_company_dirty")
    with c.db.transaction(actor=c.employee) as conn:
        assert not conn.execute("SELECT * FROM ops_company_refresh WHERE firm_id=?", (c.owner.firm_id,)).fetchall()
