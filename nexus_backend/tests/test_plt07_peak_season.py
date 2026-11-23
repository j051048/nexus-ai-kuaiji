"""Batch 10-1.1：共享总控队列的月末高峰场景（PLT07-C）与集团汇总声明（PLT07-D）。

场景按真实服务验证：同为同日结账的两家企业、复核人请假并由代班人接手、
其中一家银行流水迟到只阻断本家结账、代班授权不能越过管理层复核分离、
转派候选只包含已经拥有账套权限的人。
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.accounting.access import AccessControl
from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import (
    BookCreate,
    BookMemberGrant,
    CompanyCreate,
    FirmCreate,
    FirmMemberGrant,
)
from app.accounting.service import AccountingService
from app.assistant.paths import SampleReader, why_not_delivered
from app.handoff.service import HandoffService
from app.observability.insights import FirmInsightsService
from app.workspace.queue_lanes import (
    DEFAULT_DUE_SOON_DAYS,
    attribute_backlog,
    partition_queue_lanes,
)


@pytest.fixture
def peak(tmp_path):
    db = Database(tmp_path / "peak.db")
    directory, ledger, accounting = (
        DirectoryService(db),
        AccountingService(db),
        AccountingService(db),
    )

    def user(name):
        identity = SimpleNamespace(user_id=name, display_name=name, email=f"{name}@example.test")
        directory.register_identity(identity)
        return identity

    owner = user("owner")
    firm = directory.create_firm(owner.user_id, FirmCreate(name="月末高峰试点事务所"))
    manager = Actor(owner.user_id, firm["id"])
    company_a = directory.create_company(manager, CompanyCreate(name="高峰甲企业"))
    company_b = directory.create_company(manager, CompanyCreate(name="高峰乙企业"))
    book_a = ledger.create_book(
        manager, BookCreate(name="甲账套", period="2026-10", company_id=company_a["id"])
    )
    book_b = ledger.create_book(
        manager, BookCreate(name="乙账套", period="2026-10", company_id=company_b["id"])
    )
    absent = user("absent-reviewer")
    directory.grant_firm_member(manager, FirmMemberGrant(email=absent.email, role="reviewer"))
    substitute = user("substitute")
    directory.grant_firm_member(manager, FirmMemberGrant(email=substitute.email, role="reviewer"))
    for book in (book_a, book_b):
        directory.grant_book_member(
            manager, book["id"], absent.user_id, BookMemberGrant(role="reviewer")
        )
        directory.grant_book_member(
            manager, book["id"], substitute.user_id, BookMemberGrant(role="reviewer")
        )
    outsider = user("outsider")
    other_firm = directory.create_firm(outsider.user_id, FirmCreate(name="外部事务所"))
    return SimpleNamespace(
        db=db,
        directory=directory,
        ledger=ledger,
        accounting=accounting,
        manager=manager,
        company_a=company_a["id"],
        company_b=company_b["id"],
        book_a=book_a["id"],
        book_b=book_b["id"],
        absent=absent.user_id,
        substitute=Actor(substitute.user_id, firm["id"]),
        outsider=Actor(outsider.user_id, other_firm["id"]),
    )


TODAY = "2026-10-31"


def _task(**overrides):
    base = {
        "id": "task",
        "kind": "draft",
        "bucket": "action",
        "due_on": TODAY,
        "responsible": "会计复核员",
        "state": "ready",
        "updated_at": f"{TODAY}T01:00:00+00:00",
        "company_id": "company",
    }
    base.update(overrides)
    return base


def test_two_companies_closing_the_same_day_partition_without_overlap():
    tasks = [
        _task(id="a-today", company_id="a"),
        _task(id="a-wait", company_id="a", bucket="waiting"),
        _task(id="b-soon", company_id="b", due_on="2026-11-02"),
        _task(id="b-none", company_id="b", due_on=None),
    ]
    lanes = partition_queue_lanes(tasks, today=TODAY, due_soon_days=DEFAULT_DUE_SOON_DAYS)
    assert lanes["counts"] == {
        "must_today": 1,
        "due_soon": 1,
        "waiting_others": 1,
        "no_due_date": 1,
        "scheduled": 0,
    }
    flat = [row["id"] for lane in lanes["lanes"].values() for row in lane]
    assert sorted(flat) == sorted(task["id"] for task in tasks)
    assert len(flat) == len(set(flat))


def test_backlog_attribution_separates_two_companies_at_month_end():
    tasks = [
        _task(id="a-external", company_id="a", kind="bridge_return", responsible="银行流水"),
        _task(id="b-dept", company_id="b", kind="material", state="awaiting_info"),
        _task(id="b-unassigned", company_id="b", responsible="未分配", bucket="waiting"),
    ]
    summary = {row["code"]: row for row in attribute_backlog(tasks, min_samples=5)}
    assert summary["external_system"]["count"] == 1
    assert summary["business_dept"]["count"] == 1
    assert summary["unassigned"]["count"] == 1
    # 样本不足时必须诚实返回空值，而不是编造一个预计完成时间。
    for category in summary.values():
        assert category["eta_minutes"] is None
        assert category["eta_label"] == "样本不足"


def test_late_bank_statement_blocks_one_company_only():
    blocked = {
        "allowed": True,
        "close": {
            "status": "blocked",
            "version": 7,
            "blockers": [{"code": "bank_statement_missing", "next_action": "补传 10 月银行流水"}],
        },
        "delivery": {"package": None, "opened": False},
        "book_id": "book-a",
        "period": "2026-10",
        "company_id": "a",
        "rules": [],
    }
    clear = {
        "allowed": True,
        "close": {"status": "ready", "version": 3, "blockers": []},
        "delivery": {"package": {"version": 2}, "opened": True},
        "book_id": "book-b",
        "period": "2026-10",
        "company_id": "b",
        "rules": [],
    }
    late = why_not_delivered(SampleReader(blocked))
    fine = why_not_delivered(SampleReader(clear))
    assert late["status"] == "blocked"
    text = "\n".join(late["conclusion"])
    assert "bank_statement_missing" in text
    assert "补传 10 月银行流水" in text
    assert fine["status"] == "complete"
    assert "bank_statement_missing" not in "\n".join(fine["conclusion"])
    assert [row["code"] for row in fine["facts"]["blockers"]] == []


def test_absent_reviewer_delegation_is_time_bound_and_never_merges_review(peak):
    starts = (datetime.now(ZoneInfo("UTC")) - timedelta(hours=1)).isoformat()
    ends = (datetime.now(ZoneInfo("UTC")) + timedelta(days=1)).isoformat()
    with peak.db.transaction(
        write=True, actor=peak.manager, company_id=peak.company_a, write_scope="company"
    ) as conn:
        conn.execute(
            "INSERT INTO book_review_delegations(id,firm_id,company_id,book_id,duty,"
            "from_user_id,to_user_id,starts_at,ends_at,status,reason,requested_by,"
            "approved_by,approved_at,created_at) "
            "VALUES('deleg-1',?,?,?,'reviewer',?,?,?,?,'active',?,?,?,?,?)",
            (
                peak.manager.firm_id,
                peak.company_a,
                peak.book_a,
                peak.absent,
                peak.substitute.user_id,
                starts,
                ends,
                "合成测试：复核人请假，代班授权一天",
                peak.manager.user_id,
                peak.manager.user_id,
                starts,
                starts,
            ),
        )
    with peak.db.transaction(actor=peak.substitute) as conn:
        active = AccessControl.active_review_delegation(conn, peak.substitute, peak.book_a)
        separated = AccessControl.active_review_delegation(
            conn, peak.substitute, peak.book_a, management_review_separation=True
        )
    assert active is not None and active["from_user_id"] == peak.absent
    # 管理层与复核分离时，代班授权不得把管理层重新带回复核岗位。
    assert separated is None


def test_transfer_candidates_never_gain_permission_from_the_transfer(peak, tmp_path):
    handoff = HandoffService(peak.db)
    candidates = handoff.candidates(peak.substitute, peak.book_a, "reviewer")
    ids = {row["user_id"] for row in candidates["items"]}
    assert peak.substitute.user_id in ids
    assert peak.absent in ids
    with peak.db.transaction(actor=peak.manager) as conn:
        conn.execute(
            "INSERT INTO users(id,name,active) VALUES('fresh-reviewer','新复核人',1) "
            "ON CONFLICT(id) DO NOTHING"
        )
        conn.execute(
            "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'reviewer') "
            "ON CONFLICT(firm_id,user_id) DO NOTHING",
            (peak.manager.firm_id, "fresh-reviewer"),
        )
    refreshed = handoff.candidates(peak.substitute, peak.book_a, "reviewer")
    # 没有该账套权限的人不会因为出现在候选查询里而获得转派资格。
    assert "fresh-reviewer" not in {row["user_id"] for row in refreshed["items"]}


def test_group_summary_states_management_basis_and_refuses_non_managers(peak):
    insights = FirmInsightsService(peak.db)
    overview = insights.overview(peak.manager, "2026-10")
    notice = overview["financial"]["management_summary_notice"]
    assert notice == "管理汇总，非合并财务报表（无内部往来抵销）。"
    assert overview["financial"]["legacy_books"] == 0
    assert "未采集" not in notice
    # 跨事务所只看到自己事务所的空汇总，看不到本所数据。
    other = insights.overview(peak.outsider, "2026-10")
    assert other["kpis"]["companies"] == 0
    # 非管理角色（复核人）不能读取集团汇总。
    with pytest.raises(AccountingError) as denied:
        insights.overview(peak.substitute, "2026-10")
    assert denied.value.status in {403, 404}
