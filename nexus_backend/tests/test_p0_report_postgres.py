"""Real PostgreSQL report transitions cannot authorize from a pre-gate read.

Only the dedicated disposable loopback cluster from p0_pg is accepted. A real
restricted writer holds the company gate and an uncommitted policy mutation;
pg_blocking_pids must observe the service's separate session waiting before
that policy commits. No service method or database lock is monkeypatched.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.accounting.domain import AccountingError
from app.b04_close.models import CloseCommand
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from test_p0_close_upload_postgres import (
    _await_blocked,
    _reviewed_close,
    _runtime_transaction,
    p0_case,
    p0_pg,
)


def transition(c, operation, report_id, version):
    try:
        return getattr(ReportService(c.db), operation)(
            c.reviewer,
            report_id,
            ReportCommand(expected_version=version, reason="P0真实并发复核发布"),
        )
    except AccountingError as error:
        return error


@pytest.mark.parametrize("operation", ["review", "publish"])
def test_real_pg_report_rechecks_committed_policy_after_waiting_for_write_gate(p0_case, operation):
    c = p0_case
    prepared = _reviewed_close(c)
    reports = ReportService(c.db)
    if operation == "review":
        report = reports.create(c.manager, c.book["id"], "2026-10")
        report_id = report["report"]["id"]
        version = report["report"]["version"]
        reports.validate(c.manager, report_id)
        expected_status = "validated"
    else:
        prepared.close.lock(
            c.manager,
            prepared.run["id"],
            CloseCommand(expected_version=prepared.run["version"], reason="P0真实锁期"),
        )
        report_id = prepared.report_id
        with c.db.transaction(actor=c.manager) as conn:
            version = conn.execute(
                "SELECT version FROM report_runs WHERE id=?", (report_id,)
            ).fetchone()[0]
        expected_status = "reviewed"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with _runtime_transaction(c, c.manager) as writer:
            writer.execute(
                "SELECT accounting.begin_company_write(%s,%s)",
                (c.manager.firm_id, c.book["company_id"]),
            )
            writer.execute(
                "UPDATE accounting_policies SET industry='P0真实并发政策变更' WHERE ledger_id=%s",
                (prepared.ledger_id,),
            )
            blocker = writer.execute("SELECT pg_backend_pid()").fetchone()[0]
            pending = pool.submit(transition, c, operation, report_id, version)
            _await_blocked(c, blocker, pending)
        outcome = pending.result(timeout=15)

    assert isinstance(outcome, AccountingError), outcome
    assert outcome.status == 409
    assert "输入已变化" in outcome.detail
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT status,reviewed_by,published_by FROM report_runs WHERE id=?", (report_id,)
        ).fetchone()
        assert row["status"] == expected_status
        assert row["published_by"] is None
        if operation == "review":
            assert row["reviewed_by"] is None
