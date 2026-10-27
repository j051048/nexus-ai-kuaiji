"""F03 local negative regressions: real writes, restored values and isolation.

SQLite validates domain behavior; PostgreSQL lock races are separately covered
in test_p0_close_upload_postgres.py and must never be inferred from this suite.
"""

from review_policy_fixtures import post_reviewed_manual

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest

from app.accounting.close_dependencies import close_dependencies
from app.accounting.models import ExportRequest
from app.accounting.domain import AccountingError
from app.b04_close.models import CloseCommand, ClosePreflight
from app.b04_close.service import CloseService
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.opening.models import AccountCreate
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.subledger.models import (
    AllocationCreate,
    AllocationPart,
    AllocationReverse,
    BankAdjustment,
    BankLineCreate,
    BankMatch,
    ControlAccountCreate,
    OpenItemCreate,
)
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03
from c01_bank_fixture import verified_bank_account, reviewed_export_draft
from test_business import business
from test_posting_service import admin


@pytest.fixture
def close_case(business):
    case = business
    opening, posting = setup_b03(case)
    reviewer = admin(case)
    sub = SubledgerService(case.db)
    payment = post_reviewed_manual(
        posting,
        case.manager,
        ManualVoucher(
            book_id=case.book["id"],
            voucher_date="2026-10-20",
            command_key="p0-clear-opening",
            description="虚构全额清偿期初应付",
            lines=[
                ManualLine(
                    account_id=opening["payable"]["id"],
                    account_version=1,
                    debit="10000.00",
                    credit="0.00",
                ),
                ManualLine(
                    account_id=opening["bank"]["id"],
                    account_version=1,
                    debit="0.00",
                    credit="10000.00",
                ),
            ],
        ),
    )
    party = LedgersService(case.db).create_entity(
        case.manager,
        case.book["id"],
        EntityCreate(
            kind="counterparty",
            code="P0-SUPPLIER",
            name="虚构期初供应商",
        ),
    )
    sub.configure_control_account(
        case.manager,
        ControlAccountCreate(
            book_id=case.book["id"],
            kind="payable",
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            effective_from="2026-01",
            reason="P0 控制科目夹具",
        ),
    )
    item = sub.create_item(
        case.manager,
        OpenItemCreate(
            book_id=case.book["id"],
            kind="payable",
            counterparty_id=party["id"],
            control_account_id=opening["payable"]["id"],
            control_account_version=1,
            original_date="2026-10-01",
            amount="10000.00",
            source_kind="opening",
            reason="虚构期初子账",
        ),
    )
    allocation = sub.allocate(
        case.manager,
        AllocationCreate(
            book_id=case.book["id"],
            voucher_id=payment["voucher"]["id"],
            parts=[AllocationPart(item_id=item["id"], amount="10000.00")],
            idempotency_key="p0-opening-allocation",
            reason="虚构期初清偿核销",
        ),
    )
    bank_account = verified_bank_account(
        case, sub, opening["bank"]["id"], "10000.00", "0.00", [("2026-10-20", "-10000.00")]
    )
    bank = sub.add_bank_line(
        case.manager,
        BankLineCreate(
            book_id=case.book["id"],
            bank_account_id=bank_account["id"],
            line_date="2026-10-20",
            amount="-10000.00",
            description="虚构期初清偿流水",
        ),
    )
    bank_line = next(row for row in payment["lines"] if row["account_id"] == opening["bank"]["id"])
    sub.match_bank_line(case.manager, bank["id"], BankMatch(voucher_line_id=bank_line["id"]))
    reports = ReportService(case.db)
    report = reports.create(case.manager, case.book["id"], "2026-10")
    reports.validate(case.manager, report["report"]["id"])
    report = reports.review(
        reviewer,
        report["report"]["id"],
        ReportCommand(
            expected_version=1,
            reason="独立复核虚构报表",
        ),
    )
    close = CloseService(case.db)
    run = close.preflight(
        case.manager,
        ClosePreflight(
            book_id=case.book["id"],
            period="2026-10",
            report_id=report["report"]["id"],
            idempotency_key="p0-close-preflight",
            reason="虚构月结预检",
        ),
    )
    assert run["close"]["status"] == "preflight_passed", run["checks"]
    case.close = close
    case.run = run
    case.reviewer = reviewer
    case.opening = opening
    case.sub = sub
    case.payment = payment
    case.item = item
    case.allocation = allocation["allocations"][0]
    case.bank_account = bank_account
    case.bank = bank
    case.report = report
    return case


