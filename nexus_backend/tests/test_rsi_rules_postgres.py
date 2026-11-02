"""Mandatory PG16/17 RSI03 tests; missing local configuration is a failure."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
import json
import os

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.business.service import BusinessService
from app.ledgers.models import EntityUpdate
from test_business_postgres import business_case, business_postgres, ADMIN_URL, APP_URL
from test_rsi_rules import setup_rules
from tools.ai_inference_lab.rsi.rules import LocalRules
from tools.ai_inference_lab.rsi.snapshots import SnapshotError


@pytest.fixture
def candidate(business_case):
    assert ADMIN_URL and APP_URL, "RSI PG gate requires explicit isolated DSNs; no skip"
    c = business_case
    with c.psycopg.connect(ADMIN_URL) as conn:
        version = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        assert version in (16, 17)
    with c.db.transaction(actor=c.owner) as conn:
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert role["rolsuper"] is False and role["rolbypassrls"] is False
    adapted = SimpleNamespace(
        db=c.db,
        ledger=c.service,
        service=BusinessService(c.db),
        manager=c.owner,
        company={"id": c.company},
        book={"id": c.book},
        other_book={"id": c.other_book},
        directory=DirectoryService(c.db),
        actors={
            "employee": c.employee,
            "approver": c.approver,
            "finance": c.finance,
            "accountant": c.reviewer,
        },
        native=c,
    )
    return adapted, *setup_rules(adapted)


def test_restricted_role_stage_evaluate_current_amount_and_no_publish(candidate):
    c, lab, artifact, learning, *_ = candidate
    rule = lab.stage(c.actors["accountant"], c.book["id"], artifact)
    assert rule["status"] == "draft"
    assert lab.evaluate(c.actors["accountant"], c.book["id"], artifact, rule["id"])["passed"]
    assert learning.get_rule(c.manager, c.book["id"], rule["id"])["status"] == "draft"
    assert lab.shadow(c.manager, c.book["id"], artifact, "120.00")["lines"][0][1] == 12000


def test_low_privilege_foreign_scope_and_worker_denied(candidate):
    c, lab, artifact, *_ = candidate
    for actor in (c.actors["employee"], c.actors["finance"], c.native.unassigned):
        with pytest.raises(AccountingError):
            lab.stage(actor, c.book["id"], artifact)
    with pytest.raises(SnapshotError):
        lab.stage(c.manager, c.other_book["id"], artifact)
    with c.db.transaction(actor=c.native.unassigned) as conn:
        assert not conn.execute(
            "SELECT id FROM learning_rules WHERE book_id=?", (c.book["id"],)
        ).fetchall()
    with c.native.psycopg.connect(os.environ["ACCOUNTING_TEST_WORKER_DATABASE_URL"]) as conn:
        conn.execute("SET ROLE accounting_worker")
        with pytest.raises(c.native.psycopg.Error):
            conn.execute("SELECT * FROM accounting.learning_samples")


def test_two_real_sessions_duplicate_stage_and_lost_response(candidate, monkeypatch):
    c, lab, artifact, learning, *_ = candidate
    barrier, entered, release = Barrier(2), Event(), Event()
    pids = set()
    prepare = lab._prepare

    def observed(conn, *args, **kwargs):
        pids.add(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
        return prepare(conn, *args, **kwargs)

    monkeypatch.setattr(lab, "_prepare", observed)

    def stage():
        barrier.wait(timeout=10)
        return lab.stage(c.manager, c.book["id"], artifact)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [pool.submit(stage) for _ in range(2)]
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert len(pids) == 2
    assert results[0]["id"] == results[1]["id"]
    assert lab.stage(c.manager, c.book["id"], artifact)["id"] == results[0]["id"]
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM learning_rule_versions WHERE rule_id=?", (results[0]["id"],)
            ).fetchone()[0]
            == 1
        )


def test_revoke_commits_before_waiting_stage_then_fails(candidate):
    c, lab, artifact, *_ = candidate
    started = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                (c.book["id"], c.actors["accountant"].user_id),
            )

            def pending():
                started.set()
                return lab.stage(c.actors["accountant"], c.book["id"], artifact)

            job = pool.submit(pending)
            assert started.wait(5)
            assert not job.done()
        with pytest.raises(AccountingError):
            job.result(timeout=30)


def test_catalog_change_commits_before_waiting_stage_then_stale(candidate):
    c, lab, artifact, _, master, entity = candidate
    started = Event()
    # Hold the normal database write gate in session 1, queue session 2.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lab._session(c.manager, True) as (conn, service):
            conn.execute(
                "UPDATE accounts SET name=?,version=version+1 WHERE book_id=? AND code='6602.99'",
                ("新版本费用科目", c.book["id"]),
            )

            def pending():
                started.set()
                return lab.stage(c.manager, c.book["id"], artifact)

            job = pool.submit(pending)
            assert started.wait(5)
            assert not job.done()
        with pytest.raises(SnapshotError, match="stale"):
            job.result(timeout=30)
