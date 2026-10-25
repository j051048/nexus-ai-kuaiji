"""Remaining C01 race/fault proofs on explicit disposable PostgreSQL targets.

All business actions use the restricted application role. Admin connections
create only synthetic identities, observe locks, or revoke test identities;
they never manufacture a passed preflight or a period lock.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.b04_close.models import CloseCommand, ClosePreflight
from app.b04_close.service import CloseService
from app.import_wizard.models import MappingSettings, PreviewRequest
from app.import_wizard.service import ImportWizardService
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.subledger.models import (
    AllocationCreate,
    AllocationPart,
    AllocationReverse,
    BankAccountCreate,
    BankCoverageCreate,
    BankCoverageReview,
    BankLineCreate,
    BankMatch,
    ControlAccountCreate,
    ControlApplicability,
    OpenItemCreate,
)
from test_monthly_delivery_prerequisite_postgres import declaration_case  # noqa: F401
from test_monthly_delivery_postgres import p0_case, p0_pg  # noqa: F401
from test_p0_close_upload_postgres import _await_blocked, _runtime_transaction
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_c01_bank_evidence_postgres import coverage_pg  # noqa: F401


def _accounts(c):
    with c.db.transaction(actor=c.manager) as conn:
        return {
            row["code"]: dict(row)
            for row in conn.execute(
                "SELECT a.id,a.ledger_id,a.current_version,v.code FROM ledger_periods p "
                "JOIN ledger_accounts a ON a.ledger_id=p.ledger_id "
                "JOIN ledger_account_versions v ON v.account_id=a.id AND v.version=a.current_version "
                "WHERE p.book_id=?",
                (c.book["id"],),
            )
        }


def _post(c, accounts, income=True):
    return PostingService(c.db).post_manual(
        c.manager,
        ManualVoucher(
            book_id=c.book["id"],
            command_key="c01-race-post-" + uuid4().hex,
            voucher_date="2026-10-05",
            description="合成本地银行收付",
            lines=[
                ManualLine(
                    account_id=accounts["1002"]["id"],
                    account_version=1,
                    debit="0.30" if income else "0.00",
                    credit="0.00" if income else "0.30",
                ),
                ManualLine(
                    account_id=accounts["2202"]["id"],
                    account_version=1,
                    debit="0.00" if income else "0.30",
                    credit="0.30" if income else "0.00",
                ),
            ],
        ),
    )


@pytest.fixture
def bank_case(declaration_case):
    c, sub = declaration_case
    sub.storage = c.store
    accounts = _accounts(c)
    config = sub.configure_control_account(
        c.manager,
        ControlAccountCreate(
            book_id=c.book["id"],
            kind="payable",
            control_account_id=accounts["2202"]["id"],
            control_account_version=1,
            effective_from="2026-10",
            reason="本机真实应付控制科目配置",
        ),
    )
    bank = sub.create_bank_account(
        c.manager,
        BankAccountCreate(
            book_id=c.book["id"],
            name="本机验收账户",
            ledger_account_id=accounts["1002"]["id"],
            ledger_account_version=1,
            reason="本机独立银行证据",
        ),
    )
    wizard = ImportWizardService(c.db, c.store)
    original = wizard.upload(
        c.manager,
        c.book["id"],
        "合成银行流水.csv",
        (
            "编号,日期,摘要,对方,金额,方向,凭据\n"
            "I001,2026-10-05,合成收款,合成公司,0.30,收入,合成原件1\n"
            "O001,2026-10-05,合成付款,合成公司,0.30,支出,合成原件2\n"
        ).encode(),
    )
    preview = wizard.preview(
        c.manager,
        c.book["id"],
        PreviewRequest(
            file_id=original["file_id"],
            namespace="c01-race",
            bank_account=bank["id"],
            mapping=MappingSettings(
                columns={
                    "external_id": 0,
                    "date": 1,
                    "description": 2,
                    "counterparty": 3,
                    "amount": 4,
                    "direction": 5,
                    "evidence_ref": 6,
                }
            ),
        ),
    )
    wizard.confirm(c.manager, c.book["id"], preview["id"])
    for income in (True, False):
        voucher = _post(c, accounts, income)
        line = next(item for item in voucher["lines"] if item["code_snapshot"] == "1002")
        bank_line = sub.add_bank_line(
            c.manager,
            BankLineCreate(
                book_id=c.book["id"],
                bank_account_id=bank["id"],
                line_date="2026-10-05",
                description="真实合成匹配流水",
                amount="0.30" if income else "-0.30",
            ),
        )
        sub.match_bank_line(c.manager, bank_line["id"], BankMatch(voucher_line_id=line["id"]))
    request = BankCoverageCreate(
        book_id=c.book["id"],
        account_id=bank["id"],
        period="2026-10",
        opening="0.00",
        closing="0.00",
        source_file_id=original["file_id"],
        source_preview_id=preview["id"],
        source_sha256=original["sha256"],
        reason="真实收付相抵原件，核验本期零余额",
    )
    return SimpleNamespace(c=c, sub=sub, accounts=accounts, config=config, request=request)


def _review(bank, row):
    return bank.sub.review_bank_coverage(
        bank.c.reviewer,
        row["id"],
        BankCoverageReview(expected_version=1, decision="verified", reason="独立核验合成原件"),
    )


def _preflight(bank, *, review=True):
    c = bank.c
    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")
    report_id = report["report"]["id"]
    report = reports.validate(c.manager, report_id)
    reports.review(
        c.reviewer,
        report_id,
        ReportCommand(expected_version=report["report"]["version"], reason="独立核验合成报表"),
    )
    close = CloseService(c.db)
    run = close.preflight(
        c.manager,
        ClosePreflight(
            book_id=c.book["id"],
            period="2026-10",
            report_id=report_id,
            idempotency_key="c01-race-close-" + uuid4().hex,
            reason="合成账套真实月结预检",
        ),
    )["close"]
    if review:
        assert run["status"] == "preflight_passed", run
        run = close.review(
            c.reviewer,
            run["id"],
            CloseCommand(expected_version=run["version"], reason="独立复核真实月结"),
        )["close"]
    return SimpleNamespace(close=close, run=run)


def _lock(c, prepared):
    try:
        return prepared.close.lock(
            c.manager,
            prepared.run["id"],
            CloseCommand(expected_version=prepared.run["version"], reason="本机竞争锁期"),
        )
    except AccountingError as error:
        return error


@contextmanager
def _pause_audit(monkeypatch, service, action):
    entered, release = Event(), Event()
    state = {}
    original = service._audit

    def paused(conn, actor, book_id, event, object_id, details):
        original(conn, actor, book_id, event, object_id, details)
        if event == action:
            state["pid"] = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test latch was not released")

    with monkeypatch.context() as patch:
        patch.setattr(service, "_audit", paused)
        try:
            yield entered, release, state
        finally:
            release.set()


def _ensure_entered(entered, future):
    if not entered.wait(8):
        if future.done():
            future.result()
        raise AssertionError("real service did not reach its transaction audit")


def test_pg_control_configuration_commit_invalidates_waiting_close(bank_case):
    bank = bank_case
    c = bank.c
    _review(bank, bank.sub.declare_bank_coverage(c.manager, bank.request))
    ready = _preflight(bank)
    with ThreadPoolExecutor(1) as pool:
        with _runtime_transaction(c, c.manager) as writer:
            writer.execute(
                "INSERT INTO subledger_control_accounts(id,firm_id,company_id,ledger_id,kind,"
                "control_account_id,control_account_version,required_aux_json,effective_from,"
                "active,created_by,created_at) VALUES(%s,%s,%s,%s,'receivable',%s,1,'[]','2026-10',TRUE,%s,CURRENT_TIMESTAMP::text)",
                (
                    uuid4().hex,
                    c.manager.firm_id,
                    c.book["company_id"],
                    bank.accounts["1002"]["ledger_id"],
                    bank.accounts["1002"]["id"],
                    c.manager.user_id,
                ),
            )
            pid = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            future = pool.submit(_lock, c, ready)
            _await_blocked(c, pid, future)
        result = future.result(15)
    assert isinstance(result, AccountingError) and result.status == 409
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT id FROM internal_period_locks WHERE book_id=?", (c.book["id"],)
            ).fetchall()
            == []
        )


def test_pg_coverage_review_does_not_reuse_failed_preflight(bank_case):
    bank = bank_case
    row = bank.sub.declare_bank_coverage(bank.c.manager, bank.request)
    failed = _preflight(bank, review=False)
    assert failed.run["status"] == "preflight_failed"
    _review(bank, row)
    outcome = _lock(bank.c, failed)
    assert isinstance(outcome, AccountingError) and outcome.status == 409
    ready = _preflight(bank)
    assert _lock(bank.c, ready)["close"]["status"] == "locked"


def test_pg_close_commit_rejects_waiting_new_coverage_and_native_insert(bank_case, monkeypatch):
    bank = bank_case
    c = bank.c
    row = _review(bank, bank.sub.declare_bank_coverage(c.manager, bank.request))
    ready = _preflight(bank)
    changed = bank.request.model_copy(update={"reason": "锁期之后试图提交新声明"})
    with _pause_audit(monkeypatch, ready.close, "close.lock") as (entered, release, state):
        with ThreadPoolExecutor(2) as pool:
            lock_future = pool.submit(_lock, c, ready)
            try:
                _ensure_entered(entered, lock_future)
                write_future = pool.submit(bank.sub.declare_bank_coverage, c.manager, changed)
                _await_blocked(c, state["pid"], write_future)
            finally:
                release.set()
            assert lock_future.result(15)["close"]["status"] == "locked"
            with pytest.raises(AccountingError) as blocked:
                write_future.result(15)
            assert blocked.value.status == 409
    with c.db.transaction(actor=c.manager) as conn:
        native = dict(
            conn.execute(
                "SELECT * FROM bank_statement_coverages WHERE id=?", (row["id"],)
            ).fetchone()
        )
    native.update(
        id=uuid4().hex,
        state="pending_review",
        version=1,
        verified_by=None,
        verified_at=None,
        review_reason=None,
        fingerprint="f" * 64,
    )
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "INSERT INTO bank_statement_coverages("
                + ",".join(native)
                + ") VALUES("
                + ",".join("%s" for _ in native)
                + ")",
                tuple(native.values()),
            )
    assert bank.sub.declare_bank_coverage(c.manager, bank.request)["id"] == row["id"]


def test_pg_revocation_after_original_read_stops_coverage_review(bank_case, monkeypatch):
    import app.subledger.bank_evidence as evidence

    bank = bank_case
    c = bank.c
    row = bank.sub.declare_bank_coverage(c.manager, bank.request)
    entered, release = Event(), Event()
    original = evidence._read_original

    def paused(service, source):
        original(service, source)
        entered.set()
        if not release.wait(10):
            raise TimeoutError("original-read latch not released")

    monkeypatch.setattr(evidence, "_read_original", paused)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(_review, bank, row)
        try:
            _ensure_entered(entered, future)
            with c.psycopg.connect(c.admin) as conn:
                conn.execute(
                    "UPDATE accounting.users SET active=0 WHERE id=%s", (c.reviewer.user_id,)
                )
        finally:
            release.set()
        with pytest.raises(AccountingError) as denied:
            future.result(15)
        assert denied.value.status in {403, 404}
    assert bank.sub.list_bank_coverages(c.manager, c.book["id"])[0]["state"] == "pending_review"


def test_pg_coverage_audit_failure_rolls_back_row_revision_and_audit(bank_case, monkeypatch):
    bank = bank_case
    c = bank.c
    with c.db.transaction(actor=c.manager) as conn:
        before = [
            tuple(row)
            for row in conn.execute(
                "SELECT scope_kind,scope_id,revision FROM close_dependency_revisions ORDER BY scope_kind,scope_id"
            )
        ]
    original = bank.sub._audit

    def fail_after_audit(*args):
        original(*args)
        raise RuntimeError("injected audit response failure")

    with monkeypatch.context() as patch:
        patch.setattr(bank.sub, "_audit", fail_after_audit)
        with pytest.raises(RuntimeError, match="injected"):
            bank.sub.declare_bank_coverage(c.manager, bank.request)
    assert bank.sub.list_bank_coverages(c.manager, c.book["id"]) == []
    with c.db.transaction(actor=c.manager) as conn:
        after = [
            tuple(row)
            for row in conn.execute(
                "SELECT scope_kind,scope_id,revision FROM close_dependency_revisions ORDER BY scope_kind,scope_id"
            )
        ]
        assert after == before
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE book_id=? AND action='bank.coverage.declare'",
                (c.book["id"],),
            ).fetchone()[0]
            == 0
        )
    assert bank.sub.declare_bank_coverage(c.manager, bank.request)["state"] == "pending_review"


def test_pg_parallel_coverage_declaration_and_lost_response_replay_once(bank_case):
    bank = bank_case
    c = bank.c
    barrier = Barrier(2)

    def declare():
        barrier.wait(8)
        return bank.sub.declare_bank_coverage(c.manager, bank.request)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: declare(), range(2)))
    assert results[0]["id"] == results[1]["id"]
    # Discarding the committed return value models loss after commit; retry is
    # required to recover that same declaration, never to write another row.
    replay = bank.sub.declare_bank_coverage(c.manager, bank.request)
    assert replay == results[0]
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM bank_statement_coverages WHERE book_id=?", (c.book["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM audit_events WHERE book_id=? AND action='bank.coverage.declare'",
                (c.book["id"],),
            ).fetchone()[0]
            == 1
        )


def test_pg_posted_fact_wins_against_waiting_not_applicable_declaration(
    declaration_case, monkeypatch
):
    c, sub = declaration_case
    accounts = _accounts(c)
    original_audit = PostingService._audit
    entered, release = Event(), Event()
    state = {}

    def paused(conn, actor, book_id, action, object_id, details):
        original_audit(conn, actor, book_id, action, object_id, details)
        if action == "posting.voucher.post":
            state["pid"] = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            entered.set()
            if not release.wait(10):
                raise TimeoutError("posting latch not released")

    monkeypatch.setattr(PostingService, "_audit", staticmethod(paused))
    request = ControlApplicability(
        book_id=c.book["id"],
        status="not_applicable",
        effective_from="2026-10",
        reason="不得跳过已发生应付事实",
    )
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(_post, c, accounts)
        try:
            _ensure_entered(entered, writer)
            declaration = pool.submit(sub.set_control_applicability, c.manager, request)
            _await_blocked(c, state["pid"], declaration)
        finally:
            release.set()
        writer.result(15)
        with pytest.raises(AccountingError) as denied:
            declaration.result(15)
        assert denied.value.status == 409
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT id FROM subledger_control_applicability WHERE ledger_id=?",
                (accounts["1002"]["ledger_id"],),
            ).fetchall()
            == []
        )


def test_pg_later_fact_invalidates_accepted_not_applicable_declaration(declaration_case):
    c, sub = declaration_case
    sub.set_control_applicability(
        c.manager,
        ControlApplicability(
            book_id=c.book["id"],
            status="not_applicable",
            effective_from="2026-10",
            reason="当时真实零余额无子账事实",
        ),
    )
    assert sub.control_reconciliation(c.manager, c.book["id"])["passed"] is True
    _post(c, _accounts(c))
    result = sub.control_reconciliation(c.manager, c.book["id"])
    assert result["status"] == "blocked" and result["passed"] is False


@pytest.fixture
def auxiliary_case(declaration_case, request):
    c, sub = declaration_case
    kind = getattr(request, "param", "payable")
    accounts = _accounts(c)
    ledger_id = accounts["1002"]["ledger_id"]
    control = OpeningService(c.db).create_account(
        c.manager,
        AccountCreate(
            ledger_id=ledger_id,
            code="C01AUX",
            name="合成分项目控制科目",
            category="asset" if kind in {"receivable", "advance_paid"} else "liability",
            normal_balance="debit" if kind in {"receivable", "advance_paid"} else "credit",
            auxiliary_requirements=["counterparty", "project"],
            effective_from="2026-01",
            reason="真实逐维度核验",
        ),
    )
    sub.configure_control_account(
        c.manager,
        ControlAccountCreate(
            book_id=c.book["id"],
            kind=kind,
            control_account_id=control["id"],
            control_account_version=1,
            required_aux=["counterparty", "project"],
            effective_from="2026-10",
            reason="合成应付分桶配置",
        ),
    )
    entities = LedgersService(c.db)
    party = entities.create_entity(
        c.manager, c.book["id"], EntityCreate(kind="counterparty", code="CP", name="合成往来方")
    )
    projects = [
        entities.create_entity(
            c.manager, c.book["id"], EntityCreate(kind="project", code=code, name="合成" + code)
        )
        for code in ("PA", "PB")
    ]
    item = sub.create_item(
        c.manager,
        OpenItemCreate(
            book_id=c.book["id"],
            kind=kind,
            counterparty_id=party["id"],
            control_account_id=control["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="10.00",
            source_kind="manual",
            aux={"counterparty": party["id"], "project": projects[1]["id"]},
            reason="合成项目B未结应付",
        ),
    )
    return SimpleNamespace(
        c=c,
        sub=sub,
        accounts=accounts,
        control=control,
        party=party,
        projects=projects,
        item=item,
        kind=kind,
    )


def _aux_voucher(a, *, project=1, payment=True):
    aux = {"counterparty": a.party["id"], "project": a.projects[project]["id"]}
    control_debit = payment == (a.kind in {"payable", "advance_received"})
    return PostingService(a.c.db).post_manual(
        a.c.manager,
        ManualVoucher(
            book_id=a.c.book["id"],
            command_key="c01-aux-voucher-" + uuid4().hex,
            voucher_date="2026-10-06",
            description="合成项目凭证",
            lines=[
                ManualLine(
                    account_id=a.control["id"],
                    account_version=1,
                    auxiliary_values=aux,
                    debit="10.00" if control_debit else "0.00",
                    credit="0.00" if control_debit else "10.00",
                ),
                ManualLine(
                    account_id=a.accounts["1002"]["id"],
                    account_version=1,
                    debit="0.00" if control_debit else "10.00",
                    credit="10.00" if control_debit else "0.00",
                ),
            ],
        ),
    )


@pytest.mark.parametrize(
    "auxiliary_case", ["receivable", "payable", "advance_received", "advance_paid"], indirect=True
)
def test_pg_each_subledger_kind_uses_correct_settlement_direction(auxiliary_case):
    a = auxiliary_case
    opposite = _aux_voucher(a, payment=False)
    with pytest.raises(AccountingError):
        a.sub.allocate(
            a.c.manager,
            AllocationCreate(
                book_id=a.c.book["id"],
                voucher_id=opposite["voucher"]["id"],
                parts=[AllocationPart(item_id=a.item["id"], amount="10.00")],
                idempotency_key="c01-wrong-direction-" + uuid4().hex,
                reason="反向发生额不能核销事项",
            ),
        )
    proper = _aux_voucher(a)
    a.sub.allocate(
        a.c.manager,
        AllocationCreate(
            book_id=a.c.book["id"],
            voucher_id=proper["voucher"]["id"],
            parts=[AllocationPart(item_id=a.item["id"], amount="10.00")],
            idempotency_key="c01-right-direction-" + uuid4().hex,
            reason="正确方向的合成核销",
        ),
    )
    assert a.sub.list_items(a.c.manager, a.c.book["id"])[0]["remaining_cents"] == 0


def test_pg_equal_total_cannot_hide_wrong_auxiliary_bucket(auxiliary_case):
    a = auxiliary_case
    _aux_voucher(a, project=0, payment=False)
    result = a.sub.control_reconciliation(a.c.manager, a.c.book["id"])
    assert result["passed"] is False, "总额相等不能掩盖项目A总账与项目B子账错配"


def test_pg_allocation_rejects_voucher_auxiliary_mismatch(auxiliary_case):
    a = auxiliary_case
    voucher = _aux_voucher(a, project=0)
    with pytest.raises(AccountingError):
        a.sub.allocate(
            a.c.manager,
            AllocationCreate(
                book_id=a.c.book["id"],
                voucher_id=voucher["voucher"]["id"],
                parts=[AllocationPart(item_id=a.item["id"], amount="10.00")],
                idempotency_key="c01-wrong-aux-" + uuid4().hex,
                reason="项目A付款不能核销项目B事项",
            ),
        )
    assert a.sub.list_items(a.c.manager, a.c.book["id"])[0]["remaining_cents"] == 1000


def _native_allocate(a, voucher, *, amount=1000, other_company=False):
    c = a.c
    target = c.other_book if other_company else c.book
    with _runtime_transaction(c, c.manager) as conn:
        conn.execute(
            "INSERT INTO subledger_allocations(id,firm_id,company_id,book_id,item_id,voucher_id,amount_cents,allocated_on,idempotency_key,request_fingerprint,created_by,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'2026-10-06',%s,%s,%s,CURRENT_TIMESTAMP::text)",
            (
                uuid4().hex,
                c.manager.firm_id,
                target["company_id"],
                target["id"],
                a.item["id"],
                voucher["voucher"]["id"],
                amount,
                "c01-native-" + uuid4().hex,
                "f" * 64,
                c.manager.user_id,
            ),
        )


@pytest.mark.parametrize("violation", ["other_company", "over_amount", "wrong_aux"])
def test_pg_native_allocation_rejects_scope_amount_or_auxiliary_bypass(auxiliary_case, violation):
    a = auxiliary_case
    voucher = _aux_voucher(a, project=0 if violation == "wrong_aux" else 1)
    with pytest.raises(a.c.psycopg.Error) as denied:
        _native_allocate(
            a,
            voucher,
            amount=1001 if violation == "over_amount" else 1000,
            other_company=violation == "other_company",
        )
    assert denied.value.sqlstate in {"23514", "42501", "23503"}
    with a.c.db.transaction(actor=a.c.manager) as conn:
        assert (
            conn.execute(
                "SELECT id FROM subledger_allocations WHERE item_id=?", (a.item["id"],)
            ).fetchall()
            == []
        )


def test_pg_native_two_payments_cannot_overallocate_one_item(auxiliary_case):
    a = auxiliary_case
    vouchers = [_aux_voucher(a), _aux_voucher(a)]
    barrier = Barrier(2)

    def allocate(voucher):
        barrier.wait(8)
        try:
            _native_allocate(a, voucher, amount=700)
            return "accepted"
        except a.c.psycopg.Error as error:
            return error.sqlstate

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(allocate, vouchers))
    assert outcomes.count("accepted") == 1, outcomes
    assert len([item for item in outcomes if item in {"23514", "42501"}]) == 1, outcomes
    with a.c.db.transaction(actor=a.c.manager) as conn:
        assert (
            conn.execute(
                "SELECT SUM(amount_cents) FROM subledger_allocations WHERE item_id=?",
                (a.item["id"],),
            ).fetchone()[0]
            == 700
        )


def test_pg_reversal_commit_allows_waiting_reallocation_once(auxiliary_case, monkeypatch):
    a = auxiliary_case
    c = a.c
    vouchers = [_aux_voucher(a), _aux_voucher(a)]
    _native_allocate(a, vouchers[0])
    with c.db.transaction(actor=c.manager) as conn:
        allocation = conn.execute(
            "SELECT id FROM subledger_allocations WHERE item_id=?", (a.item["id"],)
        ).fetchone()[0]
    request = AllocationReverse(
        idempotency_key="c01-reverse-race-" + uuid4().hex, reason="撤销后允许重新正确核销"
    )
    with _pause_audit(monkeypatch, a.sub, "subledger.allocate.reverse") as (
        entered,
        release,
        state,
    ):
        with ThreadPoolExecutor(2) as pool:
            reversal = pool.submit(a.sub.reverse_allocation, c.manager, allocation, request)
            assert entered.wait(8)
            reallocation = pool.submit(_native_allocate, a, vouchers[1])
            try:
                _await_blocked(c, state["pid"], reallocation)
            finally:
                release.set()
            assert reversal.result(15)["reversed"] is True
            reallocation.result(15)
    a.sub.reverse_allocation(c.manager, allocation, request)
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM subledger_allocation_reversals WHERE allocation_id=?",
                (allocation,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT SUM(a.amount_cents) FROM subledger_allocations a WHERE a.item_id=? AND NOT EXISTS(SELECT 1 FROM subledger_allocation_reversals r WHERE r.allocation_id=a.id)",
                (a.item["id"],),
            ).fetchone()[0]
            == 1000
        )
    assert a.sub.list_items(c.manager, c.book["id"])[0]["remaining_cents"] == 0


def test_pg_unresolved_account_adjustments_cannot_cancel_each_other(bank_case):
    bank = bank_case
    c, sub = bank.c, bank.sub
    _review(bank, sub.declare_bank_coverage(c.manager, bank.request))
    assert sub.bank_reconciliation(c.manager, c.book["id"])["passed"] is True
    with _runtime_transaction(c, c.manager) as conn:
        for amount in (100, -100):
            conn.execute(
                "INSERT INTO bank_recon_adjustments(id,book_id,period,kind,amount_cents,explanation,status,created_by,created_at,bank_account_id) VALUES(%s,%s,'2026-10','error',%s,'同账户待解释调节项','open',%s,CURRENT_TIMESTAMP::text,%s)",
                (uuid4().hex, c.book["id"], amount, c.manager.user_id, bank.request.account_id),
            )
    result = sub.bank_reconciliation(c.manager, c.book["id"])
    assert result["passed"] is False, "两笔仍未解决的调节项不能因正负抵消而通过"


def test_pg_new_adjustment_cannot_omit_account_to_pose_as_legacy(declaration_case):
    c, _ = declaration_case
    with pytest.raises(c.psycopg.Error) as denied:
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "INSERT INTO bank_recon_adjustments(id,book_id,period,kind,amount_cents,explanation,status,created_by,created_at) VALUES(%s,%s,'2026-10','error',100,'新数据不得伪装历史无账户项','open',%s,CURRENT_TIMESTAMP::text)",
                (uuid4().hex, c.book["id"], c.manager.user_id),
            )
    assert denied.value.sqlstate in {"23514", "42501"}


def test_pg_native_reversal_cannot_relabel_allocation_company(auxiliary_case):
    a = auxiliary_case
    c = a.c
    voucher = _aux_voucher(a)
    _native_allocate(a, voucher)
    with c.db.transaction(actor=c.manager) as conn:
        allocation_id = conn.execute(
            "SELECT id FROM subledger_allocations WHERE item_id=?", (a.item["id"],)
        ).fetchone()[0]
    with pytest.raises(c.psycopg.Error) as denied:
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "INSERT INTO subledger_allocation_reversals(id,firm_id,company_id,book_id,allocation_id,reason,idempotency_key,request_fingerprint,created_by,created_at) VALUES(%s,%s,%s,%s,%s,'不得伪造撤销归属',%s,%s,%s,CURRENT_TIMESTAMP::text)",
                (
                    uuid4().hex,
                    c.manager.firm_id,
                    c.other_book["company_id"],
                    c.other_book["id"],
                    allocation_id,
                    "c01-reversal-" + uuid4().hex,
                    "f" * 64,
                    c.manager.user_id,
                ),
            )
    assert denied.value.sqlstate in {"23514", "42501", "23503"}
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT id FROM subledger_allocation_reversals WHERE allocation_id=?",
                (allocation_id,),
            ).fetchall()
            == []
        )


def test_pg_bank_mapping_cannot_count_one_general_ledger_account_twice(bank_case):
    bank = bank_case
    with pytest.raises(AccountingError):
        bank.sub.create_bank_account(
            bank.c.manager,
            BankAccountCreate(
                book_id=bank.c.book["id"],
                name="不得重复映射的第二个账户",
                ledger_account_id=bank.accounts["1002"]["id"],
                ledger_account_version=1,
                reason="同一总账余额不能证明两个独立银行账户",
            ),
        )


def test_pg_concurrent_configuration_has_one_effective_version(bank_case):
    bank = bank_case
    request = ControlAccountCreate(
        book_id=bank.c.book["id"],
        kind="receivable",
        control_account_id=bank.accounts["1002"]["id"],
        control_account_version=1,
        effective_from="2026-10",
        reason="并发创建同一期控制配置",
    )
    barrier = Barrier(2)

    def create():
        barrier.wait(8)
        try:
            return bank.sub.configure_control_account(bank.c.manager, request)["id"]
        except AccountingError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert (
        results.count(409) == 1 and len([value for value in results if isinstance(value, str)]) == 1
    )
    with bank.c.db.transaction(actor=bank.c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM subledger_control_accounts WHERE ledger_id=? AND kind='receivable' AND active IS TRUE",
                (bank.accounts["1002"]["ledger_id"],),
            ).fetchone()[0]
            == 1
        )


@pytest.fixture
def bridge_pg(coverage_pg, monkeypatch, request):
    import test_c01_period_bridge as bridge_checks

    reviewer = coverage_pg.pg.person(firm_role="admin")
    # Only adapt a test identity supplier; all authorization and opening services
    # still run against PostgreSQL using this independent real identity.
    monkeypatch.setattr(bridge_checks, "admin", lambda _: reviewer)
    return bridge_checks.prepare_bridge(coverage_pg, delta=getattr(request, "param", 0))


def test_pg_two_period_bridge_carries_residual_and_preserves_bank_identity(bridge_pg):
    x = bridge_pg
    assert x.sub.continuity(x.c.manager, x.target)["status"] == "bridge_required"
    first = x.sub.create_period_bridge(x.c.manager, x.request)
    assert first["status"] == "complete"
    assert [row["remaining_cents"] for row in first["items"]] == [999970]
    assert x.sub.create_period_bridge(x.c.manager, x.request) == first
    assert x.sub.control_reconciliation(x.c.manager, x.target)["passed"] is True
    assert x.sub.continuity(x.c.manager, x.target)["status"] == "bridged"
    with x.c.db.transaction(actor=x.c.manager) as conn:
        stable = conn.execute(
            "SELECT book_id,stable_bank_id FROM bank_identity_bindings WHERE book_id IN(?,?)",
            (x.c.book["id"], x.target),
        ).fetchall()
        assert len(stable) == 2 and len({row["stable_bank_id"] for row in stable}) == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM vouchers WHERE book_id=?", (x.target,)).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("bridge_pg", [1], indirect=True)
def test_pg_one_cent_wrong_next_opening_blocks_bridge_atomically(bridge_pg):
    x = bridge_pg
    with pytest.raises(AccountingError):
        x.sub.create_period_bridge(x.c.manager, x.request)
    assert x.sub.list_items(x.c.manager, x.target) == []
    with x.c.db.transaction(actor=x.c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM subledger_period_bridges WHERE book_id=?", (x.target,)
            ).fetchone()[0]
            == 0
        )


def test_pg_concurrent_period_bridge_imports_each_residual_once(bridge_pg):
    x = bridge_pg
    barrier = Barrier(2)

    def create():
        barrier.wait(8)
        return x.sub.create_period_bridge(x.c.manager, x.request)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert results[0] == results[1]
    assert len(x.sub.list_items(x.c.manager, x.target)) == 1


def test_pg_bank_binding_cannot_reference_other_company_stable_identity(bank_case):
    from app.subledger.models import BankIdentityCreate

    bank = bank_case
    c = bank.c
    foreign = bank.sub.create_bank_identity(
        c.manager,
        BankIdentityCreate(
            book_id=c.other_book["id"],
            name="其他企业真实长期银行账户",
            reason="验证账户跨企业引用拒绝",
        ),
    )
    with pytest.raises(c.psycopg.Error) as denied:
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "INSERT INTO bank_identity_bindings(id,book_id,firm_id,company_id,ledger_id,bank_account_id,stable_bank_id,idempotency_key,request_fingerprint,reason,created_by,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'拒绝跨企业银行身份',%s,CURRENT_TIMESTAMP::text)",
                (
                    uuid4().hex,
                    c.book["id"],
                    c.manager.firm_id,
                    c.book["company_id"],
                    bank.accounts["1002"]["ledger_id"],
                    bank.request.account_id,
                    foreign["id"],
                    "c01-foreign-bank-" + uuid4().hex,
                    "f" * 64,
                    c.manager.user_id,
                ),
            )
    assert denied.value.sqlstate in {"23514", "42501"}


def test_pg_two_bank_differences_cannot_cancel_at_total_level(bank_case):
    bank = bank_case
    c = bank.c
    _review(bank, bank.sub.declare_bank_coverage(c.manager, bank.request))
    ledger_account = OpeningService(c.db).create_account(
        c.manager,
        AccountCreate(
            ledger_id=bank.accounts["1002"]["ledger_id"],
            code="100201",
            name="合成第二银行账户",
            category="asset",
            normal_balance="debit",
            effective_from="2026-01",
            reason="逐银行余额不能抵消",
        ),
    )
    second = bank.sub.create_bank_account(
        c.manager,
        BankAccountCreate(
            book_id=c.book["id"],
            name="合成第二账户",
            ledger_account_id=ledger_account["id"],
            ledger_account_version=1,
            reason="每个账户单独核验",
        ),
    )
    wizard = ImportWizardService(c.db, c.store)
    source = wizard.upload(
        c.manager,
        c.book["id"],
        "第二银行.csv",
        (
            "编号,日期,摘要,对方,金额,方向,凭据\n"
            "BI1,2026-10-05,第二行收款,合成公司,0.30,收入,B原件1\n"
            "BO1,2026-10-05,第二行付款,合成公司,0.30,支出,B原件2\n"
        ).encode(),
    )
    preview = wizard.preview(
        c.manager,
        c.book["id"],
        PreviewRequest(
            file_id=source["file_id"],
            namespace="c01-bank-b",
            bank_account=second["id"],
            mapping=MappingSettings(
                columns={
                    "external_id": 0,
                    "date": 1,
                    "description": 2,
                    "counterparty": 3,
                    "amount": 4,
                    "direction": 5,
                    "evidence_ref": 6,
                }
            ),
        ),
    )
    wizard.confirm(c.manager, c.book["id"], preview["id"])
    request = bank.request.model_copy(
        update={
            "account_id": second["id"],
            "source_file_id": source["file_id"],
            "source_preview_id": preview["id"],
            "source_sha256": source["sha256"],
        }
    )
    for income in (True, False):
        voucher = _post(c, {**bank.accounts, "1002": ledger_account}, income)
        formal = next(row for row in voucher["lines"] if row["account_id"] == ledger_account["id"])
        statement = bank.sub.add_bank_line(
            c.manager,
            BankLineCreate(
                book_id=c.book["id"],
                bank_account_id=second["id"],
                line_date="2026-10-05",
                description="第二银行原件对应逐笔流水",
                amount="0.30" if income else "-0.30",
            ),
        )
        bank.sub.match_bank_line(
            c.manager, statement["id"], BankMatch(voucher_line_id=formal["id"])
        )
    _review(bank, bank.sub.declare_bank_coverage(c.manager, request))
    assert bank.sub.bank_reconciliation(c.manager, c.book["id"])["passed"] is True
    PostingService(c.db).post_manual(
        c.manager,
        ManualVoucher(
            book_id=c.book["id"],
            voucher_date="2026-10-06",
            command_key="c01-bank-transfer-" + uuid4().hex,
            description="账面跨银行调拨尚未核对至对应流水",
            lines=[
                ManualLine(
                    account_id=bank.accounts["1002"]["id"], account_version=1, credit="0.30"
                ),
                ManualLine(account_id=ledger_account["id"], account_version=1, debit="0.30"),
            ],
        ),
    )
    result = bank.sub.bank_reconciliation(c.manager, c.book["id"])
    assert result["difference_cents"] == 0
    assert sorted(row["book_difference_cents"] for row in result["rows"]) == [-30, 30]
    assert result["passed"] is False


def test_pg_native_bank_match_requires_its_own_account_voucher_line(bank_case):
    bank = bank_case
    c = bank.c
    voucher = _post(c, bank.accounts)
    wrong_line = next(row for row in voucher["lines"] if row["code_snapshot"] == "2202")
    row = bank.sub.add_bank_line(
        c.manager,
        BankLineCreate(
            book_id=c.book["id"],
            bank_account_id=bank.request.account_id,
            line_date="2026-10-05",
            description="不能把银行流水匹配应付分录",
            amount="-0.30",
        ),
    )
    with pytest.raises(AccountingError):
        bank.sub.match_bank_line(c.manager, row["id"], BankMatch(voucher_line_id=wrong_line["id"]))
    with pytest.raises(c.psycopg.Error) as denied:
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "UPDATE bank_statement_lines SET status='matched',matched_voucher_line_id=%s WHERE id=%s",
                (wrong_line["id"], row["id"]),
            )
    assert denied.value.sqlstate in {"23514", "42501"}
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT status FROM bank_statement_lines WHERE id=?", (row["id"],)
            ).fetchone()[0]
            == "unmatched"
        )


def test_pg_resolved_bank_evidence_cannot_be_rewritten(bank_case):
    from app.subledger.models import BankAdjustment, BankAdjustmentResolve

    bank = bank_case
    c = bank.c
    with c.db.transaction(actor=c.manager) as conn:
        line = dict(
            conn.execute(
                "SELECT * FROM bank_statement_lines WHERE book_id=? AND amount_cents=30",
                (c.book["id"],),
            ).fetchone()
        )
    adjustment = bank.sub.add_bank_adjustment(
        c.manager,
        BankAdjustment(
            book_id=c.book["id"],
            bank_account_id=bank.request.account_id,
            period="2026-10",
            kind="error",
            amount="0.30",
            explanation="必须冻结独立解决后的真实证据",
        ),
    )
    bank.sub.resolve_bank_adjustment(
        c.reviewer,
        adjustment["id"],
        BankAdjustmentResolve(
            bank_line_id=line["id"],
            reason="独立核验本账户正式收款凭证",
        ),
    )
    with pytest.raises(c.psycopg.Error) as denied:
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "UPDATE bank_statement_lines SET description='不得改写已解决证据' WHERE id=%s",
                (line["id"],),
            )
    assert denied.value.sqlstate in {"23514", "42501"}
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT description FROM bank_statement_lines WHERE id=?", (line["id"],)
            ).fetchone()[0]
            == line["description"]
        )


def test_pg_approved_stable_bank_mapping_cannot_drift(bank_case):
    from app.subledger.models import BankIdentityCreate, BankBindingCreate, BankBindingReview

    bank = bank_case
    c = bank.c
    stable = bank.sub.create_bank_identity(
        c.manager,
        BankIdentityCreate(
            book_id=c.book["id"], name="已核准银行身份", reason="冻结核准后的科目映射"
        ),
    )
    binding = bank.sub.bind_bank_identity(
        c.manager,
        BankBindingCreate(
            book_id=c.book["id"],
            bank_account_id=bank.request.account_id,
            stable_bank_id=stable["id"],
            idempotency_key="c01-stable-map-" + uuid4().hex,
            reason="独立复核前的真实映射",
        ),
    )
    bank.sub.review_bank_binding(
        c.reviewer,
        binding["id"],
        BankBindingReview(decision="approved", reason="独立确认该银行总账归属"),
    )
    other = OpeningService(c.db).create_account(
        c.manager,
        AccountCreate(
            ledger_id=bank.accounts["1002"]["ledger_id"],
            code="100202",
            name="其他银行科目",
            category="asset",
            normal_balance="debit",
            effective_from="2026-01",
            reason="已核准银行不能改绑其他科目",
        ),
    )
    with pytest.raises(c.psycopg.Error) as denied:
        with _runtime_transaction(c, c.manager) as conn:
            conn.execute(
                "UPDATE bank_accounts SET ledger_account_id=%s WHERE id=%s",
                (other["id"], bank.request.account_id),
            )
    assert denied.value.sqlstate in {"23514", "42501"}
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT ledger_account_id FROM bank_accounts WHERE id=?", (bank.request.account_id,)
            ).fetchone()[0]
            == bank.accounts["1002"]["id"]
        )


def test_pg_future_original_date_cannot_enter_current_period(auxiliary_case):
    a = auxiliary_case
    with pytest.raises(AccountingError):
        a.sub.create_item(
            a.c.manager,
            OpenItemCreate(
                book_id=a.c.book["id"],
                kind=a.kind,
                counterparty_id=a.party["id"],
                control_account_id=a.control["id"],
                control_account_version=1,
                original_date="2026-11-01",
                amount="10.00",
                source_kind="manual",
                aux={"counterparty": a.party["id"], "project": a.projects[1]["id"]},
                reason="十一月事实不能填入十月事项",
            ),
        )


def test_pg_historical_future_item_cannot_make_current_control_balance_pass(auxiliary_case):
    a = auxiliary_case
    with a.c.db.transaction(actor=a.c.manager) as conn:
        future = dict(
            conn.execute("SELECT * FROM subledger_items WHERE id=?", (a.item["id"],)).fetchone()
        )
    future.update(id=uuid4().hex, original_date="2026-11-01")
    with _runtime_transaction(a.c, a.c.manager) as conn:
        conn.execute(
            "INSERT INTO subledger_items("
            + ",".join(future)
            + ") VALUES("
            + ",".join("%s" for _ in future)
            + ")",
            tuple(future.values()),
        )
    _aux_voucher(a, payment=False)
    _aux_voucher(a, payment=False)
    result = a.sub.control_reconciliation(a.c.manager, a.c.book["id"])
    assert result["passed"] is False
    assert result["status"] == "blocked"
