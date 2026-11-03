"""DCP closing gates: real restricted PostgreSQL, never production or models."""

from uuid import uuid4
import pytest

from app.accounting.service import AccountingService
from app.handoff.service import HandoffService
from app.tax import filings
from app.assistant import investigation
from test_tax_workpaper_postgres import pg, case, native  # noqa: F401


def payload(c, **changes):
    return {
        "book_id": c.book["id"],
        "period": "2026-10",
        "kind": "vat",
        "status": "filed",
        "external_ref": uuid4().hex,
        "occurred_at": "2026-11-01",
        "amount_cents": 0,
        **changes,
    }


def test_restricted_filings_and_handoff_success(case):
    c = case
    record = filings.record(c.tax, c.reviewer, payload(c))
    assert filings.list_for(c.tax, c.viewer, c.book["id"])[0]["id"] == record["id"]
    service = HandoffService(c.db)
    row = service.assign(c.maker, c.paper_id, 1, c.reviewer.user_id, "2026-11-10", "虚构底稿指派")
    assert row["assignee_id"] == c.reviewer.user_id
    assert (
        service.assign(c.maker, c.paper_id, 1, c.reviewer.user_id, "2026-11-10", "虚构底稿指派")[
            "id"
        ]
        == row["id"]
    )
    assert any(x["source"]["id"] == c.paper_id for x in service.queue(c.reviewer)["items"])


@pytest.mark.parametrize("table", ["tax_filings", "handoff_tasks"])
def test_new_tables_force_rls_without_worker_write(case, table):
    c = case
    with native(c, c.maker) as conn:
        row = conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=?::regclass",
            ("accounting." + table,),
        ).fetchone()
        assert all(row.values())
        assert not conn.execute(
            "SELECT has_table_privilege('accounting_worker',?,'INSERT') AS allowed",
            ("accounting." + table,),
        ).fetchone()["allowed"]


@pytest.mark.parametrize("field", ["company_id", "created_by", "book_id", "occurred_at"])
def test_native_filing_scope_identity_and_date(case, field):
    c = case
    values = {
        "id": uuid4().hex,
        "firm_id": c.maker.firm_id,
        "company_id": c.book["company_id"],
        "book_id": c.book["id"],
        "period": "2026-10",
        "kind": "vat",
        "status": "filed",
        "external_ref": uuid4().hex,
        "occurred_at": "2026-11-01",
        "amount_cents": 0,
        "created_by": c.reviewer.user_id,
        "created_at": "2026-11-01T00:00:00Z",
    }
    values[field] = {
        "company_id": c.other["company_id"],
        "book_id": c.other["id"],
        "created_by": c.maker.user_id,
        "occurred_at": "2026-99-99",
    }[field]
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.reviewer) as conn:
            conn.execute(
                "INSERT INTO tax_filings("
                + ",".join(values)
                + ") VALUES("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
    assert error.value.sqlstate in {"42501", "23514"}


def test_investigation_uses_read_transactions_and_separate_audit(case, monkeypatch):
    c = case
    monkeypatch.setenv("ACCOUNTING_ASSISTANT_INVESTIGATION", "1")
    result = investigation.investigate(AccountingService(c.db), c.maker, c.book["id"], "2026-10")
    assert result["status"] == "complete"
    assert result["coverage"]["read"] == 3


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from app.accounting.domain import AccountingError
from app.batch_ops.service import BatchOperationsService
from app.batch_ops.models import CreateMonthlyOpenBatch
from app.business.service import BusinessService
from app.ledgers.service import LedgersService
from app.monthly.batch import MonthlyBatchService
from app.service_plans.service import ServicePlansService
from app.subledger.models import ControlAccountCreate
from app.subledger.service import SubledgerService
from test_monthly_delivery_postgres import p0_pg, package_case  # noqa: F401
from test_p0_close_upload_postgres import p0_case  # noqa: F401
from test_tax_workpaper_postgres import ADMIN_URL


@pytest.fixture(scope="module")
def worker_db():
    import os
    from app.accounting.database import Database

    db = Database(os.environ["ACCOUNTING_TEST_WORKER_DATABASE_URL"])
    yield db
    db.close()


def batches(c, worker_db):
    ledger = AccountingService(c.db)
    business = BusinessService(c.db)
    monthly = MonthlyBatchService(
        c.db, ledger, LedgersService(c.db), ServicePlansService(c.db, ledger, business), business
    )
    return BatchOperationsService(worker_db, monthly)


def ready(c, batch):
    # Host/WSL clocks can differ by milliseconds. Advance only the fixture's
    # availability, never bypass production lease or state predicates.
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.batch_operation_items SET available_at='2000-01-01' WHERE batch_id=%s",
            (batch["id"],),
        )


