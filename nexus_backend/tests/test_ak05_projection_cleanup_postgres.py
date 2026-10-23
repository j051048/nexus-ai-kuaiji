"""Regression checks for test-owned cleanup without projection privilege changes."""

import os
from uuid import uuid4

import pytest

import test_batch2_concurrency_postgres as target
from app.accounting.database import Database
from test_ak05_unified_postgres import require_isolated_target
from test_postgres_integration import ADMIN_URL, APP_URL, case, postgres


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="requires explicit disposable AK05 PostgreSQL target",
)


class InjectedCleanupFailure(RuntimeError):
    pass


@pytest.mark.parametrize("preexisting_state", [False, True])
@pytest.mark.parametrize("interrupt", [False, True])
def test_projection_probe_cleans_only_owned_rows_and_always_closes(
    case, monkeypatch, preexisting_state, interrupt
):
    c = case
    opened = []
    sentinel = "unrelated-cleanup-" + uuid4().hex

    def tracked_database(*args, **kwargs):
        db = Database(*args, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(target, "Database", tracked_database)
    if interrupt:

        def interrupted(*args, **kwargs):
            raise InjectedCleanupFailure("assertion path after projection write")

        monkeypatch.setattr(c.service, "create_book", interrupted)

    with c.psycopg.connect(ADMIN_URL) as conn:
        if not preexisting_state:
            conn.execute(
                "DELETE FROM accounting.workspace_projection_state WHERE firm_id=%s AND company_id=%s",
                (c.actor.firm_id, c.company),
            )
        before = conn.execute(
            "SELECT * FROM accounting.workspace_projection_state WHERE firm_id=%s AND company_id=%s",
            (c.actor.firm_id, c.company),
        ).fetchall()
        assert bool(before) == preexisting_state
        conn.execute(
            "INSERT INTO accounting.workspace_task_projections(firm_id,company_id,kind,ref,state,title,updated_at) VALUES(%s,%s,'alert',%s,'open','unrelated row','2026-10-23T00:00:00+00:00')",
            (c.actor.firm_id, c.company, sentinel),
        )
    try:
        if interrupt:
            with pytest.raises(InjectedCleanupFailure):
                target.test_real_pg_system_company_scope_refreshes_projection_without_global_lock(c)
        else:
            target.test_real_pg_system_company_scope_refreshes_projection_without_global_lock(c)
        assert len(opened) == 1 and opened[0]._closed
        with c.psycopg.connect(ADMIN_URL) as conn:
            after = conn.execute(
                "SELECT * FROM accounting.workspace_projection_state WHERE firm_id=%s AND company_id=%s",
                (c.actor.firm_id, c.company),
            ).fetchall()
            assert after == before
            assert conn.execute(
                "SELECT ref FROM accounting.workspace_task_projections WHERE firm_id=%s AND company_id=%s",
                (c.actor.firm_id, c.company),
            ).fetchall() == [(sentinel,)]
    finally:
        for db in opened:
            db.close()
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "DELETE FROM accounting.workspace_task_projections WHERE firm_id=%s AND company_id=%s AND ref=%s",
                (c.actor.firm_id, c.company, sentinel),
            )


def test_projection_still_cannot_delete_state_metadata(case):
    c = case
    projection = Database(
        os.environ["ACCOUNTING_PROJECTION_DATABASE_URL"],
        default_role="accounting_projection",
        validation_role="accounting_projection",
    )
    try:
        with c.psycopg.connect(ADMIN_URL) as conn:
            assert not conn.execute(
                "SELECT has_table_privilege('accounting_projection','accounting.workspace_projection_state','DELETE')"
            ).fetchone()[0]
            before = conn.execute(
                "SELECT * FROM accounting.workspace_projection_state WHERE firm_id=%s AND company_id=%s",
                (c.actor.firm_id, c.company),
            ).fetchall()
            assert before
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege) as denied:
            with projection.transaction(
                write=True,
                write_scope="company",
                firm_id=c.actor.firm_id,
                company_id=c.company,
            ) as conn:
                conn.execute(
                    "DELETE FROM workspace_projection_state WHERE firm_id=? AND company_id=?",
                    (c.actor.firm_id, c.company),
                )
        assert denied.value.sqlstate == "42501"
        with c.psycopg.connect(ADMIN_URL) as conn:
            assert (
                conn.execute(
                    "SELECT * FROM accounting.workspace_projection_state WHERE firm_id=%s AND company_id=%s",
                    (c.actor.firm_id, c.company),
                ).fetchall()
                == before
            )
    finally:
        projection.close()
