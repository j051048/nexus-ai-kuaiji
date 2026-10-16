"""Real restricted PostgreSQL snapshots, grants and fleet policy visibility."""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.operations.efficiency import EfficiencyService
from app.operations.efficiency_models import SessionStart
from app.operations.service import OperationsService
from app.operations.telemetry import TelemetryService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
from test_intelligence_workflow_postgres import pg_ai  # noqa: F401
from test_stage20_assistant_reads import service_and_thread, statements
from test_stage25_assistant_efficiency import (
    test_snapshot_reuse_preserves_all_evidence_and_facts_with_fewer_roundtrips as snapshot_checks,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要独立受限PG运行账号")


def test_pg_snapshot_reuse_has_identical_native_fingerprint_and_evidence(pg_ai):
    snapshot_checks(pg_ai)


def test_pg_progress_is_readonly_repeatable_and_revocation_is_fresh(pg_ai):
    c = pg_ai
    actor = c.actors["accountant"]
    assistant, thread = service_and_thread(c, books=[c.book["id"]])
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute("SHOW transaction_isolation").fetchone()[0] == "repeatable read"
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
    with statements(c.db) as sql:
        progress = assistant.progress(actor, thread["id"])
    assert len(sql) <= 7 and progress["active"] is False
    assert assistant.progress(actor, thread["id"])["revision"] == progress["revision"]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], actor.user_id),
        )
    with pytest.raises(AccountingError) as denied:
        assistant.progress(actor, thread["id"])
    assert denied.value.status == 404


def test_pg_portfolio_counts_only_current_review_books_and_samples(pg_ai):
    c = pg_ai
    efficiency, actor = EfficiencyService(c.db), c.actors["accountant"]
    value = efficiency.portfolio(actor, "2026-10")
    assert value["pagination"]["total"] == 1
    assert value["items"][0]["credited_seconds"] is None
    efficiency.start(actor, SessionStart(book_id=c.book["id"], idempotency_key=uuid4().hex))
    value = efficiency.portfolio(actor, "2026-10")
    assert value["items"][0]["credited_seconds"] == 0
    assert value["items"][0]["session_count"] == 1
    assert c.other_company["id"] not in str(value)
    assert efficiency.portfolio(c.actors["employee"], "2026-10")["items"] == []
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], actor.user_id),
        )
    assert efficiency.portfolio(actor, "2026-10")["items"] == []


def test_pg_telemetry_reads_real_database_policy_without_table_grant(pg_ai):
    c = pg_ai
    telemetry = TelemetryService(c.db, OperationsService(c.db, None))
    with c.psycopg.connect(ADMIN_URL) as conn:
        prior = conn.execute(
            "SELECT processing_global_limit,provider_global_limit,provider_default_limit FROM accounting.processing_runtime_limits WHERE singleton=1"
        ).fetchone()
        conn.execute(
            "UPDATE accounting.processing_runtime_limits SET processing_global_limit=4,provider_global_limit=3,provider_default_limit=2 WHERE singleton=1"
        )
    try:
        limits = telemetry.overview(c.manager, c.company["id"])["limits"]
        assert limits["global_model_concurrency"] == 4
        assert limits["provider_global_limit"] == 3
        assert limits["company_model_concurrency"] == 1
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            with c.db.transaction(actor=c.manager) as conn:
                conn.execute("SELECT * FROM processing_runtime_limits")
        with c.db.transaction(actor=c.actors["employee"]) as conn:
            with pytest.raises(c.psycopg.Error):
                conn.execute("SELECT accounting.processing_capacity(?)", (c.company["id"],))
    finally:
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.processing_runtime_limits SET processing_global_limit=%s,provider_global_limit=%s,provider_default_limit=%s WHERE singleton=1",
                prior,
            )
