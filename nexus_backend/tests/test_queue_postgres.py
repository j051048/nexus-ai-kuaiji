"""Durable dispatch and privilege boundaries on dedicated real PostgreSQL."""
import os
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.service import Actor
from test_business_postgres import (
    ADMIN_URL, APP_URL, attach, business_case, business_postgres, insert_event,
)
from test_intelligence_postgres import finish, ocr_run

WORKER_URL = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PostgreSQL 管理员、API 和 worker 测试连接")


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def worker(case):
    from psycopg.rows import dict_row

    with case.psycopg.connect(WORKER_URL, row_factory=dict_row) as conn:
        conn.execute("SET LOCAL ROLE accounting_worker")
        yield conn


@pytest.fixture
def queue_case(business_case):
    case = business_case
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.ai_company_settings VALUES(%s,%s,1,%s,%s) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (case.owner.firm_id, case.company, case.owner.user_id, now()))
        conn.execute("INSERT INTO accounting.ai_company_settings VALUES(%s,%s,1,%s,%s) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (case.owner.firm_id, case.other_company, case.owner.user_id, now()))
    yield case
    # Preserve immutable test history, but leave no dispatchable test tasks.
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ocr_attempts SET phase='finished',status='cancelled',finished_at=%s WHERE firm_id=%s AND status='running'", (now(), case.owner.firm_id))
        conn.execute("UPDATE accounting.ocr_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,finished_at=%s WHERE firm_id=%s AND status IN ('queued','running','retry_wait')", (now(), case.owner.firm_id))


def enqueue(case, *, actor=None, event_id=None, attachment_id=None, batch_id=None, **changes):
    actor = actor or case.employee
    event_id = event_id or insert_event(case, applicant=actor)
    attachment_id = attachment_id or attach(case, event_id, applicant=actor)
    batch_id = batch_id or uuid4().hex
    stamp = now()
    job = {
        "id": uuid4().hex, "batch_id": batch_id, "firm_id": actor.firm_id,
        "company_id": case.company, "event_id": event_id, "attachment_id": attachment_id,
        "expected_version": 1, "attachment_sha256": "a" * 64, "configuration_fingerprint": "configured-vision",
        "execution_user_id": actor.user_id, "created_by": actor.user_id, "created_at": stamp,
        # Windows and the WSL PostgreSQL clock can differ by tens of ms; this
        # SQL unit case is immediately eligible, without relying on wall-clock
        # synchronization or sleeps. Delay cases use database-generated times.
        "available_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "updated_at": stamp, **changes,
    }
    with case.db.transaction(write=True, actor=actor) as conn:
        if not conn.execute("SELECT id FROM ocr_batches WHERE id=?", (batch_id,)).fetchone():
            conn.execute("INSERT INTO ocr_batches VALUES(?,?,?,?,?,?,?)", (batch_id, actor.firm_id, case.company, actor.user_id, uuid4().hex, uuid4().hex, stamp))
        conn.execute(f"INSERT INTO ocr_jobs({','.join(job)}) VALUES({','.join('?' for _ in job)})", tuple(job.values()))
    return job


def claim(case, name="worker-test", global_limit=2):
    with worker(case) as conn:
        return conn.execute("SELECT * FROM accounting.claim_ocr_job(%s,180,%s)", (name, global_limit)).fetchone()


def expire(case, job_id):
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ocr_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s", (job_id,))


def recover(case):
    with worker(case) as conn:
        return conn.execute("SELECT accounting.recover_ocr_jobs() AS n").fetchone()["n"]


def finish_job(case, job, status="failed", **changes):
    with worker(case) as conn:
        return conn.execute("SELECT accounting.worker_finish_ocr_job(%s,%s,%s,%s,%s,%s,%s) AS ok", (
            job["id"], changes.get("token", job["lease_token"]), status,
            changes.get("code", "test_failure"), changes.get("detail", "安全错误说明"),
            changes.get("run_id"), changes.get("delay", 0),
        )).fetchone()["ok"]


def bind(case, job, run_id):
    with worker(case) as conn:
        return conn.execute("SELECT accounting.worker_bind_ocr_run(%s,%s,%s) AS ok", (job["id"], job["lease_token"], run_id)).fetchone()["ok"]


def sending(case, job):
    with worker(case) as conn:
        return conn.execute("SELECT accounting.worker_mark_ocr_sending(%s,%s) AS ok", (job["id"], job["lease_token"])).fetchone()["ok"]


def get_job(case, job_id):
    with case.db.transaction(actor=case.employee) as conn:
        return dict(conn.execute("SELECT * FROM ocr_jobs WHERE id=?", (job_id,)).fetchone())


def test_queue_records_follow_person_department_company_scope(queue_case):
    case = queue_case
    job = enqueue(case)
    claimed = claim(case)
    assert claimed["id"] == job["id"]
    for actor in (case.employee, case.approver, case.finance):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT count(*) FROM ocr_jobs WHERE id=?", (job["id"],)).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM ocr_batches WHERE id=?", (job["batch_id"],)).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM ocr_attempts WHERE job_id=?", (job["id"],)).fetchone()[0] == 1
    for actor in (case.colleague, case.owner, case.company_owner, case.viewer, Actor(case.employee.user_id, "other-firm")):
        with case.db.transaction(actor=actor) as conn:
            for table, column in (("ocr_jobs", "id"), ("ocr_batches", "id"), ("ocr_attempts", "job_id")):
                identity = job["batch_id"] if table == "ocr_batches" else job["id"]
                assert conn.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", (identity,)).fetchone()[0] == 0


