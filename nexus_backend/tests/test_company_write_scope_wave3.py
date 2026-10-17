"""第三批企业写锁：月结、交付回执、收付款核销与单企业月度事实。"""

from uuid import uuid4

from app.accounting.seed import LOCAL_ACTOR
from app.closing.service import ClosingService
from app.monthly.models import (
    CheckCreate,
    MonthlyReason,
    PlanApproval,
    PlanGenerate,
)
from test_business import business  # noqa: F401
from test_catalog import case as catalog_case  # noqa: F401
from test_company_write_scope_wave1 import assert_scoped, tap
from test_stage9_delivery import create as delivery_create, exported, facts
from test_stage10_closing import close_ready
from test_stage10_settlements import allocate, setup as settlement_setup
from test_stage17_monthly import confirmed, monthly_case, plan  # noqa: F401


def company_of(db, book_id):
    with db.transaction() as conn:
        return conn.execute("SELECT company_id FROM books WHERE id=?", (book_id,)).fetchone()[0]


def test_closing_preflight_review_close_are_company_scoped(catalog_case):
    accounting, _, book = catalog_case
    closing = ClosingService(accounting.db)
    calls = tap(accounting.db)
    close_ready(closing, LOCAL_ACTOR, book)
    assert_scoped(calls, company_of(accounting.db, book))


def test_delivery_receipt_is_company_scoped(catalog_case):
    ledger, delivery, book, export, _approved = exported(catalog_case)
    body = facts(delivery, book, export["id"])
    calls = tap(ledger.db)
    delivery_create(delivery, book, export["id"], body)
    assert_scoped(calls, company_of(ledger.db, book))


def test_settlement_open_preview_confirm_are_company_scoped(business):
    c = business
    service, _original, item, bank = settlement_setup(c)
    calls = tap(c.db)
    allocate(c, service, item, bank)
    assert_scoped(calls, c.company["id"])


def test_monthly_facts_are_company_scoped(monthly_case):
    c = monthly_case
    calls = tap(c.db)
    opening = confirmed(c, "opening", "100.00")
    closing = confirmed(c, "closing", "98.00")
    check = c.monthly.create_check(
        c.owner,
        c.book,
        CheckCreate(
            opening_id=opening["id"],
            closing_id=closing["id"],
            idempotency_key=uuid4().hex,
        ),
    )
    c.monthly.close_check(c.owner, c.book, check["id"], MonthlyReason(reason="差异保留后续追查"))
    row = c.monthly.create_plan(c.owner, c.book, plan())
    row = c.monthly.approve_plan(
        c.reviewer,
        c.book,
        row["id"],
        PlanApproval(version=1, reason="按合同独立核对金额与科目"),
    )
    c.monthly.generate(
        c.owner,
        c.book,
        row["id"],
        PlanGenerate(version=1, idempotency_key=uuid4().hex),
    )
    assert_scoped(calls, c.company)
