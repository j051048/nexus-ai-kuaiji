"""Real PostgreSQL gates, native scope protection and cross-process fleet caps."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import os
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor
from app.accounting.models import BookCreate
from app.concurrency.provider import _slot, _release
from app.jobs.worker import QueueExecution
from test_postgres_integration import ADMIN_URL, APP_URL, postgres, case, peer
from test_queue_worker_postgres import WORKER_URL, pg_queue, pg_ai, business_case, business_postgres
from test_queue_fairness_postgres import (
    new_company_input,
    batch_request,
    distinct_first_company_event,
)
from test_model_cost_postgres_workflow import pg_cost
from test_intelligence_workflow import create_event, extract
from app.operations.meter import BudgetError

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL or not WORKER_URL,
    reason="需要隔离 PostgreSQL 管理员、API 与 worker DSN",
)


def audit(conn, c, book):
    conn.execute(
        "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,'synthetic.company.lock',?,'{}',CURRENT_TIMESTAMP::text)",
        (c.actor.firm_id, book, c.actor.user_id, uuid4().hex),
    )


def test_real_pg_distinct_company_writes_overlap_and_same_company_serializes(case):
    c = case
    other = c.service.create_book(c.actor, BookCreate(name="并发验证第二客户", period="2026-10"))
    barrier = Barrier(2)

    def company_write(company, book):
        with c.db.transaction(
            write=True, actor=c.actor, company_id=company, write_scope="company"
        ) as conn:
            audit(conn, c, book)
            barrier.wait(3)  # A global lock would break this deterministic proof.

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(company_write, c.company, c.book)
        b = pool.submit(company_write, other["company_id"], other["id"])
        a.result()
        b.result()
    entered, release, second_entered = Event(), Event(), Event()

    def first():
        with c.db.transaction(
            write=True, actor=c.actor, company_id=c.company, write_scope="company"
        ):
            entered.set()
            assert release.wait(3)

    def second():
        with c.db.transaction(
            write=True, actor=c.actor, company_id=c.company, write_scope="company"
        ):
            second_entered.set()

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(first)
        assert entered.wait(3)
        b = pool.submit(second)
        assert not second_entered.wait(0.15)
        release.set()
        a.result()
        b.result()
        assert second_entered.is_set()


def test_real_pg_legacy_exclusive_gate_blocks_company_scope_and_native_cross_scope(case):
    c = case
    entered, release, scope_entered = Event(), Event(), Event()

    def legacy():
        with c.db.transaction(write=True, actor=c.actor):
            entered.set()
            assert release.wait(3)

    def scoped():
        with c.db.transaction(
            write=True, actor=c.actor, company_id=c.company, write_scope="company"
        ):
            scope_entered.set()

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(legacy)
        assert entered.wait(3)
        b = pool.submit(scoped)
        assert not scope_entered.wait(0.15)
        release.set()
        a.result()
        b.result()
    other = c.service.create_book(c.actor, BookCreate(name="原生 SQL 范围验证", period="2026-10"))
    for statement, params in [
        ("SELECT accounting.lock_legacy_write()", ()),
        ("UPDATE books SET name='非法跨范围写' WHERE id=?", (other["id"],)),
        ("SELECT accounting.begin_company_write(?,?)", (c.actor.firm_id, other["company_id"])),
    ]:
        with pytest.raises(Exception):
            with c.db.transaction(
                write=True, actor=c.actor, company_id=c.company, write_scope="company"
            ) as conn:
                conn.execute(statement, params)
    with pytest.raises(Exception):
        with c.db.transaction(
            write=True,
            actor=peer(c, firm_role="owner", other_firm=True),
            company_id=c.company,
            write_scope="company",
        ):
            pass


def test_real_pg_native_statement_takes_gate_before_row_lock(case):
    c = case
    started = Event()
    writer_pid = {}

    def raw_policy_writer():
        # Deliberately bypass Python transaction() to exercise the native gate.
        with closing(c.db.connect()) as conn:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL ROLE accounting_app")
            conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
            conn.execute(
                "SELECT set_config('app.user_id',?,true),set_config('app.firm_id',?,true)",
                (c.actor.user_id, c.actor.firm_id),
            )
            writer_pid["value"] = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            started.set()
            conn.execute("UPDATE books SET name='合成原生更新' WHERE id=?", (c.book,))
            conn.commit()

    with ThreadPoolExecutor(1) as pool:
        with c.db.transaction(
            write=True, actor=c.actor, company_id=c.company, write_scope="company"
        ) as conn:
            waiting = pool.submit(raw_policy_writer)
            assert started.wait(3)
            assert not waiting.done()
            deadline = monotonic() + 3
            with c.psycopg.connect(ADMIN_URL) as observation:
                while monotonic() < deadline:
                    blocked = observation.execute(
                        "SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted",
                        (writer_pid["value"],),
                    ).fetchone()
                    if blocked:
                        break
                    sleep(0.01)
                assert blocked, "Native statement did not reach the compatibility gate"
            conn.execute("SET LOCAL lock_timeout='500ms'")
            # If the raw writer took the row before waiting for our shared gate,
            # this would time out (or deadlock). Statement gate prevents it.
            conn.execute("SELECT id FROM books WHERE id=? FOR UPDATE", (c.book,)).fetchone()
        waiting.result(3)


def test_real_pg_two_company_ocr_result_transactions_overlap_without_reducing_permissions(
    pg_queue, monkeypatch
):
    c = pg_queue
    first = distinct_first_company_event(c, 71)
    company, actor, event = new_company_input(c, "并发票据第二企业")
    one = c.queue.enqueue(c.actors["employee"], batch_request(c.company["id"], [first]))
    two = c.queue.enqueue(actor, batch_request(company["id"], [event]))
    original = QueueExecution.inputs
    barrier = Barrier(2)
    seen = set()

    def checked(execution, current, attachment):
        original(execution, current, attachment)
        if execution.job["id"] not in seen:
            seen.add(execution.job["id"])
            barrier.wait(3)

    monkeypatch.setattr(QueueExecution, "inputs", checked)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(c.worker.run_once)
        b = pool.submit(c.worker.run_once)
        assert a.result() and b.result()
    for owner, batch in [(c.actors["employee"], one), (actor, two)]:
        assert c.queue.get_batch(owner, batch["id"])["progress"]["succeeded"] == 1
    with pytest.raises(Exception):
        c.queue.get_batch(actor, one["id"])


def test_real_pg_supplier_slots_recheck_grants_preserve_privacy_and_expired_slots_recover(case):
    c = case
    supplier = "slot.synthetic.test"
    one, two = uuid4().hex, uuid4().hex
    assert _slot(c.db, c.actor, c.company, supplier, one, 30)
    assert not _slot(c.db, c.actor, c.company, "different.synthetic.test", two, 30)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.provider_request_slots SET lease_until=0 WHERE id=%s", (one,)
        )
    assert _slot(c.db, c.actor, c.company, supplier, two, 30)
    with pytest.raises(Exception):
        with c.db.transaction(actor=c.actor) as conn:
            conn.execute("SELECT * FROM provider_request_slots")
    outsider = peer(c, firm_role="owner", other_firm=True)
    with pytest.raises(Exception):
        _slot(c.db, outsider, c.company, supplier, uuid4().hex, 30)
    _release(c.db, outsider, two)
    assert not _slot(c.db, c.actor, c.company, supplier, uuid4().hex, 30)
    _release(c.db, c.actor, two)
    assert _slot(c.db, c.actor, c.company, supplier, one, 30)
    _release(c.db, c.actor, one)


def test_real_pg_fleet_ceiling_and_company_round_robin_survive_more_worker_slots(pg_queue):
    c = pg_queue
    inputs = [
        (
            c.company,
            c.actors["employee"],
            [distinct_first_company_event(c, n) for n in (91, 92, 93)],
        )
    ]
    for number in range(3):
        company, actor, event = new_company_input(c, f"有界扩容合成客户{number}")
        inputs.append((company, actor, [event]))
    for company, actor, events in inputs:
        c.queue.enqueue(actor, batch_request(company["id"], events))
    with c.psycopg.connect(ADMIN_URL) as conn:
        previous = conn.execute(
            "SELECT processing_global_limit FROM accounting.processing_runtime_limits WHERE singleton=1"
        ).fetchone()[0]
        conn.execute(
            "UPDATE accounting.processing_runtime_limits SET processing_global_limit=3 WHERE singleton=1"
        )
    held = []
    try:
        for number in range(3):
            job = c.worker.queue.claim(f"bounded-synthetic-worker-{number}", 180, 8)
            assert job is not None
            held.append(job)
        assert len({j["company_id"] for j in held}) == 3
        assert c.worker.queue.claim("over-global-cap", 180, 8) is None
        assert c.worker._failure(
            held[0], "cancelled", "synthetic_cleanup", "仅合成调度验证，无模型请求"
        )
        next_job = c.worker.queue.claim("next-company-turn", 180, 8)
        held.append(next_job)
        assert next_job and next_job["company_id"] not in {j["company_id"] for j in held[:-1]}
        assert c.worker.queue.claim("still-over-cap", 180, 8) is None
    finally:
        for job in held:
            c.worker._failure(job, "cancelled", "synthetic_cleanup", "仅合成调度验证，无模型请求")
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.processing_runtime_limits SET processing_global_limit=%s WHERE singleton=1",
                (previous,),
            )


def test_real_pg_provider_specific_and_fleet_limits_are_native_not_process_local(case):
    c = case
    companies = [c.company] + [
        c.service.create_book(c.actor, BookCreate(name=f"供应商容量客户{n}", period="2026-10"))[
            "company_id"
        ]
        for n in range(3)
    ]
    ids = [uuid4().hex for _ in companies]
    supplier = "capped.synthetic.test"
    with c.psycopg.connect(ADMIN_URL) as conn:
        original = conn.execute(
            "SELECT provider_global_limit,provider_default_limit FROM accounting.processing_runtime_limits WHERE singleton=1"
        ).fetchone()
        conn.execute(
            "UPDATE accounting.processing_runtime_limits SET provider_global_limit=3,provider_default_limit=2 WHERE singleton=1"
        )
        conn.execute(
            "INSERT INTO accounting.provider_runtime_limits(provider,max_inflight) VALUES(%s,1)",
            (supplier,),
        )
    try:
        assert _slot(c.db, c.actor, companies[0], supplier, ids[0], 30)
        assert not _slot(c.db, c.actor, companies[1], supplier, ids[1], 30)
        assert _slot(c.db, c.actor, companies[1], "other.synthetic.test", ids[1], 30)
        assert _slot(c.db, c.actor, companies[2], "other.synthetic.test", ids[2], 30)
        assert not _slot(c.db, c.actor, companies[3], "third.synthetic.test", ids[3], 30)
        with pytest.raises(Exception):
            with c.db.transaction(write=True, actor=c.actor) as conn:
                conn.execute("UPDATE processing_runtime_limits SET provider_global_limit=8")
    finally:
        for request in ids:
            _release(c.db, c.actor, request)
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.processing_runtime_limits SET provider_global_limit=%s,provider_default_limit=%s WHERE singleton=1",
                original,
            )
            conn.execute(
                "DELETE FROM accounting.provider_runtime_limits WHERE provider=%s", (supplier,)
            )


def test_real_pg_scoped_ocr_and_legacy_request_cannot_spend_last_company_budget_twice(pg_cost):
    c = pg_cost
    first, other = create_event(c), create_event(c, actor="peer")
    c.queue.enqueue(c.actors["employee"], batch_request(c.company["id"], [first]))
    entered, release = Event(), Event()

    def hold_model():
        entered.set()
        assert release.wait(5)

    c.provider.extract_hook = hold_model
    with ThreadPoolExecutor(2) as pool:
        processing = pool.submit(c.worker.run_once)
        try:
            assert entered.wait(5)
            with pytest.raises(BudgetError) as exhausted:
                extract(c, other, "peer", "mixed-domain-last-budget")
            assert exhausted.value.code == "quota_exhausted"
        finally:
            release.set()
        assert processing.result(5)
    assert c.provider.extract_calls == 1
    usage = c.operations.overview(c.manager, c.company["id"])["usage"]
    assert usage["estimated_cost"] == "0.001400" and usage["reserved_cost"] == "0.000000"


def test_real_pg_system_company_scope_refreshes_projection_without_global_lock(case):
    """投影刷新等系统任务走企业锁：同企业行可写、跨企业行被写门拒绝。"""
    c = case
    projection_url = os.getenv("ACCOUNTING_PROJECTION_DATABASE_URL", "")
    if not projection_url:
        pytest.skip("需要独立 accounting_projection DSN")
    projection = Database(
        projection_url,
        default_role="accounting_projection",
        validation_role="accounting_projection",
    )
    stamp = "2026-01-01T00:00:00+00:00"
    with projection.transaction(
        write=True,
        write_scope="company",
        company_id=c.company,
        firm_id=c.actor.firm_id,
    ) as conn:
        conn.execute(
            "INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at,built_at,failures) VALUES(?,?,1,1,?,?,0) ON CONFLICT(firm_id,company_id) DO NOTHING",
            (c.actor.firm_id, c.company, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO workspace_task_projections(firm_id,company_id,kind,ref,state,title,updated_at) VALUES(?,?,'alert','scope-probe','open','探针',?)",
            (c.actor.firm_id, c.company, stamp),
        )
        conn.execute(
            "DELETE FROM workspace_task_projections WHERE firm_id=? AND company_id=? AND ref='scope-probe'",
            (c.actor.firm_id, c.company),
        )
    other = c.service.create_book(c.actor, BookCreate(name="系统企业锁第二客户", period="2026-10"))
    with pytest.raises(Exception):
        with projection.transaction(
            write=True,
            write_scope="company",
            company_id=c.company,
            firm_id=c.actor.firm_id,
        ) as conn:
            conn.execute(
                "INSERT INTO workspace_task_projections(firm_id,company_id,kind,ref,state,title,updated_at) VALUES(?,?,'alert','scope-other','open','越界',?)",
                (c.actor.firm_id, other["company_id"], stamp),
            )
    with projection.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM workspace_task_projections WHERE firm_id=? AND ref='scope-other'",
            (c.actor.firm_id,),
        )
        conn.execute(
            "DELETE FROM workspace_projection_state WHERE firm_id=? AND company_id=?",
            (c.actor.firm_id, c.company),
        )
    projection.close()
