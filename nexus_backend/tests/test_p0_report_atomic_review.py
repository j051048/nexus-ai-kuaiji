"""Reproduce mutations committed just before report review/publication write.

The hook performs a real independent transaction before the command acquires
its write gate. An early read from a previous transaction must not authorize
its later transition. PostgreSQL lock behavior is exercised separately.
"""

from contextlib import contextmanager

import pytest

from app.accounting.domain import AccountingError
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from test_business import business
from test_p0_close_dependencies import close_case, command


def install_before_write(db, actor, mutation, monkeypatch):
    original = db.transaction
    applied = []

    @contextmanager
    def transaction(*args, **kwargs):
        if kwargs.get("write") and kwargs.get("actor") == actor and not applied:
            applied.append(True)
            with original(write=True, actor=actor) as conn:
                mutation(conn)
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(db, "transaction", transaction)
    return applied


@pytest.mark.parametrize("operation", ["review", "publish"])
def test_report_transition_revalidates_inputs_inside_its_write_transaction(
    close_case, monkeypatch, operation
):
    case = close_case
    reports = ReportService(case.db)
    if operation == "review":
        report = reports.create(case.manager, case.book["id"], "2026-10")
        reports.validate(case.manager, report["report"]["id"])
        report_id = report["report"]["id"]
        version = report["report"]["version"]
        expected_status = "validated"
    else:
        case.close.review(case.reviewer, case.run["close"]["id"], command())
        case.close.lock(case.reviewer, case.run["close"]["id"], command())
        report_id = case.report["report"]["id"]
        version = case.report["report"]["version"]
        expected_status = "reviewed"

    applied = install_before_write(
        case.db,
        case.reviewer,
        lambda conn: conn.execute(
            "UPDATE accounting_policies SET industry='虚构政策在确认前变更' WHERE ledger_id=?",
            (case.opening["ledger_id"],),
        ),
        monkeypatch,
    )
    with pytest.raises(AccountingError, match="报表输入已变化") as rejected:
        getattr(reports, operation)(
            case.reviewer,
            report_id,
            ReportCommand(
                expected_version=version,
                reason="复核发布必须重验实际输入",
            ),
        )
    assert rejected.value.status == 409
    assert applied == [True]
    with case.db.transaction(actor=case.manager) as conn:
        assert (
            conn.execute("SELECT status FROM report_runs WHERE id=?", (report_id,)).fetchone()[0]
            == expected_status
        )


@pytest.mark.parametrize("operation", ["review", "publish"])
def test_report_transition_rechecks_current_identity_in_write_transaction(
    close_case, monkeypatch, operation
):
    case = close_case
    reports = ReportService(case.db)
    if operation == "review":
        report = reports.create(case.manager, case.book["id"], "2026-10")
        reports.validate(case.manager, report["report"]["id"])
        report_id = report["report"]["id"]
        version = report["report"]["version"]
    else:
        case.close.review(case.reviewer, case.run["close"]["id"], command())
        case.close.lock(case.reviewer, case.run["close"]["id"], command())
        report_id = case.report["report"]["id"]
        version = case.report["report"]["version"]
    install_before_write(
        case.db,
        case.reviewer,
        lambda conn: conn.execute(
            "UPDATE users SET active=0 WHERE id=?",
            (case.reviewer.user_id,),
        ),
        monkeypatch,
    )
    with pytest.raises(AccountingError) as rejected:
        getattr(reports, operation)(
            case.reviewer,
            report_id,
            ReportCommand(
                expected_version=version,
                reason="当前复核人失权必须阻断",
            ),
        )
    assert rejected.value.status in {403, 404}
