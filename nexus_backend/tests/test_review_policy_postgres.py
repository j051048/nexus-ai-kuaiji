"""真实 PostgreSQL 的制单责任、独立复核及并发边界；仅隔离库。"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftLine, DraftUpdate, ImportRequest, SourceRow
from app.opening.models import (
    AccountCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import ManualLine, ManualVoucher, OpeningBind
from app.posting.service import PostingService
from test_business_postgres import business_case, business_postgres  # noqa: F401


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="仅通过本地 PostgreSQL 隔离运行器执行",
)


def _draft(c):
    c.service.import_sources(
        c.owner,
        c.book,
        ImportRequest(
            source_name="独立复核合成流水",
            rows=[
                SourceRow(
                    external_id=uuid4().hex,
                    date="2026-10-15",
                    description="办公用品",
                    counterparty="虚构供应商",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="合成发票",
                )
            ],
        ),
    )
    c.service.suggestions(c.owner, c.book)
    return c.service.get_book(c.owner, c.book)["drafts"][0]


def test_pg_default_independent_rejects_maker_self_review(business_case):
    c = business_case
    draft = _draft(c)
    assert not draft["issues"]
    with pytest.raises(AccountingError):
        c.service.approve(
            c.owner, c.book, draft["id"], ApprovalRequest(expected_version=draft["version"])
        )
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_native_sql_cannot_self_review(business_case):
    c = business_case
    draft = _draft(c)
    assert not draft["issues"]
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "UPDATE drafts SET status='approved',version=version+1,approved_by=?,"
                "approved_at='2026-10-25T10:00:00Z' WHERE id=? AND book_id=?",
                (c.owner.user_id, draft["id"], c.book),
            )
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_distinct_reviewer_can_review(business_case):
    c = business_case
    draft = _draft(c)
    reviewed = c.service.approve(
        c.reviewer, c.book, draft["id"], ApprovalRequest(expected_version=draft["version"])
    )
    assert reviewed["status"] == "approved"


def _manual(c):
    opening = OpeningService(c.db)
    with c.db.transaction(actor=c.owner) as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book,)
        ).fetchone()[0]
    policy = opening.create_policy(
        c.owner,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="独立复核合成政策",
        ),
    )
    accounts = [
        opening.create_account(
            c.owner,
            AccountCreate(
                ledger_id=ledger_id,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from="2026-01",
                effective_to="2026-12",
                reason="独立复核合成科目",
            ),
        )
        for code, name, category, normal in (
            ("1002", "银行存款", "asset", "debit"),
            ("2202", "应付账款", "liability", "credit"),
        )
    ]
    package = opening.create_package(
        c.owner,
        OpeningPackageCreate(ledger_id=ledger_id, target_period="2026-10", policy_id=policy["id"]),
    )
    version = opening.import_version(
        c.owner,
        package["package"]["id"],
        OpeningImport(
            idempotency_key=uuid4().hex,
            reason="独立复核零余额期初",
            lines=[
                OpeningLineInput(
                    account_id=a["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="0.00",
                    ytd_credit="0.00",
                    opening="0.00",
                )
                for a in accounts
            ],
        ),
    )["version"]["id"]
    reviewer = c.person(firm_role="admin")
    opening.validate_version(c.owner, version)
    command = OpeningCommand(expected_version=1, reason="独立复核期初验证")
    opening.submit_version(c.owner, version, command)
    opening.approve_version(reviewer, version, command)
    opening.freeze_version(reviewer, version, command)
    PostingService(c.db).bind_opening(
        c.owner, OpeningBind(book_id=c.book, package_version_id=version, reason="绑定合成期初")
    )
    return ManualVoucher(
        command_key=uuid4().hex,
        book_id=c.book,
        voucher_date="2026-10-16",
        description="待审手工合成凭证",
        lines=[
            ManualLine(account_id=accounts[0]["id"], account_version=1, debit="1.00"),
            ManualLine(account_id=accounts[1]["id"], account_version=1, credit="1.00"),
        ],
    )


def test_pg_unreviewed_manual_cannot_post(business_case):
    c = business_case
    request = _manual(c)
    with pytest.raises(AccountingError):
        PostingService(c.db).post_manual(c.owner, request)
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute("SELECT count(*) FROM vouchers WHERE book_id=?", (c.book,)).fetchone()[0]
            == 0
        )


def _prepared_manual(c):
    from app.posting.manual_review import ManualReviewService

    request = _manual(c)
    service = ManualReviewService(c.db)
    prepared = service.prepare(c.owner, request)["request"]
    return service, request, prepared


def _review_manual(c, service, prepared, actor=None, policy_version=0):
    from app.accounting.review_policy_models import ManualReviewApproval

    return service.approve(
        actor or c.reviewer,
        prepared["id"],
        ManualReviewApproval(
            expected_version=prepared["version"],
            expected_policy_version=policy_version,
            reason="独立核对手工凭证金额科目原件",
        ),
    )["request"]


def test_pg_manual_review_binds_content_and_blocks_self_review(business_case):
    from app.accounting.review_policy_models import ManualReviewPost

    c = business_case
    service, request, prepared = _prepared_manual(c)
    with pytest.raises(AccountingError) as error:
        _review_manual(c, service, prepared, actor=c.owner)
    assert error.value.status == 403
    reviewed = _review_manual(c, service, prepared)
    post = ManualReviewPost(expected_version=reviewed["version"], command_key=request.command_key)
    result = service.post(c.owner, prepared["id"], post)
    replay = service.post(c.owner, prepared["id"], post)
    assert result["voucher"]["id"] == replay["voucher"]["id"]
    with c.db.transaction(actor=c.owner) as conn:
        row = conn.execute(
            "SELECT review_mode,review_request_id,review_completion_id FROM vouchers WHERE id=?",
            (result["voucher"]["id"],),
        ).fetchone()
    assert row["review_mode"] == "independent"
    assert row["review_request_id"] == prepared["id"]
    assert row["review_completion_id"]


def test_pg_manual_approved_request_cannot_authorize_changed_content(business_case):
    c = business_case
    service, request, prepared = _prepared_manual(c)
    _review_manual(c, service, prepared)
    tampered = request.model_copy(
        update={"review_request_id": prepared["id"], "description": "复核后偷偷换掉摘要"}
    )
    with pytest.raises(AccountingError):
        PostingService(c.db).post_manual(c.owner, tampered)
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute("SELECT count(*) FROM vouchers WHERE book_id=?", (c.book,)).fetchone()[0]
            == 0
        )


def test_pg_manual_single_operator_still_requires_recorded_review(business_case):
    from app.accounting.review_policy_models import ManualReviewPost

    c = business_case
    _policy(c)
    service, request, prepared = _prepared_manual(c)
    with pytest.raises(AccountingError):
        PostingService(c.db).post_manual(c.owner, request)
    reviewed = _review_manual(c, service, prepared, actor=c.owner, policy_version=1)
    result = service.post(
        c.owner,
        prepared["id"],
        ManualReviewPost(expected_version=reviewed["version"], command_key=request.command_key),
    )
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT review_mode FROM vouchers WHERE id=?", (result["voucher"]["id"],)
            ).fetchone()[0]
            == "single_operator"
        )


def _withdraw(c, prepared, actor=None, book_id=None, version=None, signer=None):
    actor = actor or c.owner
    with _runtime(c, actor) as conn:
        conn.execute(
            "INSERT INTO manual_review_withdrawals(request_id,book_id,request_version,reason,"
            "withdrawn_by,created_at) VALUES(%s,%s,%s,'撤回未定稿合成凭证',%s,'2026-10-25T10:00:00Z')",
            (
                prepared["id"],
                book_id or c.book,
                version if version is not None else prepared["version"],
                signer or actor.user_id,
            ),
        )


def test_pg_withdrawn_manual_request_cannot_be_reviewed(business_case):
    c = business_case
    service, _, prepared = _prepared_manual(c)
    _withdraw(c, prepared)
    with pytest.raises(AccountingError) as error:
        _review_manual(c, service, prepared)
    assert error.value.status == 409
    with c.db.transaction(actor=c.owner) as conn:
        row = conn.execute(
            "SELECT status,reviewed_by FROM manual_review_requests WHERE id=?", (prepared["id"],)
        ).fetchone()
    assert row["status"] == "pending" and row["reviewed_by"] is None


@pytest.mark.parametrize("wrong", ["signer", "book", "version"])
def test_pg_manual_withdrawal_rejects_forged_scope_and_identity(business_case, wrong):
    c = business_case
    _, _, prepared = _prepared_manual(c)
    options = {
        "signer": {"actor": c.reviewer, "signer": c.owner.user_id},
        "book": {"book_id": c.other_book},
        "version": {"version": prepared["version"] + 1},
    }
    with pytest.raises(c.psycopg.Error):
        _withdraw(c, prepared, **options[wrong])
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM manual_review_withdrawals WHERE request_id=?",
                (prepared["id"],),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("manual_status", ["pending", "approved"])
def test_pg_unposted_manual_is_explicit_close_blocker_and_withdrawal_can_release(
    business_case, manual_status
):
    from app.accounting.review_policy_models import ManualReviewWithdrawal
    from app.b04_close.models import CloseCommand, ClosePreflight
    from app.b04_close.service import CloseService
    from app.reports.models import ReportCommand
    from app.reports.service import ReportService
    from app.subledger.models import ControlApplicability
    from app.subledger.service import SubledgerService

    c = business_case
    manual, _, prepared = _prepared_manual(c)
    closer = c.person(firm_role="admin")
    if manual_status == "approved":
        _review_manual(c, manual, prepared)
    SubledgerService(c.db).set_control_applicability(
        c.owner,
        ControlApplicability(
            book_id=c.book,
            status="not_applicable",
            effective_from="2026-10",
            reason="合成企业无正式发生和子账余额",
        ),
    )
    reports = ReportService(c.db)
    report = reports.create(c.owner, c.book, "2026-10")["report"]
    reports.validate(c.owner, report["id"])
    reports.review(
        closer,
        report["id"],
        ReportCommand(expected_version=report["version"], reason="独立核验零余额报告"),
    )
    close = CloseService(c.db)

    def preflight():
        return close.preflight(
            c.owner,
            ClosePreflight(
                book_id=c.book,
                period="2026-10",
                report_id=report["id"],
                idempotency_key=uuid4().hex,
                reason="核验未完成手工凭证门槛",
            ),
        )

    blocked = preflight()
    checks = {row["code"]: row for row in blocked["checks"]}
    assert len(checks) == 7
    assert checks["unposted_manual"]["status"] == "blocked"
    assert all(row["status"] == "pass" for code, row in checks.items() if code != "unposted_manual")
    assert blocked["close"]["status"] == "preflight_failed"
    if manual_status == "pending":
        manual.withdraw(
            c.owner,
            prepared["id"],
            ManualReviewWithdrawal(
                expected_version=prepared["version"],
                reason="录入重复，保留撤回记录",
            ),
        )
        ready = preflight()
        assert ready["close"]["status"] == "preflight_passed"
        reviewed = close.review(
            closer,
            ready["close"]["id"],
            CloseCommand(
                expected_version=ready["close"]["version"],
                reason="独立核对全部月结门槛",
            ),
        )
        locked = close.lock(
            closer,
            reviewed["close"]["id"],
            CloseCommand(
                expected_version=reviewed["close"]["version"],
                reason="独立锁定合成零余额期间",
            ),
        )
        assert locked["close"]["status"] == "locked"


def _policy(c, mode="single_operator", expected=0):
    from app.accounting.review_policy import ReviewPolicyService
    from app.accounting.review_policy_models import ReviewPolicyChange

    return ReviewPolicyService(c.db).configure(
        c.owner,
        c.book,
        ReviewPolicyChange(expected_version=expected, mode=mode, reason="本机合成单人办理责任确认"),
    )


def _approve(c, draft, actor=None, policy_version=0, reason="合成票据逐项核对完成"):
    return c.service.approve(
        actor or c.reviewer,
        c.book,
        draft["id"],
        ApprovalRequest(
            expected_version=draft["version"],
            expected_policy_version=policy_version,
            review_reason=reason,
        ),
    )


def _receipt(c, draft):
    with c.db.transaction(actor=c.owner) as conn:
        return dict(
            conn.execute(
                "SELECT * FROM draft_review_receipts WHERE draft_id=? ORDER BY reviewed_version DESC",
                (draft["id"],),
            ).fetchone()
        )


def test_pg_single_operator_has_explicit_non_independent_receipt(business_case):
    c = business_case
    draft = _draft(c)
    _policy(c)
    draft = c.service.get_draft(c.owner, c.book, draft["id"])
    _approve(c, draft, actor=c.owner, policy_version=1)
    receipt = _receipt(c, draft)
    assert receipt["maker_user_id"] == receipt["reviewer_user_id"] == c.owner.user_id
    assert receipt["mode"] == "single_operator"
    assert not receipt["independent"]
    assert receipt["policy_version"] == 1
    assert receipt["reason"] == "合成票据逐项核对完成"


def test_pg_independent_receipt_exactly_binds_reviewed_version(business_case):
    c = business_case
    draft = _draft(c)
    _approve(c, draft)
    receipt = _receipt(c, draft)
    assert receipt["maker_user_id"] == c.owner.user_id
    assert receipt["reviewer_user_id"] == c.reviewer.user_id
    assert receipt["reviewed_version"] == draft["version"]
    assert receipt["mode"] == "independent" and receipt["independent"]
    assert receipt["content_fingerprint"]


def test_pg_policy_change_rejects_old_review_request(business_case):
    c = business_case
    draft = _draft(c)
    _policy(c)
    with pytest.raises(AccountingError) as error:
        _approve(c, draft, policy_version=0)
    assert error.value.status == 409
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_policy_change_does_not_relabel_historical_review(business_case):
    c = business_case
    draft = _draft(c)
    _approve(c, draft)
    before = _receipt(c, draft)
    _policy(c)
    assert _receipt(c, draft) == before
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "approved"


def test_pg_review_permission_does_not_grant_policy_management(business_case):
    from app.accounting.review_policy import ReviewPolicyService
    from app.accounting.review_policy_models import ReviewPolicyChange

    c = business_case
    with pytest.raises(AccountingError) as error:
        ReviewPolicyService(c.db).configure(
            c.reviewer,
            c.book,
            ReviewPolicyChange(
                expected_version=0, mode="single_operator", reason="试图放宽本人复核权限"
            ),
        )
    assert error.value.status == 403


def test_pg_content_edit_rebinds_maker_to_current_version(business_case):
    c = business_case
    draft = _draft(c)
    editor = c.person(firm_role="admin")
    updated = c.service.update_draft(
        editor,
        c.book,
        draft["id"],
        DraftUpdate(
            expected_version=draft["version"],
            description="第二位会计核实后制单",
            lines=[
                DraftLine(**{key: line[key] for key in ("account_code", "debit", "credit")})
                for line in draft["lines"]
            ],
            correction_reason="业务摘要纠错",
        ),
    )
    assert updated["version"] > draft["version"]
    with pytest.raises(AccountingError):
        _approve(c, updated, actor=editor)
    _approve(c, updated, actor=c.owner)
    receipt = _receipt(c, updated)
    assert receipt["maker_user_id"] == editor.user_id
    assert receipt["reviewer_user_id"] == c.owner.user_id
    assert receipt["reviewed_version"] == updated["version"]


def test_pg_native_line_change_cannot_accept_old_pending_version(business_case):
    c = business_case
    draft = _draft(c)
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "UPDATE draft_lines SET account_code='6602.99' WHERE draft_id=? AND debit_cents>0",
            (draft["id"],),
        )
    with pytest.raises(AccountingError) as error:
        _approve(c, draft)
    assert error.value.status == 409
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_duplicate_approval_does_not_duplicate_receipt(business_case):
    c = business_case
    draft = _draft(c)
    _approve(c, draft)
    with pytest.raises(AccountingError) as error:
        _approve(c, draft)
    assert error.value.status == 409
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM draft_review_receipts WHERE draft_id=?", (draft["id"],)
            ).fetchone()[0]
            == 1
        )


def test_pg_runtime_cannot_forge_preparer_or_reviewer(business_case):
    c = business_case
    draft = _draft(c)
    forged_reviewer = c.person(firm_role="admin")
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "UPDATE draft_preparations SET maker_user_id=? WHERE draft_id=?",
                (c.reviewer.user_id, draft["id"]),
            )
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute(
                "UPDATE drafts SET status='approved',version=version+1,approved_by=?,"
                "approved_at='2026-10-25T10:00:00Z' WHERE id=?",
                (forged_reviewer.user_id, draft["id"]),
            )
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_review_receipt_is_immutable_and_hidden_from_unassigned_user(business_case):
    c = business_case
    draft = _draft(c)
    _approve(c, draft)
    with c.db.transaction(actor=c.unassigned) as conn:
        assert (
            conn.execute(
                "SELECT * FROM draft_review_receipts WHERE draft_id=?", (draft["id"],)
            ).fetchall()
            == []
        )
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "UPDATE draft_review_receipts SET reason='rewrite' WHERE draft_id=?", (draft["id"],)
            )


@contextmanager
def _runtime(c, actor):
    with c.psycopg.connect(os.environ["ACCOUNTING_TEST_DATABASE_URL"]) as conn:
        conn.execute("SET LOCAL ROLE accounting_app")
        conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
        conn.execute("SET LOCAL statement_timeout='12s'")
        conn.execute(
            "SELECT set_config('app.user_id',%s,true),set_config('app.firm_id',%s,true)",
            (actor.user_id, actor.firm_id),
        )
        yield conn


def _await_blocked(c, pid, future):
    until = time.monotonic() + 8
    with c.psycopg.connect(os.environ["ACCOUNTING_TEST_POSTGRES_URL"], autocommit=True) as observer:
        while time.monotonic() < until:
            if observer.execute(
                "SELECT 1 FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))", (pid,)
            ).fetchone():
                return
            assert not future.done(), "审核未等到独立PG会话提交即完成"
            time.sleep(0.025)
    pytest.fail("未观察到真实 PostgreSQL 双会话等待")


def _captured(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception as exc:
        return exc


def test_pg_committed_revocation_wins_waiting_approval(business_case):
    c = business_case
    draft = _draft(c)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with c.psycopg.connect(os.environ["ACCOUNTING_TEST_POSTGRES_URL"]) as revoker:
            revoker.execute("SELECT accounting.lock_legacy_write()")
            revoker.execute(
                "UPDATE accounting.users SET active=0 WHERE id=%s", (c.reviewer.user_id,)
            )
            pid = revoker.execute("SELECT pg_backend_pid()").fetchone()[0]
            future = pool.submit(_captured, _approve, c, draft)
            _await_blocked(c, pid, future)
        result = future.result(timeout=15)
    assert isinstance(result, AccountingError), result
    assert result.status in {403, 404}
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_policy_commit_wins_waiting_approval(business_case):
    c = business_case
    draft = _draft(c)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _runtime(c, c.owner) as writer:
            writer.execute("SELECT accounting.lock_legacy_write()")
            writer.execute(
                "INSERT INTO ledger_review_policies(ledger_id,version,firm_id,company_id,mode,"
                "reason,confirmed_by,created_at) SELECT ledger_id,1,%s,%s,'single_operator',"
                "'本地并发责任政策',%s,'2026-10-25T10:00:00Z' FROM ledger_periods WHERE book_id=%s",
                (c.owner.firm_id, c.company, c.owner.user_id, c.book),
            )
            pid = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            future = pool.submit(_captured, _approve, c, draft)
            _await_blocked(c, pid, future)
        result = future.result(timeout=15)
    assert isinstance(result, AccountingError), result
    assert result.status == 409
    assert c.service.get_draft(c.owner, c.book, draft["id"])["status"] == "draft"


def test_pg_native_policy_cannot_bind_other_company(business_case):
    c = business_case
    with pytest.raises(c.psycopg.Error):
        with _runtime(c, c.owner) as conn:
            conn.execute(
                "INSERT INTO ledger_review_policies(ledger_id,version,firm_id,company_id,mode,"
                "reason,confirmed_by,created_at) SELECT ledger_id,1,%s,%s,'single_operator',"
                "'伪造跨企业绑定',%s,'2026-10-25T10:00:00Z' FROM ledger_periods WHERE book_id=%s",
                (c.owner.firm_id, c.other_company, c.owner.user_id, c.book),
            )


def test_pg_committed_review_wins_duplicate_waiting_request(business_case):
    c = business_case
    draft = _draft(c)
    second = c.person(firm_role="admin")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _runtime(c, c.reviewer) as first:
            first.execute(
                "UPDATE drafts SET status='approved',version=version+1,approved_by=%s,"
                "approved_at='2026-10-25T10:00:00Z',review_policy_version=0,"
                "review_reason='第一复核人已完成核对' WHERE id=%s",
                (c.reviewer.user_id, draft["id"]),
            )
            pid = first.execute("SELECT pg_backend_pid()").fetchone()[0]
            future = pool.submit(_captured, _approve, c, draft, actor=second)
            _await_blocked(c, pid, future)
        result = future.result(timeout=15)
    assert isinstance(result, AccountingError), result
    assert result.status == 409
    assert _receipt(c, draft)["reviewer_user_id"] == c.reviewer.user_id
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM draft_review_receipts WHERE draft_id=?", (draft["id"],)
            ).fetchone()[0]
            == 1
        )


def test_pg_review_commits_before_policy_change_retains_old_policy_receipt(business_case):
    c = business_case
    draft = _draft(c)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _runtime(c, c.reviewer) as first:
            first.execute(
                "UPDATE drafts SET status='approved',version=version+1,approved_by=%s,"
                "approved_at='2026-10-25T10:00:00Z',review_policy_version=0,"
                "review_reason='独立复核先于后续政策变更' WHERE id=%s",
                (c.reviewer.user_id, draft["id"]),
            )
            pid = first.execute("SELECT pg_backend_pid()").fetchone()[0]
            future = pool.submit(_captured, _policy, c)
            _await_blocked(c, pid, future)
        result = future.result(timeout=15)
    assert not isinstance(result, Exception), result
    receipt = _receipt(c, draft)
    assert receipt["policy_version"] == 0
    assert receipt["mode"] == "independent"
    assert receipt["independent"]
