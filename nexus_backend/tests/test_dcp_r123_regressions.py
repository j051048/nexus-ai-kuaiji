"""Review-only reproductions; synthetic SQLite; no production or model calls."""

from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.batch_ops.models import CreateMonthlyOpenBatch
from app.batch_ops.service import BatchOperationsService
from app.subledger.models import ControlAccountCreate
from app.subledger.service import SubledgerService
from app.tax import filings
from test_business import business
from test_monthly_batch import monthly_batch
from test_dcp06_review_export import export_case
from c01_bank_fixture import reviewed_export_draft
from test_p0_close_dependencies import close_case
from test_monthly_delivery_package import ready_case
from test_tax_workpaper import tax


def test_exhausted_retry_is_either_rejected_or_claimable(monthly_batch):
    c = monthly_batch
    s = BatchOperationsService(c.db, c.monthly_batch)
    batch = s.create_monthly_open(
        c.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="companies",
            companies=[c.company["id"]],
            reason="隔离核对尝试耗尽",
            idempotency_key=uuid4().hex,
        ),
    )
    for n in range(3):
        item = s.claim("audit-retry")
        assert item is not None
        s.finish(item, "failed", error_code="synthetic")
        if n < 2:
            s.retry(c.manager, batch["id"])
    try:
        s.retry(c.manager, batch["id"])
    except AccountingError as e:
        assert e.status == 409
        return
    assert s.claim("audit-retry-after-limit") is not None, (
        "retry accepted but item permanently unclaimable"
    )


def test_export_response_loss_reuses_completed_export(export_case):
    c = export_case
    reviewed_export_draft(c)
    plan = c.batches.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    c.batches.create_review_export(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    item = c.batches.claim("audit-export")
    first = c.batches.execute(item)
    # Business transaction committed; worker result not settled yet.
    second = c.batches.execute(item)
    assert second["export_id"] == first["export_id"]


def test_published_report_batch_package_success(ready_case):
    c = ready_case
    monthly = SimpleNamespace(access=c.ledger.access, plans=c.packages.plans)
    s = BatchOperationsService(c.db, monthly)
    plan = s.preview_package_drafts(c.manager, [c.company["id"]], "2026-10")
    assert plan["ready"] == 1
    s.create_package_drafts(c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex)
    item = s.claim("audit-package")
    result = s.execute(item)
    assert result["package_id"] and result["package_version"]
    s.finish(item, "succeeded", result=result)


def test_reviewed_unpublished_report_is_not_ready(close_case):
    c = close_case
    s = BatchOperationsService(c.db, SimpleNamespace(access=c.ledger.access))
    plan = s.preview_package_drafts(c.manager, [c.company["id"]], "2026-10")
    assert plan["ready"] == 0, (
        "preview admits unpublished report but package.create requires published"
    )


def test_locked_period_control_mapping_cannot_change(ready_case):
    c = ready_case
    with pytest.raises(AccountingError):
        SubledgerService(c.db).configure_control_account(
            c.manager,
            ControlAccountCreate(
                book_id=c.book["id"],
                kind="receivable",
                control_account_id=c.opening["payable"]["id"],
                control_account_version=1,
                effective_from="2026-10",
                effective_to="2026-10",
                reason="锁期后不得新增本期控制口径",
            ),
        )


def test_invalid_calendar_date_is_rejected(tax):
    with pytest.raises(AccountingError):
        filings.record(
            tax.tax,
            tax.manager,
            {
                "book_id": tax.book["id"],
                "period": "2026-10",
                "kind": "vat",
                "status": "filed",
                "external_ref": "audit-invalid-calendar",
                "occurred_at": "2026-99-99",
                "amount_cents": 0,
            },
        )


def test_real_control_configuration_completes_setup_step(ready_case):
    from app.accounting.admin_service import AdminService
    from app.onboarding.service import OnboardingService

    c = ready_case
    # ready_case reached actual close/lock/publish through the domain services.
    service = OnboardingService(c.db, AdminService(c.db), c.ledger, None, None)
    result = service.setup(c.manager, c.company["id"], c.book["id"], "2026-10")
    step = next(row for row in result["steps"] if row["id"] == "control")
    assert step["complete"] is True, step


def test_investigation_never_releases_facts_after_revocation(business, monkeypatch):
    from app.assistant import investigation

    monkeypatch.setenv("ACCOUNTING_ASSISTANT_INVESTIGATION", "1")
    actor = business.actors["accountant"]

    def revoke(name):
        if name == "close_blockers":
            with business.db.transaction(write=True, actor=business.manager) as conn:
                conn.execute(
                    "DELETE FROM book_access WHERE book_id=? AND user_id=?",
                    (business.book["id"], actor.user_id),
                )

    result = investigation.investigate(
        business.service, actor, business.book["id"], "2026-10", on_step=revoke
    )
    assert result["status"] == "blocked" and result["findings"] == {}


def test_future_configuration_remains_possible(ready_case):
    c = ready_case
    row = SubledgerService(c.db).configure_control_account(
        c.manager,
        ControlAccountCreate(
            book_id=c.book["id"],
            kind="receivable",
            control_account_id=c.opening["payable"]["id"],
            control_account_version=1,
            effective_from="2026-11",
            reason="后续月份配置",
        ),
    )
    assert row["effective_from"] == "2026-11"


def test_cancelled_expired_item_is_not_resurrected(monthly_batch):
    c = monthly_batch
    s = BatchOperationsService(c.db, c.monthly_batch)
    batch = s.create_monthly_open(
        c.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="companies",
            companies=[c.company["id"]],
            reason="取消恢复验证",
            idempotency_key=uuid4().hex,
        ),
    )
    item = s.claim("cancel-recovery")
    s.cancel_item(c.manager, batch["id"], item["item_id"], "取消待恢复项")
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE batch_operation_items SET lease_until='2000-01-01' WHERE id=?",
            (item["item_id"],),
        )
    s.recover()
    assert s.claim("after-cancel") is None
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT status FROM batch_operation_items WHERE id=?", (item["item_id"],)
        ).fetchone()
    assert row["status"] == "cancelled"


