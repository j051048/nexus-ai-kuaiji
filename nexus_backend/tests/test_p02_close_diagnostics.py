"""Blocked preflight must retain evidence and tell its real operator what to do."""

from app.b04_close.models import ClosePreflight
from app.b04_close.service import CloseService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from test_b04_pilot import setup_b03
from test_business import business  # noqa: F401
from test_posting_service import admin


def test_missing_subledger_configuration_has_reason_and_scoped_next_step(business):
    case = business
    setup_b03(case)
    reports = ReportService(case.db)
    report = reports.create(case.manager, case.book["id"], "2026-10")
    rid = report["report"]["id"]
    reports.validate(case.manager, rid)
    reports.review(admin(case), rid, ReportCommand(expected_version=1, reason="独立复核"))
    run = CloseService(case.db).preflight(
        case.manager,
        ClosePreflight(
            book_id=case.book["id"],
            period="2026-10",
            report_id=rid,
            idempotency_key="p02-diagnostic-001",
            reason="检查未配置的真实阻断",
        ),
    )
    assert run["close"]["status"] == "preflight_failed"
    check = next(c for c in run["checks"] if c["code"] == "subledger_control")
    assert isinstance(check["detail"], dict), "rows=[] must not discard the blocking reason"
    assert "配置" in check["detail"]["reason"]
    assert check["detail"]["passed"] is False
    assert check["owner_role"] == "账套会计"
    assert check["next_action"]
    assert check["action_target"] == {
        "kind": "subledger",
        "book_id": case.book["id"],
        "period": "2026-10",
    }
    for item in run["checks"]:
        if item["status"] == "blocked":
            assert item["owner_role"] and item["next_action"]
            assert item["action_target"]["book_id"] == case.book["id"]