def expire(c, item):
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.batch_operation_items SET lease_until='2000-01-01' WHERE id=%s",
            (item["item_id"],),
        )


@pytest.mark.parametrize("change", ["book", "version", "fingerprint", "actor", "assignee"])
def test_native_handoff_source_and_reviewer_binding(case, change):
    c = case
    row = HandoffService(c.db).assign(
        c.maker, c.paper_id, 1, c.reviewer.user_id, None, "原生指派绑定测试"
    )
    with native(c, c.maker) as conn:
        original = dict(
            conn.execute("SELECT * FROM handoff_tasks WHERE id=?", (row["id"],)).fetchone()
        )
        conn.execute(
            "UPDATE handoff_tasks SET superseded_at=? WHERE id=?", ("2026-11-02", row["id"])
        )
    values = {
        **original,
        "id": uuid4().hex,
        "superseded_at": None,
        "created_at": "2026-11-03T00:00:00Z",
    }
    key, val = {
        "book": ("book_id", c.other["id"]),
        "version": ("source_version", 2),
        "fingerprint": ("source_fingerprint", "f" * 64),
        "actor": ("created_by", c.reviewer.user_id),
        "assignee": ("assignee_id", c.maker.user_id),
    }[change]
    values[key] = val
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.maker) as conn:
            conn.execute(
                "INSERT INTO handoff_tasks("
                + ",".join(values)
                + ") VALUES("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
    assert error.value.sqlstate in {"42501", "23514"}


def test_handoff_concurrent_idempotency_and_reader_isolation(case):
    c = case
    barrier = Barrier(2)

    def assign():
        barrier.wait(timeout=10)
        return HandoffService(c.db).assign(
            c.maker, c.paper_id, 1, c.reviewer.user_id, None, "并发指派验证"
        )["id"]

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(assign) for _ in range(2)]
        assert len({f.result(timeout=30) for f in futures}) == 1
    with pytest.raises(AccountingError):
        HandoffService(c.db).assign(
            c.viewer, c.paper_id, 1, c.reviewer.user_id, None, "只读无权指派"
        )
    with native(c, c.reviewer) as conn:
        assert not conn.execute(
            "SELECT id FROM handoff_tasks WHERE book_id=?", (c.other["id"],)
        ).fetchall()
        assert not conn.execute(
            "SELECT id FROM tax_filings WHERE book_id=?", (c.other["id"],)
        ).fetchall()
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.maker) as conn:
            conn.execute(
                "UPDATE handoff_tasks SET reason='改写历史' WHERE source_id=?", (c.paper_id,)
            )
    assert error.value.sqlstate == "42501"


