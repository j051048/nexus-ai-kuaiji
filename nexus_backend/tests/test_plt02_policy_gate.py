"""PLT02: no active policy must never produce a formal report."""

from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.service import ReportService
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


def _prepared_case(case):
    opening = frozen_opening(case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="PLT02 合成期初绑定",
        ),
    )
    return opening


def test_report_without_active_policy_is_trial_only(business):
    case = business
    _prepared_case(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET status='draft' "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)",
            (case.book["id"],),
        )
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["mode"] == "trial"
    assert "缺少生效的核算政策" in report["report"]["trial_reasons"]


def test_report_with_active_policy_still_uses_its_standard(business):
    case = business
    _prepared_case(case)
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["template_id"] == "small_enterprise_basic"
    assert report["report"]["mode"] == "formal"
