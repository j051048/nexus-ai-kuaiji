"""Same service cases under real restricted local PostgreSQL roles."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounting.domain import AccountingError
from app.periodic.service import PeriodicService
from app.subledger.service import SubledgerService
from test_intelligence_workflow_postgres import pg_ai, business_case, business_postgres  # noqa: F401
from test_business_postgres import ADMIN_URL, APP_URL
from test_monthly_delivery_postgres import package_case, p0_case, p0_pg  # noqa: F401
from test_finance_agent_reuse import (
    asset_case,
    test_depreciation_tail_and_first_month as check_tail,
    test_asset_creation_retains_reason_in_audit as check_audit,
    test_periodic_workspace_review_then_post_and_scope as check_workflow,
    test_aging_scope_and_separate_kind_totals as check_aging,
    test_aging_rejects_invalid_or_noncanonical_date as check_date,
    test_expired_asset_account_is_rejected_before_recording as check_expired,
)

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需显式隔离 PostgreSQL 配置")


@pytest.fixture
def finance(pg_ai, monkeypatch):
    # The old SQLite fixture's ledger lookup has no actor. Bind the same lookup
    # to the real actor; every write still uses the unmodified product service.
    import test_posting_service

    def ledger_of(case):
        with case.db.transaction(actor=case.manager) as conn:
            return conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
            ).fetchone()[0]

    monkeypatch.setattr(test_posting_service, "ledger_of", ledger_of)
    return pg_ai


@pytest.mark.parametrize(
    "cost,residual,life,start,expected",
    [
        ("100.00", "0.00", 3, "2026-08", 10000),
        ("100.00", "1.00", 7, "2026-04", 9900),
        ("0.01", "0.00", 12, "2025-11", 1),
        ("100.00", "0.00", 3, "2026-10", 3333),
    ],
)
def test_tail(finance, cost, residual, life, start, expected):
    check_tail(finance, cost, residual, life, start, expected)


def test_audit(finance):
    check_audit(finance)


def test_expired_account(finance):
    check_expired(finance)


def test_review_post_and_isolation(finance):
    check_workflow(finance)


def test_aging_identity(finance):
    check_aging(finance)


@pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "20261031", "2026-W44-6"])
def test_invalid_date(finance, value):
    check_date(finance, value)


def test_generation_competition_and_revocation(finance):
    c = finance
    service, asset = asset_case(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.generate_asset(c.manager, asset["id"]), range(2)))
    assert len({r["draft"]["id"] for r in results}) == 1
    draft_id = results[0]["draft"]["id"]
    with pytest.raises(Exception) as denied:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE periodic_drafts SET status='approved',approved_by=?,approved_at=? WHERE id=?",
                (c.manager.user_id, "2026-11-07T00:00:00Z", draft_id),
            )
    assert getattr(denied.value, "sqlstate", None) in {"42501", "23514"}
    reviewer = c.actors["accountant"]
    assert service.workspace(reviewer, c.book["id"])["drafts"][0]["can_approve"]
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book["id"], reviewer.user_id),
        )
    for action in (
        lambda: service.workspace(reviewer, c.book["id"]),
        lambda: SubledgerService(c.db).aging(reviewer, c.book["id"], "2026-10-31"),
    ):
        with pytest.raises(AccountingError):
            action()
    assert (
        PeriodicService(c.db).workspace(c.manager, c.book["id"])["drafts"][0]["draft"]["status"]
        == "draft"
    )


def test_locked_period_disables_actions_and_rejects_native_asset_write(package_case):
    c = package_case
    view = PeriodicService(c.db).workspace(c.manager, c.book["id"])
    assert view["locked"] and not view["can_generate"]
    accounts = view["accounts"]
    assert len(accounts) >= 2
    with pytest.raises(Exception) as denied:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "INSERT INTO periodic_assets(id,book_id,kind,name,cost_cents,residual_cents,life_months,start_period,accumulated_cents,expense_account_id,expense_account_version,accum_account_id,accum_account_version,status,created_by,created_at) VALUES(?,?,'fixed','locked asset',100,0,3,'2026-10',0,?,1,?,1,'active',?,?)",
                (
                    "locked-" + c.book["id"],
                    c.book["id"],
                    accounts[0]["id"],
                    accounts[1]["id"],
                    c.manager.user_id,
                    "2026-11-07T00:00:00Z",
                ),
            )
    assert getattr(denied.value, "sqlstate", None) in {"42501", "23514", "55000"}
    assert PeriodicService(c.db).workspace(c.manager, c.book["id"])["assets"] == []
