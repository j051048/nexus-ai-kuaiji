"""PLT07-A / PLT07-B / PLT07-D: shared-service queue lanes, backlog attribution, and trustworthy firm insights."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate, BookMemberGrant, CompanyCreate
from app.observability.insights import FirmInsightsService
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.workspace.queue_lanes import (
    QUEUE_LANE_RULE_VERSION,
    attribute_backlog,
    classify_backlog_category,
    classify_task_lane,
    partition_queue_lanes,
)
from app.workspace.service import WorkspaceService
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


def test_queue_lanes_partition_and_honest_backlog_eta():
    today = "2026-10-15"
    tasks = [
        {
            "key": "t1",
            "kind": "fee_variance",
            "bucket": "action",
            "due_on": "2026-10-14",
            "responsible": "张会计",
            "state": "pending",
        },
        {
            "key": "t2",
            "kind": "review",
            "bucket": "action",
            "due_on": "2026-10-15",
            "responsible": "李复核",
            "state": "draft",
        },
        {
            "key": "t3",
            "kind": "material",
            "bucket": "action",
            "due_on": "2026-10-18",
            "responsible": "王出纳",
            "state": "pending",
        },
        {
            "key": "t4",
            "kind": "bridge_return",
            "bucket": "action",
            "due_on": "2026-10-25",
            "responsible": "李复核",
            "state": "pending",
        },
        {
            "key": "t5",
            "kind": "case",
            "bucket": "action",
            "due_on": None,
            "responsible": "未分配",
            "state": "open",
        },
        {
            "key": "t6",
            "kind": "approval",
            "bucket": "waiting",
            "due_on": "2026-10-14",
            "responsible": "业务主管",
            "state": "awaiting_info",
        },
    ]

    assert classify_task_lane(tasks[0], today=today) == "must_today"
    assert classify_task_lane(tasks[1], today=today) == "must_today"
    assert classify_task_lane(tasks[2], today=today) == "due_soon"
    assert classify_task_lane(tasks[3], today=today) == "scheduled"
    assert classify_task_lane(tasks[4], today=today) == "no_due_date"
    assert classify_task_lane(tasks[5], today=today) == "waiting_others"

    partition = partition_queue_lanes(tasks, today=today)
    assert partition["rule_version"] == QUEUE_LANE_RULE_VERSION
    assert partition["counts"] == {
        "must_today": 2,
        "due_soon": 1,
        "waiting_others": 1,
        "no_due_date": 1,
        "scheduled": 1,
    }
    assert sum(partition["counts"].values()) == len(tasks)

    assert classify_backlog_category(tasks[0]) == "shared_center"
    assert classify_backlog_category(tasks[2]) == "business_dept"
    assert classify_backlog_category(tasks[3]) == "external_system"
    assert classify_backlog_category(tasks[4]) == "unassigned"
    assert classify_backlog_category(tasks[5]) == "business_dept"

    # 样本不足 5 条不给虚构 ETA；满 5 条按中位数给出并注明样本量
    summary = attribute_backlog(
        tasks,
        sample_durations_minutes={
            "business_dept": [10, 20, 30],  # 仅 3 条，样本不足
            "shared_center": [15, 25, 30, 45, 60],  # 5 条，中位数 30
        },
    )
    by_code = {item["code"]: item for item in summary}
    assert by_code["business_dept"]["count"] == 2
    assert by_code["business_dept"]["eta_minutes"] is None
    assert "样本不足" in by_code["business_dept"]["eta_label"]

    assert by_code["shared_center"]["count"] == 2
    assert by_code["shared_center"]["eta_minutes"] == 60.0
    assert "5" in by_code["shared_center"]["eta_label"]

    assert by_code["external_system"]["count"] == 1
    assert by_code["unassigned"]["count"] == 1


def test_workspace_service_due_soon_bucket_and_lane_metadata(business):
    from app.accounting.review import ReviewService
    from app.cases.models import CaseCreate
    from app.cases.service import CaseService
    from test_business import approved, handoff

    case = business
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    soon_due = (today + timedelta(days=2)).isoformat()
    later_due = (today + timedelta(days=10)).isoformat()
    ev1 = approved(case, description="合成到期核对事项")
    ev2 = approved(case, description="合成远期核对事项")
    h1 = handoff(case, ev1)
    h2 = handoff(case, ev2)
    cases = CaseService(case.db, case.service)
    c1 = cases.create(
        case.manager,
        CaseCreate(
            book_id=case.book["id"],
            event_id=ev1["id"],
            draft_id=h1["draft_id"],
            kind="exception",
            title="即将到期对账差异",
            reason="合成待办核对",
            priority="urgent",
            due_on=soon_due,
            idempotency_key="plt07-case-soon-0001",
        ),
    )
    c2 = cases.create(
        case.manager,
        CaseCreate(
            book_id=case.book["id"],
            event_id=ev2["id"],
            draft_id=h2["draft_id"],
            kind="exception",
            title="计划内对账事项",
            reason="合成计划核对",
            priority="normal",
            due_on=later_due,
            idempotency_key="plt07-case-later-0002",
        ),
    )

    ws = WorkspaceService(
        case.db,
        case.ledger,
        case.service,
        ReviewService(case.db, case.ledger),
        cases,
    )
    all_action = ws.list(case.manager, bucket="action", use_projection=False)
    assert set(all_action["counts"].keys()).issubset({"action", "waiting"})
    assert all_action["queue_lanes"]["rule_version"] == QUEUE_LANE_RULE_VERSION
    assert all_action["queue_lanes"]["due_soon_total"] >= 1
    assert any(item["ref"] == c1["id"] and item["lane"] == "due_soon" for item in all_action["tasks"])
    assert any(item["ref"] == c2["id"] and item["lane"] == "scheduled" for item in all_action["tasks"])

    due_only = ws.list(case.manager, bucket="due_soon", use_projection=False)
    refs = {item["ref"] for item in due_only["tasks"]}
    assert c1["id"] in refs
    assert c2["id"] not in refs


def _prepare_reviewed_book(case, company, book, period: str, standard_kind: str, *, make_legacy: bool = False):
    case.directory.grant_book_member(
        case.manager, book["id"], case.actors["accountant"].user_id, BookMemberGrant(role="reviewer")
    )
    sub_case = type("SubCase", (), {**vars(case), "company": company, "book": book})()
    opening = frozen_opening(sub_case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET standard_kind=? "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)",
            (standard_kind, book["id"]),
        )
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=book["id"],
            package_version_id=opening["version_id"],
            reason="PLT07-D 合成期初",
        ),
    )
    reports = ReportService(case.db)
    created = reports.create(case.manager, book["id"], period)["report"]
    reports.validate(case.manager, created["id"])
    reports.review(
        case.actors["accountant"],
        created["id"],
        ReportCommand(expected_version=created["version"], reason="独立复核"),
    )
    if make_legacy:
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE report_runs SET policy_version=NULL, policy_approver=NULL WHERE id=?",
                (created["id"],),
            )
    return created


def test_firm_insights_marks_missing_mixed_standards_legacy_and_blocks_non_manager(business):
    case = business
    # 共 3 家企业：case.book（缺报/仅草稿）、case.other_book（小企业已复核）、新建一般企业（企业准则已复核且模拟历史报表）
    _prepare_reviewed_book(case, case.other, case.other_book, "2026-10", "small_enterprise")
    third_company = case.directory.create_company(case.manager, CompanyCreate(name="合成汽服一般企业公司"))
    third_book = case.ledger.create_book(
        case.manager, BookCreate(name="合成汽服账套", period="2026-10", company_id=third_company["id"])
    )
    _prepare_reviewed_book(case, third_company, third_book, "2026-10", "enterprise", make_legacy=True)

    # 给默认第 1 家留一份仅草稿/未复核的报表，检查其不被误计入已复核汇总
    opening = frozen_opening(case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="缺报单位合成期初",
        ),
    )
    ReportService(case.db).create(case.manager, case.book["id"], "2026-10")

    insights = FirmInsightsService(case.db)
    data = insights.overview(case.manager, "2026-10")
    fin = data["financial"]

    assert fin["known_books"] == 2
    assert fin["missing_books"] == 1
    assert fin["complete"] is False
    assert "已纳入 2/3 家" in fin["coverage_label"]
    assert case.company["name"] in fin["coverage_label"]
    assert "管理汇总，非合并财务报表" in fin["management_summary_notice"]

    assert fin["mixed_standards"] is True
    assert fin["totals_comparable"] is False
    assert "多种会计准则" in fin["totals_notice"]
    breakdown_kinds = {row["standard_kind"] for row in fin["standards_breakdown"]}
    assert breakdown_kinds == {"small_enterprise", "enterprise"}

    assert fin["legacy_books"] == 1
    assert "历史报表" in fin["coverage_label"]
    assert len(fin["missing_companies"]) == 1
    assert fin["missing_companies"][0]["company_name"] == case.company["name"]
    assert fin["missing_companies"][0]["has_trial_draft"] is True

    # 普通会计/员工无集团管理权限，不得访问汇总反查他企数据
    for actor in (case.actors["accountant"], case.actors["employee"]):
        with pytest.raises(AccountingError) as exc:
            insights.overview(actor, "2026-10")
        assert exc.value.status == 403