def test_api_has_no_cross_company_dispatch_privilege(queue_case):
    case = queue_case
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.employee) as conn:
            conn.execute("SELECT * FROM accounting.claim_ocr_job('api',180,2)")
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.psycopg.connect(APP_URL) as conn:
            conn.execute("SET LOCAL ROLE accounting_worker")


@pytest.mark.parametrize("table", ["ocr_jobs", "ocr_batches", "ocr_attempts", "ocr_retries", "ocr_company_schedule", "business_attachments", "ai_runs"])
def test_worker_role_has_no_direct_table_read(queue_case, table):
    with pytest.raises(queue_case.psycopg.errors.InsufficientPrivilege):
        with worker(queue_case) as conn:
            conn.execute(f"SELECT * FROM accounting.{table}")


def test_queue_scope_foreign_keys_and_duplicate_active_attachment(queue_case):
    case = queue_case
    job = enqueue(case)
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        enqueue(case, event_id=job["event_id"], attachment_id=job["attachment_id"])
    other_event = insert_event(case)
    with pytest.raises(case.psycopg.errors.ForeignKeyViolation):
        enqueue(case, event_id=other_event, attachment_id=job["attachment_id"])


def test_api_cannot_forge_completion_attempt_or_worker_identity(queue_case):
    case = queue_case
    job = enqueue(case)
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("UPDATE ocr_jobs SET status='failed',finished_at=? WHERE id=?", (now(), job["id"])).rowcount == 0
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        enqueue(case, attempt_count=1, max_attempts=4)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        enqueue(case, execution_user_id=case.finance.user_id)


def test_company_policy_and_revocation_block_enqueue_and_reads(queue_case):
    case = queue_case
    job = enqueue(case)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ai_company_settings SET enabled=0 WHERE company_id=%s", (case.company,))
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        enqueue(case)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.company_memberships WHERE user_id=%s AND company_id=%s", (case.employee.user_id, case.company))
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT count(*) FROM ocr_jobs WHERE id=?", (job["id"],)).fetchone()[0] == 0


def test_persistent_fairness_company_cap_and_global_cap(queue_case):
    case = queue_case
    a1, a2 = enqueue(case), enqueue(case)
    other_actor = case.person("employee", company=case.other_company)
    other_case = SimpleNamespace(**{**vars(case), "company": case.other_company, "department": None, "employee": other_actor})
    b1 = enqueue(other_case)
    first, second = claim(case, "worker-a"), claim(case, "worker-b")
    assert {first["company_id"], second["company_id"]} == {case.company, case.other_company}
    assert claim(case, "worker-c") is None
    first_a = first if first["company_id"] == case.company else second
    first_b = second if first_a is first else first
    assert finish_job(case, first_a)
    third = claim(case, "worker-restarted")
    assert third["company_id"] == case.company
    assert third["id"] in {a1["id"], a2["id"]} - {first_a["id"]}
    assert finish_job(case, third)
    assert finish_job(case, first_b)
    # Both companies enqueue again. The durable sequence puts the company that
    # was dispatched least recently first, independent of worker identity.
    enqueue(case)
    next_b = enqueue(other_case)
    assert claim(case, "new-process")["id"] == next_b["id"]