def test_locked_mapping_native_and_future_configuration(package_case):
    c = package_case
    with c.db.transaction(actor=c.manager) as conn:
        account = conn.execute(
            "SELECT a.id,a.current_version AS version FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version JOIN ledger_periods lp ON lp.ledger_id=a.ledger_id WHERE lp.book_id=? AND v.code='2202'",
            (c.book["id"],),
        ).fetchone()
    assert account
    args = dict(
        book_id=c.book["id"],
        kind="payable",
        control_account_id=account["id"],
        control_account_version=account["version"],
        effective_from="2026-10",
        effective_to="2026-10",
        reason="锁期口径保护",
    )
    with pytest.raises(AccountingError) as error:
        SubledgerService(c.db).configure_control_account(c.manager, ControlAccountCreate(**args))
    assert error.value.status == 409
    row = SubledgerService(c.db).configure_control_account(
        c.manager,
        ControlAccountCreate(**{**args, "effective_from": "2026-11", "effective_to": None}),
    )
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.manager) as conn:
            conn.execute(
                "UPDATE subledger_control_accounts SET effective_from='2026-10' WHERE id=?",
                (row["id"],),
            )
    assert error.value.sqlstate in {"23514", "55000"}

    with c.db.transaction(actor=c.manager) as conn:
        bank = conn.execute(
            "SELECT a.id,a.current_version AS version FROM ledger_accounts a JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version JOIN ledger_periods lp ON lp.ledger_id=a.ledger_id WHERE lp.book_id=? AND v.code='1002'",
            (c.book["id"],),
        ).fetchone()
    from app.subledger.models import BankAccountCreate

    with pytest.raises(AccountingError):
        SubledgerService(c.db).create_bank_account(
            c.manager,
            BankAccountCreate(
                book_id=c.book["id"],
                name="锁期后银行",
                ledger_account_id=bank["id"],
                ledger_account_version=bank["version"],
                reason="锁期后新增应拒绝",
            ),
        )
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.manager) as conn:
            conn.execute(
                "INSERT INTO bank_accounts(id,firm_id,company_id,book_id,name,ledger_account_id,ledger_account_version,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    c.manager.firm_id,
                    c.book["company_id"],
                    c.book["id"],
                    "伪造锁期银行",
                    bank["id"],
                    bank["version"],
                    c.manager.user_id,
                    "2026-11-02",
                ),
            )
    assert error.value.sqlstate == "23514"


def test_batch_two_claimers_cancel_recovery_and_stale_finish(case, worker_db):
    c = case
    s = batches(c, worker_db)
    batch = s.create_monthly_open(
        c.maker,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="companies",
            companies=[c.book["company_id"]],
            reason="并发领取取消测试",
            idempotency_key=uuid4().hex,
        ),
    )
    ready(c, batch)
    barrier = Barrier(2)

    def claim(n):
        barrier.wait(timeout=10)
        return s.claim("claim-" + str(n))

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(claim, n) for n in range(2)]
        claims = [f.result(timeout=30) for f in futures]
    assert sum(x is not None for x in claims) == 1
    item = next(x for x in claims if x)
    assert item["batch_id"] == batch["id"]
    s.cancel_item(c.maker, batch["id"], item["item_id"], "取消运行中任务")
    expire(c, item)
    assert s.finish(item, "succeeded", {"status": "created"}) is False
    s.recover()
    assert s.get(c.maker, batch["id"])["items"][0]["status"] == "cancelled"
    with pytest.raises(AccountingError):
        s.retry(c.maker, batch["id"])
    assert s.claim("cancel-not-reclaimed") is None


def test_batch_retry_exhaustion_and_real_monthly_success(case, worker_db):
    c = case
    s = batches(c, worker_db)
    request = CreateMonthlyOpenBatch(
        period="2026-11",
        scope="companies",
        companies=[c.book["company_id"]],
        reason="重试上限成功路径",
        idempotency_key=uuid4().hex,
    )
    batch = s.create_monthly_open(c.maker, request)
    ready(c, batch)
    for attempt in range(1, 4):
        item = s.claim("retry-limit")
        assert item["attempt"] == attempt
        assert s.finish(item, "failed", error_code="synthetic")
        if attempt < 3:
            s.retry(c.maker, batch["id"])
            ready(c, batch)
    with pytest.raises(AccountingError) as error:
        s.retry(c.maker, batch["id"])
    assert error.value.status == 409
    batch = s.create_monthly_open(
        c.maker, request.model_copy(update={"idempotency_key": uuid4().hex})
    )
    ready(c, batch)
    item = s.claim("real-open")
    result = s.execute(item)
    assert result["status"] == "created", result
    assert s.finish(item, "succeeded", result)
    assert s.get(c.maker, batch["id"])["succeeded"] == 1


