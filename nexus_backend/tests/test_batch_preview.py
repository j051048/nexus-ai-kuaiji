"""批量开期预检：只读、逐户说明、执行口径与真正的开期一致。

The point of P02 is that an operator can see the outcome of a 200-company batch
*before* it runs: who is ready, who is already open, who has no history and who
is not even a client of this firm. A precheck that writes anything would defeat
that purpose, so the main assertions here are "nothing changed".
"""

import pytest

from app.accounting.models import CompanyCreate
from app.ledgers.service import LedgersService
from app.monthly.batch import BatchOpen, MonthlyBatchService
from app.service_plans.service import ServicePlansService
from test_business import business  # noqa: F401  (fixture)
from test_stage9_delivery_api import client  # noqa: F401  (app fixture)


@pytest.fixture
def batch(business):  # noqa: F811
    c = business
    plans = ServicePlansService(c.db, c.ledger, c.service)
    ledgers = LedgersService(c.db)
    c.monthly_batch = MonthlyBatchService(c.db, c.ledger, ledgers, plans, c.service)
    return c


def _count(c, table):
    with c.db.transaction() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def test_preview_reports_ready_and_writes_nothing(batch):
    c = batch
    before = (_count(c, "books"), _count(c, "ledger_periods"), _count(c, "audit_events"))
    result = c.monthly_batch.preview(
        c.manager, BatchOpen(period="2026-11", scope="awaiting", reason="月初批量开期预检")
    )
    assert (result["ready"], result["skipped"], result["blocked"]) == (2, 0, 0)
    assert all(row["status"] == "ready" for row in result["results"])
    assert all(row["from_period"] == "2026-10" for row in result["results"])
    assert (_count(c, "books"), _count(c, "ledger_periods"), _count(c, "audit_events")) == before
    # A preview leaves no batch record either: it is not an execution.
    assert _count(c, "batch_operations") == 0


def test_preview_marks_already_open_and_missing_history(batch):
    c = batch
    c.monthly_batch.open_month(
        c.manager, BatchOpen(period="2026-11", scope="awaiting", reason="先真正开一次期")
    )
    fresh = c.directory.create_company(
        c.manager, CompanyCreate(name="没有历史账期企业", tax_id="91330100PREVIEW0001")
    )
    result = c.monthly_batch.preview(
        c.manager,
        BatchOpen(
            period="2026-11",
            scope="companies",
            companies=[*[row["company_id"] for row in _books(c)], fresh["id"]],
            reason="预检已开期与首次接入企业",
        ),
    )
    rows = {row["company_id"]: row for row in result["results"]}
    existing = [row for row in result["results"] if row["status"] == "skipped"]
    assert len(existing) == 2
    assert {row["code"] for row in existing} == {"already_open"}
    assert rows[fresh["id"]]["status"] == "blocked"
    assert rows[fresh["id"]]["code"] == "no_history"
    assert result["blocked"] == 1


def test_preview_reports_unknown_company_as_no_access(batch):
    c = batch
    result = c.monthly_batch.preview(
        c.manager,
        BatchOpen(
            period="2026-11",
            scope="companies",
            companies=["company-outside-this-firm"],
            reason="预检非本所客户",
        ),
    )
    assert result["blocked"] == 1
    assert result["results"][0]["code"] == "no_access"
    assert result["ready"] == 0


def test_preview_requires_firm_manager(batch):
    c = batch
    with pytest.raises(Exception) as denied:
        c.monthly_batch.preview(
            c.actors["accountant"],
            BatchOpen(period="2026-11", scope="awaiting", reason="会计不能批量开期"),
        )
    assert getattr(denied.value, "status", None) == 403


def test_preview_route_is_read_only_and_manager_only(client):
    """The HTTP surface must expose the same read-only precheck."""
    paths = {
        route.path
        for route in client.app.routes
        if getattr(route, "path", "").startswith("/api/firm/batch-operations")
    }
    assert "/api/firm/batch-operations/monthly-open/preview" in paths


def _books(c):
    with c.db.transaction() as conn:
        return conn.execute(
            "SELECT DISTINCT company_id FROM books WHERE firm_id=? AND period='2026-10'",
            (c.manager.firm_id,),
        ).fetchall()


def test_preview_reports_scope_cap_instead_of_silent_truncation():
    """A 200-company range must say so; silent truncation would hide clients."""
    from app.monthly.batch import MAX_COMPANIES, MonthlyBatchService

    notes = MonthlyBatchService._preview_notes(MAX_COMPANIES - 1)
    assert not any("上限" in note for note in notes)
    capped = MonthlyBatchService._preview_notes(MAX_COMPANIES)
    assert any(str(MAX_COMPANIES) in note and "分批" in note for note in capped)
