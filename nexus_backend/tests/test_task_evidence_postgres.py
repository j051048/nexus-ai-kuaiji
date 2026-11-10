"""Current rule memory and bounded reads using actual PG16/17 restricted roles."""

import pytest
from app.accounting.domain import AccountingError
from app.learning import memory as projection
from test_rsi_rules_postgres import candidate, business_case, business_postgres  # noqa: F401
from test_task_rule_memory import memory
from test_task_parallel_reads import (
    test_two_readers_overlap_without_sharing_connection as overlap,
    test_parallel_final_change_discards_every_branch as changed,
    test_parallel_late_results_hold_slots_and_never_publish as late,
)
from app.assistant import investigation


def test_pg_rule_memory_current_source_scope_and_no_publication(candidate):
    c, lab, artifact, learning, *_ = candidate
    result = memory(c, learning, artifact)
    assert result["state"] == "candidate" and result["auto_apply"] is False
    assert result["sources"] and result["scope"]["company_id"] == c.company["id"]
    assert lab.shadow(c.manager, c.book["id"], artifact, "120.00")["lines"][0][1] == 12000
    for actor, book in [
        (c.actors["employee"], c.book),
        (c.native.unassigned, c.book),
        (c.manager, c.other_book),
    ]:
        with pytest.raises(AccountingError):
            learning.rule_memory(
                actor,
                book["id"],
                result["rule_id"],
                expected_version=result["version"],
                expected_state_version=result["state_version"],
            )
    assert learning.get_rule(c.manager, c.book["id"], result["rule_id"])["status"] == "draft"


def test_pg_rule_memory_rechecks_committed_revocation(candidate, monkeypatch):
    c, _, artifact, learning, *_ = candidate
    original = projection.snapshot
    count = 0

    def revoke_after_read(*args):
        nonlocal count
        value = original(*args)
        count += 1
        if count == 1:
            c.directory.revoke_book_member(c.manager, c.book["id"], c.actors["accountant"].user_id)
        return value

    monkeypatch.setattr(projection, "snapshot", revoke_after_read)
    rule = learning.get_rule(c.manager, c.book["id"], artifact["source_rule_id"])
    with pytest.raises(AccountingError):
        learning.rule_memory(
            c.actors["accountant"],
            c.book["id"],
            rule["id"],
            expected_version=rule["current_version"],
            expected_state_version=rule["state_version"],
        )


def test_pg_parallel_connections_versions_and_late_results(candidate, monkeypatch):
    c = candidate[0]
    overlap(c, monkeypatch)
    with monkeypatch.context() as m:
        changed(c, m)
    with monkeypatch.context() as m:
        late(c, m)


def test_pg_parallel_revocation_and_no_fee_or_attempt(candidate, monkeypatch):
    c = candidate[0]
    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "2")
    actor = c.actors["accountant"]

    def revoke(name):
        if name == "close_blockers":
            c.directory.revoke_book_member(c.manager, c.book["id"], actor.user_id)

    with c.db.transaction(actor=c.manager) as conn:
        before = [
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE firm_id=?", (c.manager.firm_id,)
            ).fetchone()[0]
            for table in ("ai_runs", "model_costs")
        ]
    result = investigation.investigate(c.service, actor, c.book["id"], "2026-10", on_step=revoke)
    assert result["status"] == "blocked" and result["findings"] == {} and result["evidence"] is None
    assert result["model_calls"] == 0 and result["execution"]["cost_cny"] is None
    with c.db.transaction(actor=c.manager) as conn:
        after = [
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE firm_id=?", (c.manager.firm_id,)
            ).fetchone()[0]
            for table in ("ai_runs", "model_costs")
        ]
    assert after == before


def test_pg_parallel_pool_wait_obeys_budget_and_resets_identity(candidate, monkeypatch):
    import time

    c = candidate[0]
    monkeypatch.setenv(investigation.ENV_FLAG, "1")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "2")
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_READ_SECONDS", "0.15")
    with c.db.transaction(actor=c.manager) as conn:
        original_timeout = conn.execute("SHOW statement_timeout").fetchone()[0]
    pool = c.db._postgres_pool()
    held = [c.db.connect() for _ in range(pool.max_size)]
    started = time.monotonic()
    try:
        result = investigation.investigate(c.service, c.manager, c.book["id"], "2026-10")
        assert result["execution"]["stop_reason"] == "timeout"
        assert result["findings"] == {} and result["execution"]["attempted_reads"] == 0
        assert time.monotonic() - started < 2
    finally:
        for conn in held:
            conn.close()
    with c.db.transaction(actor=c.manager) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == original_timeout
        assert conn.execute("SELECT accounting.actor_user()").fetchone()[0] == c.manager.user_id