def test_package_commit_loss_recovered_without_new_package(package_case, worker_db):
    c = package_case
    s = batches(c, worker_db)
    plan = s.preview_package_drafts(c.manager, [c.book["company_id"]], "2026-10")
    assert plan["ready"] == 1, plan
    batch = s.create_package_drafts(
        c.manager, [c.book["company_id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    ready(c, batch)
    item = s.claim("package-loss")
    result = s.execute(item)
    assert s.execute(item)["package_id"] == result["package_id"]
    s.cancel_item(c.manager, batch["id"], item["item_id"], "提交后收到取消请求")
    expire(c, item)
    s.recover()
    row = s.get(c.manager, batch["id"])["items"][0]
    assert row["status"] == "succeeded" and row["result"]["package_id"] == result["package_id"]
    assert not s.finish(item, "failed", error_code="late")
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) n FROM monthly_delivery_packages WHERE book_id=?", (c.book["id"],)
            ).fetchone()["n"]
            == 1
        )


from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_intelligence_workflow_postgres import pg_ai  # noqa: F401
from c01_bank_fixture import reviewed_export_draft


@pytest.mark.parametrize(
    "scenario",
    ["replay", "recover", "cancel", "changed_set", "foreign_claim", "revoked", "key_collision"],
)
def test_export_commit_fences_and_recovery(pg_ai, worker_db, scenario):
    c = pg_ai
    c.reviewer = c.actors["accountant"]
    s = batches(c, worker_db)
    if scenario == "revoked":
        from app.accounting.domain import Actor

        actor = Actor("dcp-admin-" + uuid4().hex, c.manager.firm_id)
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,'虚构可撤权管理员',%s)",
                (actor.user_id, actor.user_id + "@example.test"),
            )
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'admin')",
                (actor.firm_id, actor.user_id),
            )
        c.manager = actor
    reviewed_export_draft(c)
    plan = s.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    batch = s.create_review_export(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    ready(c, batch)
    item = s.claim("export-" + scenario)
    assert item["batch_id"] == batch["id"]
    try:
        if scenario == "key_collision":
            from app.accounting.models import ExportRequest

            other = reviewed_export_draft(c)
            c.ledger.export(
                c.manager,
                c.book["id"],
                ExportRequest(
                    draft_ids=[other["id"]],
                    idempotency_key=f"batch:{batch['id']}:{c.company['id']}",
                ),
            )
            expire(c, item)
            s.recover()
            assert s.get(c.manager, batch["id"])["items"][0]["status"] != "succeeded"
            return
        if scenario in {"replay", "recover"}:
            result = s.execute(item)
            if scenario == "replay":
                assert s.execute(item)["export_id"] == result["export_id"]
                assert s.finish(item, "succeeded", result)
            else:
                expire(c, item)
                s.recover()
                row = s.get(c.manager, batch["id"])["items"][0]
                assert (
                    row["status"] == "succeeded"
                    and row["result"]["export_id"] == result["export_id"]
                )
                assert not s.finish(item, "failed", error_code="late")
            with c.db.transaction(actor=c.manager) as conn:
                assert (
                    conn.execute(
                        "SELECT COUNT(*) n FROM exports WHERE book_id=?", (c.book["id"],)
                    ).fetchone()["n"]
                    == 1
                )
            return
        if scenario == "cancel":
            s.cancel_item(c.manager, batch["id"], item["item_id"], "取消导出测试")
        if scenario == "changed_set":
            reviewed_export_draft(c)
        if scenario == "foreign_claim":
            item = {**item, "company_id": c.other_company["id"]}
        if scenario == "revoked":
            with c.psycopg.connect(ADMIN_URL) as conn:
                conn.execute(
                    "UPDATE accounting.memberships SET role='viewer' WHERE firm_id=%s AND user_id=%s",
                    (c.manager.firm_id, c.manager.user_id),
                )
        with pytest.raises(AccountingError):
            s.execute(item)
        with c.psycopg.connect(ADMIN_URL) as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM accounting.exports WHERE book_id=%s", (c.book["id"],)
                ).fetchone()[0]
                == 0
            )
    finally:
        # Only terminalize this fixture; never grant runtime new permissions.
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute(
                "UPDATE accounting.batch_operation_items SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE batch_id=%s AND status IN('queued','running','retry_wait')",
                (batch["id"],),
            )