def test_expired_unsent_attempt_requeues_with_new_token_and_fences_old_worker(queue_case):
    case = queue_case
    enqueue(case)
    old = claim(case)
    expire(case, old["id"])
    assert recover(case) == 1
    assert get_job(case, old["id"])["status"] == "retry_wait"
    assert not finish_job(case, old)
    new = claim(case)
    assert new["id"] == old["id"] and new["lease_token"] != old["lease_token"] and new["attempt_count"] == 2
    with worker(case) as conn:
        assert not conn.execute("SELECT accounting.worker_check_ocr_lease(%s,%s) AS ok", (old["id"], old["lease_token"])).fetchone()["ok"]
    with case.db.transaction(actor=case.employee) as conn:
        attempts = conn.execute("SELECT attempt_no,status,phase FROM ocr_attempts WHERE job_id=? ORDER BY attempt_no", (old["id"],)).fetchall()
        assert [tuple(row.values()) if hasattr(row, "values") else tuple(row) for row in attempts] == [(1, "interrupted", "finished"), (2, "running", "claimed")]


def test_expired_sending_requires_manual_retry_and_stales_unpublished_run(queue_case):
    case = queue_case
    job = enqueue(case)
    active = claim(case)
    run_id = ocr_run(case, job["event_id"], job["attachment_id"])
    assert bind(case, active, run_id) and sending(case, active)
    expire(case, active["id"])
    assert recover(case) == 1
    result = get_job(case, job["id"])
    assert result["status"] == "interrupted" and result["finished_at"]
    assert claim(case) is None
    assert finish(case, run_id) == 0  # A late response cannot publish evidence.


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "stale"])
def test_recovery_adopts_already_committed_ai_result(queue_case, outcome):
    case = queue_case
    job = enqueue(case)
    active = claim(case)
    run_id = ocr_run(case, job["event_id"], job["attachment_id"])
    assert bind(case, active, run_id) and sending(case, active)
    with case.db.transaction(write=True, actor=case.employee) as conn:
        conn.execute("UPDATE ai_runs SET status=?,result_json=?,finished_at=? WHERE id=?", (outcome, '{}' if outcome == 'succeeded' else None, now(), run_id))
    expire(case, active["id"])
    assert recover(case) == 1
    assert get_job(case, job["id"])["status"] == outcome


def test_binding_cannot_cross_event_run_or_requester(queue_case):
    case = queue_case
    job = enqueue(case)
    active = claim(case)
    another_event = insert_event(case)
    another_attachment = attach(case, another_event)
    wrong_event_run = ocr_run(case, another_event, another_attachment)
    wrong_actor_run = ocr_run(case, job["event_id"], job["attachment_id"], actor=case.finance)
    assert not bind(case, active, wrong_event_run)
    assert not bind(case, active, wrong_actor_run)
    assert not sending(case, active)
    correct_run = ocr_run(case, job["event_id"], job["attachment_id"])
    assert bind(case, active, correct_run)
    assert bind(case, active, correct_run)
    assert not bind(case, active, wrong_event_run)


def test_cancel_scope_and_late_result_fencing(queue_case):
    case = queue_case
    job = enqueue(case)
    active = claim(case)
    run_id = ocr_run(case, job["event_id"], job["attachment_id"])
    assert bind(case, active, run_id) and sending(case, active)
    with case.db.transaction(write=True, actor=case.colleague) as conn:
        assert not conn.execute("SELECT accounting.cancel_ocr_job(?,'撤回')", (job["id"],)).fetchone()[0]
    with case.db.transaction(write=True, actor=case.finance) as conn:
        assert conn.execute("SELECT accounting.cancel_ocr_job(?,'核对资料后撤回')", (job["id"],)).fetchone()[0]
    assert get_job(case, job["id"])["status"] == "cancelled"
    assert not finish_job(case, active, "succeeded", run_id=run_id)
    assert finish(case, run_id) == 0