def command():
    return CloseCommand(expected_version=1, reason="独立月结复核锁期")


def mutate(case, kind):
    book_id = case.book["id"]
    if kind == "bank_adjustment":
        case.sub.add_bank_adjustment(
            case.manager,
            BankAdjustment(
                book_id=book_id,
                bank_account_id=case.bank_account["id"],
                period="2026-10",
                kind="error",
                amount="1.00",
                explanation="虚构银行调节差异",
            ),
        )
    elif kind == "allocation_aba":
        case.sub.reverse_allocation(
            case.manager,
            case.allocation["id"],
            AllocationReverse(
                idempotency_key="p0-reverse-allocation",
                reason="虚构重新核销",
            ),
        )
        case.sub.allocate(
            case.manager,
            AllocationCreate(
                book_id=book_id,
                voucher_id=case.payment["voucher"]["id"],
                parts=[AllocationPart(item_id=case.item["id"], amount="10000.00")],
                idempotency_key="p0-reallocate-opening",
                reason="虚构重新核销",
            ),
        )
    elif kind == "new_account":
        OpeningService(case.db).create_account(
            case.manager,
            AccountCreate(
                ledger_id=case.opening["ledger_id"],
                code="1601",
                name="虚构设备",
                category="asset",
                normal_balance="debit",
                effective_from="2026-01",
                effective_to="2026-12",
                reason="虚构科目配置调整",
            ),
        )
    elif kind == "periodic_rule":
        from app.periodic.models import AccrualRule
        from app.periodic.service import PeriodicService

        PeriodicService(case.db).create_accrual_rule(
            case.manager,
            AccrualRule(
                book_id=book_id,
                name="虚构新增计提规则",
                amount="1.00",
                debit_account_id=case.opening["expense"]["id"],
                debit_account_version=1,
                credit_account_id=case.opening["payable"]["id"],
                credit_account_version=1,
                reason="虚构期末计提规则变化",
            ),
        )
    elif kind == "account_version_aba":
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "INSERT INTO ledger_account_versions(id,account_id,ledger_id,firm_id,company_id,version,code,name,parent_account_id,level,is_leaf,category,normal_balance,auxiliary_requirements,effective_from,effective_to,status,reason,created_by,created_at) "
                "SELECT ?,account_id,ledger_id,firm_id,company_id,2,code,name,parent_account_id,level,is_leaf,category,normal_balance,auxiliary_requirements,effective_from,effective_to,status,reason,created_by,created_at FROM ledger_account_versions WHERE account_id=? AND version=1",
                (uuid4().hex, case.opening["bank"]["id"]),
            )
            conn.execute(
                "UPDATE ledger_accounts SET current_version=2 WHERE id=?",
                (case.opening["bank"]["id"],),
            )
            conn.execute(
                "UPDATE ledger_accounts SET current_version=1 WHERE id=?",
                (case.opening["bank"]["id"],),
            )
    elif kind == "opening_version_aba":
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "INSERT INTO opening_package_versions(id,package_id,version,policy_version,config_fingerprint,status,request_key,request_fingerprint,created_by,created_at) "
                "SELECT ?,package_id,2,policy_version,config_fingerprint,'draft','p0-next-opening',request_fingerprint,created_by,created_at FROM opening_package_versions WHERE id=?",
                (uuid4().hex, case.opening["version_id"]),
            )
            conn.execute(
                "UPDATE opening_packages SET current_version=2 WHERE ledger_id=?",
                (case.opening["ledger_id"],),
            )
            conn.execute(
                "UPDATE opening_packages SET current_version=1 WHERE ledger_id=?",
                (case.opening["ledger_id"],),
            )
    elif kind == "posting":
        from app.posting.service import PostingService

        post_reviewed_manual(
            PostingService(case.db),
            case.manager,
            ManualVoucher(
                book_id=book_id,
                voucher_date="2026-10-21",
                command_key="p0-late-posting",
                description="虚构晚到正式凭证",
                lines=[
                    ManualLine(
                        account_id=case.opening["bank"]["id"],
                        account_version=1,
                        debit="1.00",
                        credit="0.00",
                    ),
                    ManualLine(
                        account_id=case.opening["payable"]["id"],
                        account_version=1,
                        debit="0.00",
                        credit="1.00",
                    ),
                ],
            ),
        )
    else:
        # Each edit returns to its original business value in the SAME committed
        # transaction. A content hash alone therefore cannot detect this history.
        targets = {
            "bank_match_aba": (
                "bank_statement_lines",
                "status",
                "unmatched",
                "id",
                case.bank["id"],
            ),
            "policy_aba": (
                "accounting_policies",
                "industry",
                "虚构行业变更",
                "ledger_id",
                case.opening["ledger_id"],
            ),
            "template_definition_aba": (
                "report_templates",
                "definition_json",
                "{}",
                "id",
                ReportService.TEMPLATE_ID,
            ),
            "template_version_aba": (
                "report_templates",
                "version",
                2,
                "id",
                ReportService.TEMPLATE_ID,
            ),
            "opening_version_aba": (
                "opening_packages",
                "current_version",
                2,
                "ledger_id",
                case.opening["ledger_id"],
            ),
            "account_version_aba": (
                "ledger_accounts",
                "status",
                "inactive",
                "id",
                case.opening["bank"]["id"],
            ),
        }
        table, column, replacement, key, identity = targets[kind]
        with case.db.transaction(write=True, actor=case.manager) as conn:
            original = conn.execute(
                f"SELECT {column} FROM {table} WHERE {key}=?", (identity,)
            ).fetchone()[0]
            conn.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (replacement, identity))
            conn.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (original, identity))