def test_native_handoff_rechecks_authority_after_lock_wait(case):
    from queue import Queue
    import time

    c = case
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.memberships SET role='admin' WHERE firm_id=%s AND user_id=%s",
            (c.editor.firm_id, c.editor.user_id),
        )
    row = HandoffService(c.db).assign(
        c.editor, c.paper_id, 1, c.reviewer.user_id, None, "原生撤权竞争验证"
    )
    with native(c, c.editor) as conn:
        value = dict(
            conn.execute("SELECT * FROM handoff_tasks WHERE id=?", (row["id"],)).fetchone()
        )
        conn.execute(
            "UPDATE handoff_tasks SET superseded_at=? WHERE id=?", ("2026-11-02", row["id"])
        )
    value.update(id=uuid4().hex, superseded_at=None, created_at="2026-11-04")
    pids = Queue()

    def waiting_insert():
        with native(c, c.editor) as conn:
            conn.execute("SET LOCAL statement_timeout='10s'")
            pids.put(conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
            conn.execute(
                "INSERT INTO handoff_tasks("
                + ",".join(value)
                + ") VALUES("
                + ",".join("?" for _ in value)
                + ")",
                tuple(value.values()),
            )

    with ThreadPoolExecutor(1) as pool:
        with c.db.transaction(write=True, actor=c.maker) as holder:
            pid_holder = holder.execute("SELECT pg_backend_pid()").fetchone()[0]
            task = pool.submit(waiting_insert)
            pid = pids.get(timeout=5)
            deadline = time.monotonic() + 5
            with c.psycopg.connect(ADMIN_URL, autocommit=True) as monitor:
                while (
                    pid_holder
                    not in monitor.execute("SELECT pg_blocking_pids(%s)", (pid,)).fetchone()[0]
                ):
                    assert time.monotonic() < deadline, "No actual PostgreSQL lock wait"
                    time.sleep(0.02)
            holder.execute(
                "UPDATE memberships SET role='operator' WHERE firm_id=? AND user_id=?",
                (c.editor.firm_id, c.editor.user_id),
            )
        with pytest.raises(c.psycopg.Error) as error:
            task.result(timeout=15)
    assert error.value.sqlstate == "42501"


def test_handoff_batch_projection_binds_exact_failed_item(case, worker_db):
    """NEXT04: a failed batch item is projected and assignable to a firm manager."""
    c = case
    s = batches(c, worker_db)
    request = CreateMonthlyOpenBatch(
        period="2026-11",
        scope="companies",
        companies=[c.book["company_id"]],
        reason="NEXT04 批量失败接手",
        idempotency_key=uuid4().hex,
    )
    batch = s.create_monthly_open(c.maker, request)
    ready(c, batch)
    item = s.claim("n04-batch")
    assert s.finish(item, "failed", error_code="synthetic")
    import json

    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "UPDATE accounting.batch_operation_items SET result_json=%s WHERE id=%s",
            (json.dumps({"book_id": c.book["id"]}), item["item_id"]),
        )
    queued = HandoffService(c.db).queue(c.maker)["items"]
    source = next(
        row
        for row in queued
        if row["source"]["kind"] == "batch_item" and row["scope"]["batch_id"] == batch["id"]
    )
    assigned = HandoffService(c.db).assign(
        c.maker,
        source["source"]["id"],
        source["source"]["version"],
        c.maker.user_id,
        None,
        "NEXT04 批量失败指派",
        kind="batch_item",
    )
    assert assigned["source"]["kind"] == "batch_item"
    assert assigned["assignee_id"] == c.maker.user_id


def test_handoff_delivery_package_projection_and_guard(package_case):
    """NEXT04: an unacknowledged frozen package is a delivery handoff source."""
    c = package_case
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    queued = HandoffService(c.db).queue(c.manager)["items"]
    source = next(
        row
        for row in queued
        if row["source"]["kind"] == "delivery_package" and row["source"]["id"] == package["id"]
    )
    assert source["role"] == "delivery"
    assigned = HandoffService(c.db).assign(
        c.manager,
        package["id"],
        package["version"],
        c.reviewer.user_id,
        None,
        "NEXT04 交付包指派",
        kind="delivery_package",
    )
    assert assigned["assignee_id"] == c.reviewer.user_id
    assert assigned["source"]["fingerprint"] == package["manifest_sha256"]