def test_expired_owner_cannot_finish_without_recover(monthly_batch):
    c = monthly_batch
    s = BatchOperationsService(c.db, c.monthly_batch)
    s.create_monthly_open(
        c.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="companies",
            companies=[c.company["id"]],
            reason="过期围栏验证",
            idempotency_key=uuid4().hex,
        ),
    )
    item = s.claim("expired")
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE batch_operation_items SET lease_until='2000-01-01' WHERE id=?",
            (item["item_id"],),
        )
    assert s.finish(item, "succeeded", result={"status": "created"}) is False


def test_monthly_cancel_between_read_and_creation_is_fenced(monthly_batch, monkeypatch):
    c = monthly_batch
    s = BatchOperationsService(c.db, c.monthly_batch)
    batch = s.create_monthly_open(
        c.manager,
        CreateMonthlyOpenBatch(
            period="2026-11",
            scope="companies",
            companies=[c.company["id"]],
            reason="取消建期竞争",
            idempotency_key=uuid4().hex,
        ),
    )
    item = s.claim("cancel-before-create")
    original = c.ledgers.create_period

    def cancel(*args, **kwargs):
        s.cancel_item(c.manager, batch["id"], item["item_id"], "取消创建前任务")
        return original(*args, **kwargs)

    monkeypatch.setattr(c.ledgers, "create_period", cancel)
    result = s.execute(item)
    assert result["status"] == "blocked"
    with c.db.transaction(actor=c.manager) as conn:
        assert not conn.execute(
            "SELECT 1 FROM books WHERE company_id=? AND period='2026-11'", (c.company["id"],)
        ).fetchone()