def test_auto_retry_budget_and_explicit_retry_keep_attempt_history(queue_case):
    case = queue_case
    job = enqueue(case)
    for attempt in range(1, 4):
        active = claim(case)
        assert active["attempt_count"] == attempt
        assert finish_job(case, active, "retry_wait", code="provider_busy")
        assert get_job(case, job["id"])["status"] == ("retry_wait" if attempt < 3 else "failed")
    assert claim(case) is None
    with case.db.transaction(write=True, actor=case.finance) as conn:
        # Use the database clock without granting the app a private worker
        # helper. Windows/WSL clock skew must not defer an immediate unit job.
        stamp = conn.execute('''SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')''').fetchone()[0]
        assert conn.execute("UPDATE ocr_jobs SET status='queued',execution_user_id=?,max_attempts=attempt_count+3,current_run_id=NULL,error_code='',error_detail='',finished_at=NULL,available_at=?,updated_at=? WHERE id=?", (case.finance.user_id, stamp, stamp, job["id"])).rowcount == 1
        conn.execute("INSERT INTO ocr_retries VALUES(?,?,?,?,?,?,?)", (job["id"], uuid4().hex, case.finance.firm_id, case.company, job["event_id"], case.finance.user_id, stamp))
    fresh = claim(case)
    assert fresh["attempt_count"] == 4 and fresh["max_attempts"] == 6 and fresh["execution_user_id"] == case.finance.user_id
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT count(*) FROM ocr_attempts WHERE job_id=?", (job["id"],)).fetchone()[0] == 4
    assert finish_job(case, fresh)
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.db.transaction(write=True, actor=case.employee) as conn:
            conn.execute("UPDATE ocr_jobs SET status='queued',execution_user_id=?,max_attempts=30,current_run_id=NULL,error_code='',error_detail='',finished_at=NULL WHERE id=?", (case.employee.user_id, job["id"]))


@pytest.mark.parametrize("lease_seconds,global_limit", [(29, 2), (601, 2), (180, 9), (180, 0)])
def test_worker_dispatch_bounds_are_database_enforced(queue_case, lease_seconds, global_limit):
    with pytest.raises(queue_case.psycopg.errors.InvalidParameterValue):
        with worker(queue_case) as conn:
            conn.execute("SELECT * FROM accounting.claim_ocr_job('worker',%s,%s)", (lease_seconds, global_limit))


def test_attempt_terminal_history_remains_immutable_even_for_admin(queue_case):
    case = queue_case
    enqueue(case)
    active = claim(case)
    assert finish_job(case, active)
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.ocr_attempts SET error_detail='重写历史' WHERE job_id=%s", (active["id"],))


def test_simultaneous_workers_cannot_claim_same_job_or_exceed_company_cap(queue_case):
    case = queue_case
    jobs = [enqueue(case) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda index: claim(case, f"concurrent-{index}"), range(4)))
    winners = [row for row in results if row is not None]
    assert len(winners) == 1
    assert winners[0]["id"] in {job["id"] for job in jobs}
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT count(*) FROM ocr_attempts WHERE firm_id=?", (case.owner.firm_id,)).fetchone()[0] == 1


def test_worker_provisioning_has_only_required_roles_and_hides_password(queue_case, capsys):
    from scripts.migrate_accounting import provision_worker_login

    case = queue_case
    username = "worker_provision_" + uuid4().hex
    password = "dedicated-test-only-password-" + uuid4().hex
    try:
        assert provision_worker_login(ADMIN_URL, username, password) == username
        assert password not in capsys.readouterr().out
        with case.psycopg.connect(ADMIN_URL) as conn:
            flags = conn.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s", (username,)).fetchone()
            assert flags == (True, False, False, False, False, False)
            roles = {row[0] for row in conn.execute("SELECT p.rolname FROM pg_auth_members m JOIN pg_roles p ON p.oid=m.roleid JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s", (username,))}
            assert roles == {"accounting_app", "accounting_worker"}
        with pytest.raises(ValueError, match="已存在"):
            provision_worker_login(ADMIN_URL, username, password)
        with pytest.raises(ValueError, match="20"):
            provision_worker_login(ADMIN_URL, "worker_short_" + uuid4().hex, "too-short")
    finally:
        from psycopg import sql
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(username)))


