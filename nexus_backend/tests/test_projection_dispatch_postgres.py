"""Real source-trigger/permission regressions for migration 117."""

from uuid import uuid4

import pytest

from test_business_postgres import (
    ADMIN_URL,
    APP_URL,
    business_case,  # noqa: F401
    business_postgres,  # noqa: F401
)
from test_queue_postgres import (
    WORKER_URL,
    claim,
    enqueue,
    finish_job,
    queue_case,  # noqa: F401
)

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL,
    reason="需要独立 PostgreSQL 管理员和受限 API 测试 DSN",
)


def revision(case, company):
    with case.db.transaction(actor=case.owner) as conn:
        return conn.execute(
            "SELECT revision FROM workspace_projection_state "
            "WHERE firm_id=? AND company_id=?",
            (case.owner.firm_id, company),
        ).fetchone()[0]


@pytest.mark.skipif(not WORKER_URL, reason="需要独立 PostgreSQL worker 测试 DSN")
def test_pg117_real_claim_and_finish_dirty_only_the_source_company(queue_case):
    case = queue_case
    queued = enqueue(case)
    before = revision(case, case.company)
    other_before = revision(case, case.other_company)

    claimed = claim(case, name="projection117-" + uuid4().hex)
    assert claimed is not None and claimed["id"] == queued["id"]
    after_claim = revision(case, case.company)
    assert after_claim > before
    assert revision(case, case.other_company) == other_before
    assert finish_job(
        case,
        claimed,
        status="failed",
        code="synthetic_test",
        detail="本地投影调度回归测试，无模型请求",
    )
    assert revision(case, case.company) > after_claim
    assert revision(case, case.other_company) == other_before


@pytest.mark.parametrize("table", ["workspace_projection_state", "workspace_task_projections"])
def test_pg117_app_cannot_directly_write_projection_metadata_or_content(
    business_case, table
):
    case = business_case
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            # Only a closed parameter controls this table name.
            conn.execute(f"DELETE FROM {table} WHERE firm_id=?", (case.owner.firm_id,))

    with case.db.transaction(actor=case.owner) as conn:
        for role in ("accounting_app", "accounting_worker"):
            assert conn.execute(
                "SELECT has_table_privilege(?,?,'INSERT,UPDATE,DELETE'),"
                "has_any_column_privilege(?,?,'INSERT,UPDATE')",
                (role, "accounting." + table, role, "accounting." + table),
            ).fetchone()[:] == (False, False)
            assert not conn.execute(
                "SELECT has_function_privilege(?,"
                "'accounting.workspace_projection_bump()','EXECUTE')",
                (role,),
            ).fetchone()[0]


def test_pg117_forged_source_with_real_table_name_is_rejected(business_case):
    case = business_case
    before = revision(case, case.company)
    with case.psycopg.connect(ADMIN_URL) as conn:
        # Test-only DDL rolls back. A pg_temp table with a trusted-looking name
        # must not invoke the definer to manufacture another company's state.
        with pytest.raises(
            case.psycopg.errors.InsufficientPrivilege,
            match="Untrusted workspace projection trigger source",
        ):
            with conn.transaction(force_rollback=True):
                conn.execute("CREATE TEMP TABLE ocr_jobs(firm_id TEXT,company_id TEXT)")
                conn.execute(
                    "CREATE TRIGGER workspace_projection_bump AFTER INSERT ON ocr_jobs "
                    "FOR EACH ROW EXECUTE FUNCTION accounting.workspace_projection_bump()"
                )
                conn.execute(
                    "INSERT INTO ocr_jobs VALUES(%s,%s)",
                    (case.owner.firm_id, case.company),
                )
                raise AssertionError("Forged projection source unexpectedly succeeded")
    assert revision(case, case.company) == before


def test_pg117_real_source_with_unexpected_trigger_arguments_is_rejected(business_case):
    case = business_case
    before = revision(case, case.company)
    with case.psycopg.connect(ADMIN_URL) as conn:
        with pytest.raises(
            case.psycopg.errors.InsufficientPrivilege,
            match="Untrusted workspace projection trigger source",
        ):
            with conn.transaction(force_rollback=True):
                # Exercise a real registered relation and correct trigger name;
                # the extra argument alone must cause rejection. Restore on rollback.
                conn.execute(
                    "DROP TRIGGER workspace_projection_bump ON accounting.firm_clients"
                )
                conn.execute(
                    "CREATE TRIGGER workspace_projection_bump AFTER UPDATE ON accounting.firm_clients "
                    "FOR EACH ROW EXECUTE FUNCTION accounting.workspace_projection_bump('forged')"
                )
                conn.execute(
                    "UPDATE accounting.firm_clients SET status=status WHERE firm_id=%s AND company_id=%s",
                    (case.owner.firm_id, case.company),
                )
                raise AssertionError("Projection trigger arguments unexpectedly accepted")
    assert revision(case, case.company) == before
