"""Run existing review/race assertions with the verified PG16/17 test target.

The historical P0 module assumes a removed /tmp/test_nexus_p0_ cluster and the
standalone review module omits its dependent fixture import. Reuse their exact
assertions without modifying C01 or relaxing the isolated suite target gate.
"""

from test_monthly_delivery_postgres import p0_case, p0_pg  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.accounting.domain import AccountingError
from app.b04_close.models import CloseCommand, ClosePreflight
from app.b04_close.service import CloseService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.subledger.models import ControlApplicability
from app.subledger.service import SubledgerService
from p01_recovery_business import _zero_formal_opening
from test_p0_report_postgres import transition
from test_p0_close_upload_postgres import _await_blocked, _runtime_transaction
from test_review_boundary_postgres import (  # noqa: F401
    test_direct_sql_unbalanced_approval_is_rejected,
    test_direct_sql_closed_period_approval_is_rejected,
    test_direct_sql_export_without_review_is_rejected,
    test_direct_sql_cross_company_approval_cannot_see_or_change_draft,
)


@pytest.mark.parametrize("operation", ["review", "publish"])
def test_report_rechecks_committed_policy_after_real_company_gate(p0_case, operation):
    c = p0_case
    # The obsolete fixture predates mandatory applicability declarations. Build
    # a genuine zero-activity book using the current services, never fake a pass.
    _zero_formal_opening(c.db, c.manager, c.reviewer, c.book)
    SubledgerService(c.db).set_control_applicability(
        c.manager,
        ControlApplicability(
            book_id=c.book["id"],
            status="not_applicable",
            effective_from="2026-10",
            reason="本地零业务账套，没有往来余额及发生",
        ),
    )
    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")["report"]
    rid, version = report["id"], report["version"]
    reports.validate(c.manager, rid)
    expected = "validated"
    if operation == "publish":
        reports.review(c.reviewer, rid, ReportCommand(expected_version=version, reason="独立复核"))
        close = CloseService(c.db)
        run = close.preflight(
            c.manager,
            ClosePreflight(
                book_id=c.book["id"],
                period="2026-10",
                report_id=rid,
                idempotency_key="p02-race-" + rid,
                reason="真实月结预检",
            ),
        )["close"]
        assert run["status"] == "preflight_passed"
        command = CloseCommand(expected_version=run["version"], reason="独立月结核对")
        close.review(c.reviewer, run["id"], command)
        close.lock(c.reviewer, run["id"], command)
        expected = "reviewed"
    with ThreadPoolExecutor(max_workers=1) as pool:
        with _runtime_transaction(c, c.manager) as writer:
            writer.execute(
                "SELECT accounting.begin_company_write(%s,%s)",
                (c.manager.firm_id, c.book["company_id"]),
            )
            writer.execute(
                "UPDATE accounting_policies SET industry='P02本机并发政策变更' WHERE ledger_id=%s",
                ("ledger_" + c.book["id"],),
            )
            blocker = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            pending = pool.submit(transition, c, operation, rid, version)
            _await_blocked(c, blocker, pending)
        outcome = pending.result(timeout=15)
    assert isinstance(outcome, AccountingError)
    assert outcome.status == 409 and "输入已变化" in outcome.detail
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT status,reviewed_by,published_by FROM report_runs WHERE id=?", (rid,)
        ).fetchone()
        assert row["status"] == expected and row["published_by"] is None
        if operation == "review":
            assert row["reviewed_by"] is None