@pytest.mark.parametrize("outcome", ["failed", "interrupted", "retry_wait", "stale"])
def test_early_finish_also_fences_pending_run(queue_case, outcome):
    case = queue_case
    job = enqueue(case)
    active = claim(case)
    run_id = ocr_run(case, job["event_id"], job["attachment_id"])
    assert bind(case, active, run_id)
    assert finish_job(case, active, outcome, run_id=run_id)
    assert finish(case, run_id) == 0
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT status FROM ai_runs WHERE id=?", (run_id,)).fetchone()[0] == "stale"


@pytest.mark.parametrize("interrupted", [False, True])
def test_disabling_company_processing_still_allows_scoped_cancel(queue_case, interrupted):
    case = queue_case
    job = enqueue(case)
    if interrupted:
        active = claim(case)
        assert finish_job(case, active, "interrupted")
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.ai_company_settings SET enabled=0 WHERE company_id=%s", (case.company,))
    with case.db.transaction(write=True, actor=case.employee) as conn:
        assert conn.execute("SELECT accounting.cancel_ocr_job(?,'停止处理，等补充原件')", (job["id"],)).fetchone()[0]
    assert get_job(case, job["id"])["status"] == "cancelled"


def test_fencing_migration_rejects_existing_dangerous_worker_role(queue_case):
    from pathlib import Path

    case = queue_case
    migration = Path(__file__).resolve().parents[1] / "migrations" / "postgres" / "014_ocr_queue_fencing.sql"
    # Role mutation stays in an uncommitted transaction and is rolled back
    # together with the rejected migration; concurrent workers see no change.
    with pytest.raises(case.psycopg.errors.RaiseException, match="unsafe existing capabilities"):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("ALTER ROLE accounting_worker CREATEDB")
            conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
    with case.psycopg.connect(ADMIN_URL) as conn:
        assert not conn.execute("SELECT rolcreatedb FROM pg_roles WHERE rolname='accounting_worker'").fetchone()[0]


def insert_batch_receipt(case, job, actor=None, request_key=None, **changes):
    actor = actor or case.employee
    values = {
        "batch_id": job["batch_id"], "request_key": request_key or uuid4().hex,
        "firm_id": actor.firm_id, "company_id": case.company,
        "requested_by": actor.user_id, "created_at": now(), **changes,
    }
    with case.db.transaction(write=True, actor=actor) as conn:
        conn.execute(f"INSERT INTO ocr_batch_retries({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
    return values["request_key"]


def test_batch_retry_receipt_is_personal_and_follows_current_batch_visibility(queue_case):
    case = queue_case
    job = enqueue(case)
    key = insert_batch_receipt(case, job)
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT count(*) FROM ocr_batch_retries WHERE batch_id=? AND request_key=?", (job["batch_id"], key)).fetchone()[0] == 1
    for actor in (case.finance, case.approver, case.colleague, case.owner, Actor(case.employee.user_id, "wrong-firm")):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT count(*) FROM ocr_batch_retries WHERE batch_id=?", (job["batch_id"],)).fetchone()[0] == 0
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        insert_batch_receipt(case, job, actor=case.colleague)
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        insert_batch_receipt(case, job, actor=case.finance, requested_by=case.employee.user_id)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s", (case.company, case.employee.user_id))
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT count(*) FROM ocr_batch_retries WHERE batch_id=?", (job["batch_id"],)).fetchone()[0] == 0
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with worker(case) as conn:
            conn.execute("SELECT * FROM accounting.ocr_batch_retries")


def test_batch_retry_receipt_is_unique_scoped_and_immutable(queue_case):
    case = queue_case
    job = enqueue(case)
    key = insert_batch_receipt(case, job)
    with pytest.raises(case.psycopg.errors.UniqueViolation):
        insert_batch_receipt(case, job, request_key=key)
    # Another currently authorized requester has a separate command namespace,
    # without learning the first requester's accepted item set or count.
    insert_batch_receipt(case, job, actor=case.finance, request_key=key)
    with pytest.raises(case.psycopg.errors.ForeignKeyViolation):
        insert_batch_receipt(case, job, company_id=case.other_company)
    with pytest.raises(case.psycopg.errors.CheckViolation):
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.ocr_batch_retries SET request_key='rewritten' WHERE batch_id=%s AND requested_by=%s", (job["batch_id"], case.employee.user_id))
