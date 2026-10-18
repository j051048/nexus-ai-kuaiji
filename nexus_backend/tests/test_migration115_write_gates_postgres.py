"""Real PostgreSQL regressions for historical write-gate repair migration 115.

Uses the existing dedicated test-DSN/multi-company fixtures. No model, network
storage, production account, temporary replacement trigger, or admin business
write is used to exercise the repaired guards.
"""

from uuid import uuid4

import pytest

from test_business_postgres import (
    ADMIN_URL,
    APP_URL,
    STAMP,
    attach,
    business_case,  # noqa: F401
    business_postgres,  # noqa: F401
    insert_event,
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


def insert_ai_run(conn, case, event_id, attachment_id):
    run_id = "gate115-run-" + uuid4().hex
    conn.execute(
        "INSERT INTO ai_runs("
        "id,firm_id,company_id,event_id,kind,attachment_id,input_version,"
        "input_fingerprint,idempotency_key,status,provider,model,prompt_version,"
        "created_by,created_at) "
        "VALUES(?,?,?,?,'ocr',?,1,?,?,'running','compatible','synthetic',"
        "'gate115',?,?)",
        (
            run_id,
            case.employee.firm_id,
            case.company,
            event_id,
            attachment_id,
            uuid4().hex,
            uuid4().hex,
            case.employee.user_id,
            STAMP,
        ),
    )
    return run_id


def test_pg115_company_ai_write_commits_without_entering_legacy_lock(business_case):
    case = business_case
    event_id = insert_event(case)
    attachment_id = attach(case, event_id)

    with case.db.transaction(
        write=True,
        actor=case.employee,
        company_id=case.company,
        write_scope="company",
    ) as conn:
        assert conn.execute("SELECT current_user").fetchone()[0] == "accounting_app"
        run_id = insert_ai_run(conn, case, event_id, attachment_id)
        conn.execute(
            "UPDATE ai_runs SET status='failed',error_code='synthetic_test',"
            "error_detail='本地合成写门测试，无模型请求',finished_at=? WHERE id=?",
            (STAMP, run_id),
        )
        conn.execute(
            "INSERT INTO ops_exception_snapshots("
            "event_id,firm_id,company_id,blocking_count,warning_count,"
            "context_fingerprint,updated_at) VALUES(?,?,?,1,0,?,?)",
            (event_id, case.employee.firm_id, case.company, uuid4().hex, STAMP),
        )

    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute(
            "SELECT status,error_code FROM ai_runs WHERE id=?", (run_id,)
        ).fetchone()[:] == ("failed", "synthetic_test")
    # Operations snapshots are finance/manager-only, even when their own event
    # processor may write them. Do not weaken read scope for this regression.
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute(
            "SELECT blocking_count FROM ops_exception_snapshots WHERE event_id=?",
            (event_id,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("outside_scope", ["company", "firm"])
def test_pg115_ai_guard_rejects_foreign_scope_before_constraints(
    business_case, outside_scope
):
    case = business_case
    event_id = insert_event(case)
    firm_id = case.employee.firm_id
    company_id = case.company
    if outside_scope == "company":
        company_id = case.other_company
    else:
        firm_id = "gate115-foreign-firm-" + uuid4().hex

    with pytest.raises(
        case.psycopg.errors.InsufficientPrivilege,
        match="Write exceeds declared company scope",
    ):
        with case.db.transaction(
            write=True,
            actor=case.employee,
            company_id=case.company,
            write_scope="company",
        ) as conn:
            conn.execute(
                "INSERT INTO ops_exception_snapshots("
                "event_id,firm_id,company_id,blocking_count,warning_count,"
                "context_fingerprint,updated_at) VALUES(?,?,?,1,0,?,?)",
                (event_id, firm_id, company_id, uuid4().hex, STAMP),
            )
            raise AssertionError("Cross-scope AI write unexpectedly succeeded")

    with case.db.transaction(actor=case.employee) as conn:
        assert not conn.execute(
            "SELECT event_id FROM ops_exception_snapshots WHERE event_id=?", (event_id,)
        ).fetchall()


@pytest.mark.skipif(not WORKER_URL, reason="需要独立 PostgreSQL worker 测试 DSN")
def test_pg115_dispatcher_keeps_allowed_ocr_claim_domain(queue_case):
    # Go through the worker's real security-definer capability. An app role
    # cannot acquire the dispatcher lock merely by setting its marker.
    case = queue_case
    queued = enqueue(case)
    claimed = claim(case, name="gate115-worker-" + uuid4().hex)
    assert claimed is not None and claimed["id"] == queued["id"]
    assert claimed["company_id"] == case.company
    assert finish_job(
        case,
        claimed,
        status="failed",
        code="synthetic_test",
        detail="本地合成调度写门测试，无模型请求",
    )


@pytest.mark.parametrize(
    "table", ["batch_operations", "batch_operation_items", "onboarding_runs"]
)
def test_pg115_dispatcher_cannot_enter_new_batch_or_onboarding_tables(
    business_case, table
):
    case = business_case
    object_id = "gate115-dispatch-denied-" + uuid4().hex
    parent_id = "gate115-parent-" + uuid4().hex
    if table == "batch_operation_items":
        # A valid inert parent prevents an unrelated foreign-key error from
        # masking a missing row gate on the third repaired table.
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute(
                "INSERT INTO batch_operations("
                "id,firm_id,kind,status,idempotency_key,request_json,total,"
                "created_by,created_at,updated_at) "
                "VALUES(?,?,'monthly_open','completed',?,'{}',0,?,?,?)",
                (
                    parent_id,
                    case.owner.firm_id,
                    uuid4().hex,
                    case.owner.user_id,
                    STAMP,
                    STAMP,
                ),
            )
    with pytest.raises(
        case.psycopg.errors.InsufficientPrivilege,
        match="Write exceeds dispatcher domain",
    ):
        # The firm owner normally has permission for these actual tables. The
        # dispatcher marker must narrow that domain rather than inherit it.
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("SELECT set_config('app.write_dispatch','1',true)")
            if table == "batch_operations":
                conn.execute(
                    "INSERT INTO batch_operations("
                    "id,firm_id,kind,status,idempotency_key,request_json,total,"
                    "created_by,created_at,updated_at) "
                    "VALUES(?,?,'monthly_open','queued',?,'{}',0,?,?,?)",
                    (
                        object_id,
                        case.owner.firm_id,
                        uuid4().hex,
                        case.owner.user_id,
                        STAMP,
                        STAMP,
                    ),
                )
            elif table == "batch_operation_items":
                conn.execute(
                    "INSERT INTO batch_operation_items("
                    "id,batch_id,firm_id,company_id,sequence_no,status,"
                    "available_at,created_at,updated_at) "
                    "VALUES(?,?,?,?,1,'queued',?,?,?)",
                    (
                        object_id,
                        parent_id,
                        case.owner.firm_id,
                        case.company,
                        STAMP,
                        STAMP,
                        STAMP,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO onboarding_runs("
                    "id,firm_id,company_id,period,status,steps_json,"
                    "created_by,created_at,updated_at) "
                    "VALUES(?,?,?,'2026-10','draft','{}',?,?,?)",
                    (
                        object_id,
                        case.owner.firm_id,
                        case.company,
                        case.owner.user_id,
                        STAMP,
                        STAMP,
                    ),
                )
            raise AssertionError(f"Dispatcher unexpectedly wrote {table}")

    with case.db.transaction(actor=case.owner) as conn:
        # table is a closed pytest parameter, never caller-controlled SQL.
        assert not conn.execute(
            f"SELECT id FROM {table} WHERE id=?", (object_id,)
        ).fetchall()