@pytest.mark.parametrize("stage", ["review", "lock"])
@pytest.mark.parametrize(
    "kind",
    [
        "bank_adjustment",
        "bank_match_aba",
        "allocation_aba",
        "new_account",
        "posting",
        "periodic_rule",
        "policy_aba",
        "template_definition_aba",
        "template_version_aba",
        "opening_version_aba",
        "account_version_aba",
    ],
)
def test_each_dependency_change_invalidates_frozen_preflight(close_case, stage, kind):
    case = close_case
    if stage == "lock":
        case.close.review(case.reviewer, case.run["close"]["id"], command())
    mutate(case, kind)
    with pytest.raises(AccountingError, match="预检已变化") as rejected:
        getattr(case.close, stage)(case.reviewer, case.run["close"]["id"], command())
    assert rejected.value.status == 409
    with case.db.transaction(actor=case.manager) as conn:
        assert not conn.execute(
            "SELECT 1 FROM internal_period_locks WHERE book_id=?", (case.book["id"],)
        ).fetchone()


def test_other_company_change_does_not_invalidate_this_close(close_case):
    case = close_case
    with case.db.transaction(actor=case.manager) as conn:
        before = close_dependencies(conn, case.book["id"], ReportService.TEMPLATE_ID)
    case.sub.add_bank_line(
        case.manager,
        BankLineCreate(
            book_id=case.other_book["id"],
            line_date="2026-10-20",
            amount="1.00",
            description="另一企业的虚构流水",
        ),
    )
    with case.db.transaction(actor=case.manager) as conn:
        assert close_dependencies(conn, case.book["id"], ReportService.TEMPLATE_ID) == before
    case.close.review(case.reviewer, case.run["close"]["id"], command())
    assert (
        case.close.lock(case.reviewer, case.run["close"]["id"], command())["close"]["status"]
        == "locked"
    )


def test_direct_stale_status_transition_and_lock_insert_are_rejected(close_case):
    case = close_case
    mutate(case, "bank_match_aba")
    with pytest.raises(sqlite3.IntegrityError, match="dependencies changed"):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            conn.execute(
                "UPDATE internal_close_runs SET status='reviewed',reviewed_by=?,reviewed_at='2026-10-31T00:00:00Z' WHERE id=?",
                (case.reviewer.user_id, case.run["close"]["id"]),
            )
    with pytest.raises(sqlite3.IntegrityError, match="reviewed close dependencies"):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            conn.execute(
                "INSERT INTO internal_period_locks(id,firm_id,company_id,ledger_id,book_id,period,close_run_id,locked_by,locked_at) VALUES('bad-lock',?,?,?,?,?,?,?,'now')",
                (
                    case.manager.firm_id,
                    case.company["id"],
                    case.opening["ledger_id"],
                    case.book["id"],
                    "2026-10",
                    case.run["close"]["id"],
                    case.reviewer.user_id,
                ),
            )


def test_legacy_preflight_without_revision_vector_requires_new_preflight(close_case):
    case = close_case
    # A migrated old row has this default. Feed it to the same current-input
    # assertion without weakening the new database insert/state guards.
    legacy = {**case.run["close"], "dependencies_json": "{}"}
    with case.db.transaction(actor=case.reviewer) as conn:
        with pytest.raises(AccountingError, match="预检已变化"):
            case.close._assert_current(conn, case.reviewer, legacy)


