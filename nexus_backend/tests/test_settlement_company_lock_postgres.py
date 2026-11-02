"""Restricted, real two-session tests for the settlement write-domain contract."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from queue import Queue
import time
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.settlements.models import AllocationConfirm, AllocationReverse, OpenItemCreate
from app.settlements.service import SettlementService
from period_fixtures import restore_period_state
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
from test_intelligence_workflow import create_event, handoff, import_bank
from test_intelligence_workflow_postgres import pg_ai  # noqa: F401
from test_stage10_settlements_postgres import prepare, preview


FUNCTIONS = {
    "settlement_open_item": "text,text,text,integer,text,text,text",
    "settlement_make_preview": "text,text",
    "settlement_confirm": "text,text,text",
    "settlement_reverse": "text,text,text,text,text",
}
GLOBAL_KEY = 6420031786281903101


@pytest.fixture(autouse=True)
def require_isolated_postgres():
    assert ADMIN_URL and APP_URL, "Mandatory PG gate: missing isolated DSNs, never skip"


def pending(c):
    """Use the real business handoff; stop before the first settlement mutation."""
    c.intelligence.company_settings(c.manager, c.company["id"], False)
    original = handoff(c, create_event(c, template="procurement_expense", payment_status="unpaid"))
    party = LedgersService(c.db).create_entity(
        c.manager, c.book["id"], EntityCreate(kind="counterparty", code="SUP-LOCK", name="耗材商店")
    )
    service = SettlementService(c.db)
    draft = c.ledger.get_draft(c.actors["accountant"], c.book["id"], original["draft_id"])
    request = OpenItemCreate(
        source_id=original["source_id"],
        counterparty_id=party["id"],
        expected_draft_version=draft["version"],
        idempotency_key=uuid4().hex,
        reason="隔离测试核验未付款采购",
    )
    import_bank(c, external_id="settlement-lock-" + uuid4().hex, amount="0.30")
    bank = service.overview(c.actors["accountant"], c.book["id"])["banks"][0]
    return service, original, request, bank


@pytest.mark.parametrize("name", FUNCTIONS)
def test_native_mutations_share_company_contract_and_preserve_acl(business_postgres, name):
    db, psycopg = business_postgres
    with psycopg.connect(ADMIN_URL) as conn:
        assert int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000 in (16, 17)
        signature = f"accounting.{name}({FUNCTIONS[name]})"
        body, security, settings = conn.execute(
            "SELECT prosrc,prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
            (signature,),
        ).fetchone()
        assert "accounting.lock_company_write(" in body, name
        assert "accounting.lock_legacy_write()" not in body, name
        assert security and "search_path=pg_catalog, accounting, pg_temp" in settings
        assert conn.execute(
            "SELECT has_function_privilege('accounting_app',%s,'EXECUTE')", (signature,)
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT has_function_privilege('accounting_worker',%s,'EXECUTE')", (signature,)
        ).fetchone()[0]
        assert not conn.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_proc p, LATERAL aclexplode(p.proacl) a "
            "WHERE p.oid=%s::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')",
            (signature,),
        ).fetchone()[0]


def test_all_four_operations_do_not_wait_for_another_company_and_replay(pg_ai):
    c = pg_ai
    service, _, request, bank = pending(c)

    def run():
        item = service.open_item(c.actors["accountant"], c.book["id"], request)
        assert service.open_item(c.actors["accountant"], c.book["id"], request)["id"] == item["id"]
        plan = preview(c, service, item, bank)
        command = AllocationConfirm(idempotency_key=uuid4().hex)
        first = service.confirm(c.actors["accountant"], c.book["id"], plan["id"], command)
        assert service.confirm(c.actors["accountant"], c.book["id"], plan["id"], command) == first
        row = service.overview(c.manager, c.book["id"])["allocations"][0]
        reverse = AllocationReverse(
            expected_fingerprint=row["fingerprint"],
            idempotency_key=uuid4().hex,
            reason="独立核验后撤销分配",
        )
        result = service.reversal(c.manager, c.book["id"], row["id"], reverse)
        assert service.reversal(c.manager, c.book["id"], row["id"], reverse) == result
        assert service.overview(c.manager, c.book["id"])["items"][0]["remaining"] == "0.30"

    with ThreadPoolExecutor(max_workers=1) as pool:
        # Shared compatibility gate + B's exclusive company lock stays held
        # throughout all A mutations. A global exclusive lock would deadlock.
        with c.db.transaction(
            write=True, actor=c.manager, company_id=c.other_company["id"], write_scope="company"
        ):
            result = pool.submit(run)
            result.result(timeout=15)
    assert c.provider.extract_calls == c.provider.proposal_calls == 0


def native_confirm(c, plan_id, key, *, actor=None, company=None):
    with c.db.transaction(
        write=True,
        actor=actor or c.actors["accountant"],
        company_id=company or c.company["id"],
        write_scope="company",
    ) as conn:
        pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
        result = conn.execute(
            "SELECT accounting.settlement_confirm(?,?,?)", (c.book["id"], plan_id, key)
        ).fetchone()[0]
        return pid, result


@pytest.mark.parametrize("same_key", [True, False])
def test_two_native_sessions_replay_or_reject_stale_never_overallocate(pg_ai, same_key):
    c = pg_ai
    service, _, item, bank = prepare(c)
    first = preview(c, service, item, bank, "0.20")
    second = first if same_key else preview(c, service, item, bank, "0.20")
    key = uuid4().hex
    barrier = Barrier(2)

    def compete(plan, operation_key):
        barrier.wait(timeout=10)
        try:
            return native_confirm(c, plan["id"], operation_key)
        except c.psycopg.errors.RaiseException as exc:
            assert "remaining balance required" in str(exc) or "stale" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(compete, first, key),
            pool.submit(compete, second, key if same_key else uuid4().hex),
        ]
        results = [job.result(timeout=20) for job in jobs]
    success = [r for r in results if r]
    if same_key:
        assert len(success) == 2 and success[0][0] != success[1][0]
        assert success[0][1] == success[1][1]
        assert native_confirm(c, first["id"], key)[1] == success[0][1]  # response lost, replay
    else:
        assert len(success) == 1
    overview = service.overview(c.manager, c.book["id"])
    assert len(overview["allocations"]) == 1 and overview["items"][0]["remaining"] == "0.10"


def test_scope_permission_revocation_and_locked_period_are_not_bypassed(pg_ai):
    c = pg_ai
    service, _, item, bank = prepare(c)
    plan = preview(c, service, item, bank)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege, match="company scope"):
        native_confirm(c, plan["id"], uuid4().hex, actor=c.manager, company=c.other_company["id"])
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        native_confirm(c, plan["id"], uuid4().hex, actor=c.actors["employee"])
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "DELETE FROM book_access WHERE book_id=? AND user_id=?",
            (c.book["id"], c.actors["accountant"].user_id),
        )
    with pytest.raises(AccountingError) as denied:
        native_confirm(c, plan["id"], uuid4().hex)
    assert denied.value.status == 403
    # Native SQL also checks revoked access without relying on the service gate.
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute(
                "SELECT accounting.settlement_confirm(?,?,?)",
                (c.book["id"], plan["id"], uuid4().hex),
            )
    # Owner's own preview, then a historical closed-period fixture. No guard disabled.
    from app.settlements.models import AllocationPreview

    owned = service.preview(
        c.manager,
        c.book["id"],
        AllocationPreview(
            bank_source_id=bank["id"],
            parts=[{"item_id": item["id"], "amount": "0.30"}],
            reason="独立核验关闭期间写入拒绝",
        ),
    )
    restore_period_state(c.db, c.book["id"])
    with pytest.raises(c.psycopg.errors.RaiseException, match="open"):
        native_confirm(c, owned["id"], uuid4().hex, actor=c.manager)
    assert service.overview(c.manager, c.book["id"])["allocations"] == []


def test_native_rollback_and_lock_modes_keep_legacy_compatibility(pg_ai):
    c = pg_ai
    service, _, item, bank = prepare(c)
    plan = preview(c, service, item, bank)
    key = uuid4().hex
    for scoped in (True, False):
        options = {"company_id": c.company["id"], "write_scope": "company"} if scoped else {}
        with pytest.raises(RuntimeError, match="rollback after native write"):
            with c.db.transaction(write=True, actor=c.actors["accountant"], **options) as conn:
                conn.execute(
                    "SELECT accounting.settlement_confirm(?,?,?)", (c.book["id"], plan["id"], key)
                )
                locks = conn.execute(
                    "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND classid=? AND objid=?",
                    (GLOBAL_KEY >> 32, GLOBAL_KEY & 0xFFFFFFFF),
                ).fetchall()
                assert {r["mode"] for r in locks} == (
                    {"ShareLock"} if scoped else {"ExclusiveLock"}
                )
                if scoped:
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND mode='ExclusiveLock'"
                        ).fetchone()[0]
                        >= 1
                    )
                raise RuntimeError("rollback after native write")
        assert service.overview(c.manager, c.book["id"])["allocations"] == []
    native_confirm(c, plan["id"], key)
    assert len(service.overview(c.manager, c.book["id"])["allocations"]) == 1


def test_two_native_sessions_reverse_once_then_reallocate(pg_ai):
    c = pg_ai
    service, _, item, bank = prepare(c)
    plan = preview(c, service, item, bank)
    native_confirm(c, plan["id"], uuid4().hex)
    row = service.overview(c.manager, c.book["id"])["allocations"][0]
    key, barrier = uuid4().hex, Barrier(2)

    def reverse():
        barrier.wait(timeout=10)
        with c.db.transaction(
            write=True, actor=c.manager, company_id=c.company["id"], write_scope="company"
        ) as conn:
            pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            result = conn.execute(
                "SELECT accounting.settlement_reverse(?,?,?,?,?)",
                (c.book["id"], row["id"], row["fingerprint"], key, "独立核验原分配错误"),
            ).fetchone()[0]
            return pid, result

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reverse) for _ in range(2)]
        results = [job.result(timeout=20) for job in futures]
    assert results[0][0] != results[1][0] and results[0][1] == results[1][1]
    assert service.overview(c.manager, c.book["id"])["items"][0]["remaining"] == "0.30"
    fresh = preview(c, service, item, bank)
    native_confirm(c, fresh["id"], uuid4().hex)
    current = service.overview(c.manager, c.book["id"])
    assert current["items"][0]["remaining"] == "0.00"
    assert len(current["allocations"]) == 2
    assert sum(not r["reversed"] for r in current["allocations"]) == 1


def test_access_is_rechecked_after_lock_wait_and_revoke_commit(pg_ai):
    c = pg_ai
    service, _, item, bank = prepare(c)
    plan = preview(c, service, item, bank)
    pids = Queue()

    def waiting():
        # Connect as the restricted login; observe its real wait in pg_locks.
        with c.psycopg.connect(APP_URL) as conn:
            conn.execute("SET LOCAL ROLE accounting_app")
            conn.execute("SET LOCAL statement_timeout='10s'")
            conn.execute(
                "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
                (c.actors["accountant"].user_id, c.manager.firm_id),
            )
            pids.put(conn.info.backend_pid)
            conn.execute(
                "SELECT accounting.begin_company_write(%s,%s)", (c.manager.firm_id, c.company["id"])
            )
            conn.execute(
                "SELECT accounting.settlement_confirm(%s,%s,%s)",
                (c.book["id"], plan["id"], uuid4().hex),
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with c.db.transaction(write=True, actor=c.manager) as holder:
            holder_pid = holder.execute("SELECT pg_backend_pid()").fetchone()[0]
            job = pool.submit(waiting)
            pid = pids.get(timeout=5)
            deadline = time.monotonic() + 5
            with c.psycopg.connect(ADMIN_URL, autocommit=True) as monitor:
                while True:
                    blockers = monitor.execute("SELECT pg_blocking_pids(%s)", (pid,)).fetchone()[0]
                    if holder_pid in blockers:
                        break
                    assert time.monotonic() < deadline, "No real PostgreSQL lock wait observed"
                    time.sleep(0.02)
            holder.execute(
                "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                (c.book["id"], c.actors["accountant"].user_id),
            )
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            job.result(timeout=15)
    assert service.overview(c.manager, c.book["id"])["allocations"] == []


def test_accounting_review_still_required_before_clearance_export(pg_ai):
    c = pg_ai
    service, original, item, bank = prepare(c)
    plan = preview(c, service, item, bank)
    native_confirm(c, plan["id"], uuid4().hex)
    request = ExportRequest(
        draft_ids=[original["draft_id"], bank["draft_id"]], idempotency_key=uuid4().hex
    )
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors["accountant"], c.book["id"], request)
    draft = c.ledger.get_draft(c.actors["accountant"], c.book["id"], original["draft_id"])
    c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    clearance = service.clearance(c.actors["accountant"], c.book["id"], bank["id"])
    changed = c.ledger.update_draft(
        c.actors["accountant"],
        c.book["id"],
        bank["draft_id"],
        DraftUpdate(
            expected_version=clearance["draft_version"],
            description=clearance["description"],
            lines=clearance["lines"],
            correction_reason="核销应付不得重复费用",
        ),
    )
    with pytest.raises(AccountingError):
        c.ledger.approve(
            c.actors["accountant"],
            c.book["id"],
            changed["id"],
            ApprovalRequest(expected_version=changed["version"]),
        )
    # Use the known fixture reviewer; do not widen user-directory RLS.
    reviewer = c.manager
    assert reviewer.user_id != c.actors["accountant"].user_id
    c.ledger.approve(
        reviewer, c.book["id"], changed["id"], ApprovalRequest(expected_version=changed["version"])
    )
    assert c.ledger.export(c.actors["accountant"], c.book["id"], request)["count"] == 2


def test_forward_migration_repeat_preserves_function_identity_and_acl(business_postgres):
    _, psycopg = business_postgres
    migration = (
        Path(__file__).resolve().parents[1] / "migrations/postgres/196_settlement_company_lock.sql"
    )
    with psycopg.connect(ADMIN_URL) as conn:
        sql = "SELECT oid,prosrc,proacl::text,proowner,prosecdef,proconfig FROM pg_proc WHERE pronamespace='accounting'::regnamespace AND proname=ANY(%s) ORDER BY oid"
        before = conn.execute(sql, (list(FUNCTIONS),)).fetchall()
        conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
        conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
        assert conn.execute(sql, (list(FUNCTIONS),)).fetchall() == before
        conn.rollback()