def test_lock_reads_one_transaction_snapshot_and_writes_wait_for_it(close_case, monkeypatch):
    case = close_case
    case.close.review(case.reviewer, case.run["close"]["id"], command())
    entered, release, writer_started = Event(), Event(), Event()
    original = case.close._snapshot
    calls = []

    def paused(conn, *args):
        result = original(conn, *args)
        calls.append(conn)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(case.close, "_snapshot", paused)

    def write():
        writer_started.set()
        return mutate(case, "bank_adjustment")

    with ThreadPoolExecutor(max_workers=2) as pool:
        locking = pool.submit(case.close.lock, case.reviewer, case.run["close"]["id"], command())
        assert entered.wait(10)
        writing = pool.submit(write)
        assert writer_started.wait(5)
        assert not writing.done()
        release.set()
        assert locking.result(10)["close"]["status"] == "locked"
        with pytest.raises(sqlite3.IntegrityError, match="locked period"):
            writing.result(10)
    assert len(calls) == 1
    assert case.close.get(case.reviewer, case.run["close"]["id"])["close"]["status"] == "locked"


def test_preflight_evidence_cannot_be_rewritten_to_forge_current_revisions(close_case):
    case = close_case
    with pytest.raises(sqlite3.IntegrityError, match="evidence is immutable"):
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute(
                "UPDATE internal_close_runs SET dependencies_json='{}' WHERE id=?",
                (case.run["close"]["id"],),
            )


def test_new_preflight_rejects_old_report_after_restored_policy_change(close_case):
    case = close_case
    mutate(case, "policy_aba")
    with pytest.raises(AccountingError, match="报表输入已变化"):
        case.close.preflight(
            case.manager,
            ClosePreflight(
                book_id=case.book["id"],
                period="2026-10",
                report_id=case.report["report"]["id"],
                idempotency_key="p0-stale-report",
                reason="旧报表不可重复预检",
            ),
        )


def test_new_preflight_rejects_modified_report_content_even_with_old_input_fingerprint(close_case):
    case = close_case
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "UPDATE report_runs SET content_json=replace(content_json,'银行存款','被改写的银行名') WHERE id=?",
            (case.report["report"]["id"],),
        )
    with pytest.raises(AccountingError, match="报表输入已变化"):
        case.close.preflight(
            case.manager,
            ClosePreflight(
                book_id=case.book["id"],
                period="2026-10",
                report_id=case.report["report"]["id"],
                idempotency_key="p0-forged-report",
                reason="复核内容不可静默改写",
            ),
        )


def test_locked_period_allows_real_reviewed_export_and_report_publication(close_case):
    case = close_case
    draft = reviewed_export_draft(case)
    run = case.close.preflight(
        case.manager,
        ClosePreflight(
            book_id=case.book["id"],
            period="2026-10",
            report_id=case.report["report"]["id"],
            idempotency_key="p0-preflight-for-export",
            reason="导出前重新冻结预检",
        ),
    )
    expected = CloseCommand(expected_version=run["close"]["version"], reason="独立复核并锁期")
    case.close.review(case.reviewer, run["close"]["id"], expected)
    case.close.lock(case.reviewer, run["close"]["id"], expected)
    exported = case.ledger.export(
        case.reviewer,
        case.book["id"],
        ExportRequest(
            draft_ids=[draft["id"]],
            idempotency_key="p0-locked-export",
        ),
    )
    assert exported["count"] == 1
    assert (
        case.ledger.export(
            case.reviewer,
            case.book["id"],
            ExportRequest(
                draft_ids=[draft["id"]],
                idempotency_key="p0-locked-export",
            ),
        )["id"]
        == exported["id"]
    )
    after = case.ledger.get_draft(case.reviewer, case.book["id"], draft["id"])
    assert after["status"] == "exported"
    assert after["version"] == draft["version"] + 1
    assert (
        case.close.publish(case.reviewer, run["close"]["id"], expected)["close"]["status"]
        == "published"
    )


@pytest.mark.parametrize("status", ["reviewed", "locked", "published"])
def test_insert_cannot_forge_advanced_close_status(close_case, status):
    case = close_case
    with pytest.raises(sqlite3.IntegrityError, match="unreviewed current preflight"):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            conn.execute(
                "INSERT INTO internal_close_runs(id,book_id,period,status,version,inputs_fingerprint,checks_json,dependencies_json,report_run_id,created_by,created_at) "
                "SELECT 'p0-fake',book_id,period,?,100,inputs_fingerprint,checks_json,dependencies_json,report_run_id,?,created_at FROM internal_close_runs WHERE id=?",
                (status, case.reviewer.user_id, case.run["close"]["id"]),
            )


def test_insert_cannot_impersonate_preflight_creator(close_case):
    case = close_case
    with pytest.raises(sqlite3.IntegrityError, match="unreviewed current preflight"):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            conn.execute(
                "INSERT INTO internal_close_runs(id,book_id,period,status,version,inputs_fingerprint,checks_json,dependencies_json,report_run_id,created_by,created_at) "
                "SELECT 'p0-fake',book_id,period,status,100,inputs_fingerprint,checks_json,dependencies_json,report_run_id,created_by,created_at FROM internal_close_runs WHERE id=?",
                (case.run["close"]["id"],),
            )


@pytest.mark.parametrize(
    "field",
    ["reviewed_by", "reviewed_at", "locked_by", "locked_at", "published_by", "published_at"],
)
def test_approval_fields_cannot_be_backfilled_without_transition(close_case, field):
    case = close_case
    with pytest.raises(sqlite3.IntegrityError, match="prior approvals are immutable"):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            replacement = case.reviewer.user_id if field.endswith("_by") else "2026-10-31T00:00:00Z"
            conn.execute(
                f"UPDATE internal_close_runs SET {field}=? WHERE id=?",
                (replacement, case.run["close"]["id"]),
            )


def test_direct_locked_transition_requires_real_matching_lock(close_case):
    case = close_case
    case.close.review(case.reviewer, case.run["close"]["id"], command())
    with pytest.raises(sqlite3.IntegrityError, match="Matching current period lock"):
        with case.db.transaction(write=True, actor=case.reviewer) as conn:
            conn.execute(
                "UPDATE internal_close_runs SET status='locked',locked_by=?,locked_at='2026-10-31T00:00:00Z' WHERE id=?",
                (case.reviewer.user_id, case.run["close"]["id"]),
            )


def test_sqlite_actor_functions_are_transaction_scoped_and_empty_after_exit(close_case):
    case = close_case
    with case.db.transaction(write=True, actor=case.reviewer) as conn:
        assert (
            conn.execute("SELECT accounting_actor_user(),accounting_actor_firm()").fetchone()[0]
            == case.reviewer.user_id
        )
    with case.db.connect() as raw:
        assert (
            raw.execute("SELECT accounting_actor_user(),accounting_actor_firm()").fetchone()[0]
            == ""
        )
        with pytest.raises(sqlite3.IntegrityError, match="unreviewed current preflight"):
            raw.execute(
                "INSERT INTO internal_close_runs(id,book_id,period,status,version,inputs_fingerprint,checks_json,dependencies_json,report_run_id,created_by,created_at) "
                "SELECT 'p0-no-actor',book_id,period,status,100,inputs_fingerprint,checks_json,dependencies_json,report_run_id,created_by,created_at FROM internal_close_runs WHERE id=?",
                (case.run["close"]["id"],),
            )


@pytest.mark.parametrize("report_state, expected_status", [("missing", 404), ("validated", 409)])
def test_preflight_missing_or_unreviewed_report_is_domain_error(
    close_case, report_state, expected_status
):
    case = close_case
    report_id = case.report["report"]["id"]
    if report_state == "missing":
        report_id = "p0-report-does-not-exist"
    else:
        with case.db.transaction(write=True, actor=case.manager) as conn:
            conn.execute("UPDATE report_runs SET status='validated' WHERE id=?", (report_id,))
    with pytest.raises(AccountingError) as rejected:
        case.close.preflight(
            case.manager,
            ClosePreflight(
                book_id=case.book["id"],
                period="2026-10",
                report_id=report_id,
                idempotency_key="p0-invalid-report-preflight",
                reason="拒绝缺失或未复核报告",
            ),
        )
    assert rejected.value.status == expected_status


def test_failed_preflight_is_persisted_for_actionable_reconciliation_errors(close_case):
    case = close_case
    mutate(case, "bank_adjustment")
    result = case.close.preflight(
        case.manager,
        ClosePreflight(
            book_id=case.book["id"],
            period="2026-10",
            report_id=case.report["report"]["id"],
            idempotency_key="p0-diagnostic-failed-preflight",
            reason="保存银行差异处理清单",
        ),
    )
    assert result["close"]["status"] == "preflight_failed"
    assert (
        next(check for check in result["checks"] if check["code"] == "bank_reconciliation")[
            "status"
        ]
        == "blocked"
    )
