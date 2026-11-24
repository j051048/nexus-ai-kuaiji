"""Batch 10-1.2：集团关联方内部往来对平与合并抵消试算的真实事务验证。

覆盖内部应收/应付与收入/成本的逐笔对平、未达差额、多准则不混加、抵消分录
自审 403 与独立复核生效、单体账簿只在只读事务中被读取，以及跨事务所隔离。
"""

from types import SimpleNamespace

import pytest

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import (
    BookCreate,
    CompanyCreate,
    CompanyMemberGrant,
    FirmCreate,
)
from app.accounting.service import AccountingService
from app.reports import consolidation
from app.reports.consolidation import ConsolidationService


@pytest.fixture
def group(tmp_path):
    db = Database(tmp_path / "consolidation.db")
    directory, ledger, accounting = (
        DirectoryService(db),
        AccountingService(db),
        AccountingService(db),
    )
    service = ConsolidationService(db, accounting)

    def user(name):
        identity = SimpleNamespace(user_id=name, display_name=name, email=f"{name}@example.test")
        directory.register_identity(identity)
        return identity

    owner = user("owner")
    firm = directory.create_firm(owner.user_id, FirmCreate(name="集团试点事务所"))
    manager = Actor(owner.user_id, firm["id"])
    company_a = directory.create_company(manager, CompanyCreate(name="集团甲公司"))
    company_b = directory.create_company(manager, CompanyCreate(name="集团乙公司"))
    book_a = ledger.create_book(
        manager, BookCreate(name="甲公司账套", period="2026-10", company_id=company_a["id"])
    )
    book_b = ledger.create_book(
        manager, BookCreate(name="乙公司账套", period="2026-10", company_id=company_b["id"])
    )
    reviewer_identity = user("reviewer")
    for company_id in (company_a["id"], company_b["id"]):
        directory.grant_company_member(
            manager,
            company_id,
            CompanyMemberGrant(email=reviewer_identity.email, role="admin"),
        )
    outsider_identity = user("outsider")
    other_firm = directory.create_firm(outsider_identity.user_id, FirmCreate(name="外部事务所"))
    return SimpleNamespace(
        db=db,
        service=service,
        manager=manager,
        reviewer=Actor(reviewer_identity.user_id, firm["id"]),
        outsider=Actor(outsider_identity.user_id, other_firm["id"]),
        company_a=company_a["id"],
        company_b=company_b["id"],
        book_a=book_a["id"],
        book_b=book_b["id"],
    )


def _line(company, counterparty, code, debit=0, credit=0):
    return {
        "company_id": company,
        "counterparty_company_id": counterparty,
        "code": code,
        "debit_cents": debit,
        "credit_cents": credit,
    }


def test_internal_receivable_and_payable_pair_up_to_zero(group):
    rows = [
        _line(group.company_a, group.company_b, "1122", debit=5_000_000),
        _line(group.company_b, group.company_a, "2202", credit=5_000_000),
    ]
    balances, skipped = consolidation.classify_lines(rows)
    pairs = consolidation.pair_up(balances)
    assert skipped == []
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["status"] == "balanced"
    assert pair["difference_cents"] == 0
    assert pair["matched"] == "50,000.00"
    assert pair["left"] == "50,000.00" and pair["right"] == "50,000.00"


def test_open_difference_and_one_sided_balances_are_named(group):
    rows = [
        _line(group.company_a, group.company_b, "1122", debit=5_000_000),
        _line(group.company_b, group.company_a, "2202", credit=4_965_000),
        _line(group.company_b, group.company_a, "6001", credit=8_000_000),
    ]
    balances, _ = consolidation.classify_lines(rows)
    pairs = consolidation.pair_up(balances)
    # 归并方向取决于两家企业 id 的大小，测试不假定顺序。
    receivable = [
        row
        for row in pairs
        if {row["kind"], row["counterpart_kind"]} == {"internal_receivable", "internal_payable"}
    ][0]
    assert receivable["status"] == "difference"
    assert receivable["difference_cents"] == 35_000
    assert receivable["difference"] == "350.00"
    # 乙公司确认了内部收入，但甲公司没有对应的内部成本：必须报未达，而不是互相抵消。
    revenue_side = [
        row
        for row in pairs
        if {row["kind"], row["counterpart_kind"]} == {"internal_revenue", "internal_cost"}
    ][0]
    assert revenue_side["status"] == "one_sided"
    assert revenue_side["matched_cents"] == 0
    assert revenue_side["difference_cents"] == 8_000_000


def test_unmapped_party_is_reported_not_guessed(group):
    rows = [
        {
            "company_id": group.company_a,
            "counterparty_company_id": "",
            "code": "1122",
            "debit_cents": 1_000,
            "credit_cents": 0,
        },
        _line(group.company_a, group.company_b, "1122", debit=-500),
    ]
    balances, skipped = consolidation.classify_lines(rows)
    assert balances == []
    assert {row["reason"] for row in skipped} == {"unmapped_party", "direction"}


def test_worksheet_never_mixes_accounting_standards(group):
    rows = [
        _line(group.company_a, group.company_b, "1122", debit=5_000_000),
        _line(group.company_b, group.company_a, "2202", credit=5_000_000),
    ]
    balances, _ = consolidation.classify_lines(rows)
    pairs = consolidation.pair_up(balances)
    sheet = consolidation.build_worksheet(
        requested=[group.company_a, group.company_b],
        companies=[
            {"company_id": group.company_a, "name": "集团甲公司"},
            {"company_id": group.company_b, "name": "集团乙公司"},
        ],
        standards={group.company_a: "small_enterprise", group.company_b: "enterprise"},
        pairs=pairs,
        eliminations=[],
    )
    assert sheet["pairs"] == []
    assert len(sheet["not_merged_pairs"]) == 1
    assert sheet["not_merged_pairs"][0]["reason"] == "standard_mismatch"
    assert {row["standard"] for row in sheet["standard_groups"]} == {
        "small_enterprise",
        "enterprise",
    }
    assert sheet["statement"] == consolidation.STATEMENT


def test_totals_are_recomputable_and_never_add_cross_standard_pairs():
    """多准则分组时顶层不混算总计，只留分准则可比小计；异准则往来一律单列。"""

    def pair(left, right, standard, matched, difference, status):
        return {
            "company_id": left,
            "counterparty_company_id": right,
            "standard": standard,
            "matched_cents": matched,
            "difference_cents": difference,
            "status": status,
        }

    scoped = [
        pair("a", "b", "small_enterprise", 5_000_000, 0, "balanced"),
        pair("c", "d", "enterprise", 3_000_000, 200_000, "difference"),
    ]
    not_merged = [
        pair("a", "c", None, 9_000_000, 9_000_000, "difference") | {"reason": "standard_mismatch"}
    ]
    totals, by_standard, not_merged_totals = consolidation.worksheet_totals(scoped, not_merged)

    assert totals["comparable"] is False
    assert totals["matched_cents"] is None
    assert totals["matched"] is None
    assert totals["difference_cents"] is None
    assert totals["difference"] is None
    assert totals["open_pairs"] == 1
    assert totals["pairs"] == 2
    assert "不混算总计" in totals["scope"]
    # 每个准则小计都能由该准则的明细精确复算。
    assert {row["standard"]: row["matched_cents"] for row in by_standard} == {
        "small_enterprise": 5_000_000,
        "enterprise": 3_000_000,
    }
    assert {row["standard"]: row["difference_cents"] for row in by_standard} == {
        "small_enterprise": 0,
        "enterprise": 200_000,
    }
    # 异准则往来金额单列，绝不进入顶层匹配金额。
    assert not_merged_totals["matched_cents"] == 9_000_000
    assert not_merged_totals["pairs"] == 1
    assert "未计入" in not_merged_totals["label"]

    # 单一会计准则时顶层才给出可比合计，且严格等于该准则明细之和。
    single_totals, single_by_standard, _ = consolidation.worksheet_totals(
        [scoped[0]], not_merged
    )
    assert single_totals["comparable"] is True
    assert single_totals["matched_cents"] == 5_000_000
    assert single_totals["matched"] == "50,000.00"
    assert single_totals["difference_cents"] == 0
    assert len(single_by_standard) == 1


def test_worksheet_totals_recompute_on_five_company_scope():
    """5 家应报法人（含 1 家未开账/无权、准则不同）：合计只按同口径分准则复算。"""

    companies = [
        {"company_id": "a", "name": "甲"},
        {"company_id": "b", "name": "乙"},
        {"company_id": "c", "name": "丙"},
        {"company_id": "e", "name": "戊"},
    ]
    standards = {
        "a": "small_enterprise",
        "b": "small_enterprise",
        "c": "enterprise",
        "e": "enterprise",
    }
    # 两家小企业准则：对平；两家企业准则：有 350.00 未达；甲与丙跨准则：不得合并。
    pairs = [
        {
            "company_id": "a",
            "counterparty_company_id": "b",
            "kind": "internal_receivable",
            "counterpart_kind": "internal_payable",
            "matched_cents": 5_000_000,
            "difference_cents": 0,
            "status": "balanced",
        },
        {
            "company_id": "c",
            "counterparty_company_id": "e",
            "kind": "internal_revenue",
            "counterpart_kind": "internal_cost",
            "matched_cents": 3_000_000,
            "difference_cents": 35_000,
            "status": "difference",
        },
        {
            "company_id": "a",
            "counterparty_company_id": "c",
            "kind": "internal_funding",
            "counterpart_kind": "internal_funding",
            "matched_cents": 9_000_000,
            "difference_cents": 9_000_000,
            "status": "difference",
        },
    ]
    sheet = consolidation.build_worksheet(
        requested=["a", "b", "c", "e", "d"],
        companies=companies,
        standards=standards,
        pairs=pairs,
        eliminations=[],
    )
    totals, by_standard, not_merged = consolidation.worksheet_totals(
        sheet["pairs"], sheet["not_merged_pairs"]
    )

    # 未开账/无权法人在 missing_companies 里点名，不会被静默丢弃。
    assert sheet["missing_companies"] == ["d"]
    assert len(sheet["included_companies"]) == 4
    # 跨准则那对只出现在 not_merged_pairs，且对平/未达明细附带中文名称供前端展示。
    assert [row["company_id"] for row in sheet["not_merged_pairs"]] == ["a"]
    assert sheet["open_differences"][0]["left_company_name"] == "丙"
    assert sheet["open_differences"][0]["right_company_name"] == "戊"
    assert len(sheet["pairs"]) == 2
    # 存在两套准则分组时顶层不混算；每个分准则小计可由对应明细精确复算。
    assert totals["comparable"] is False
    assert totals["matched_cents"] is None
    assert totals["difference_cents"] is None
    assert {row["standard"]: row["matched_cents"] for row in by_standard} == {
        "small_enterprise": 5_000_000,
        "enterprise": 3_000_000,
    }
    assert {row["standard"]: row["difference_cents"] for row in by_standard} == {
        "small_enterprise": 0,
        "enterprise": 35_000,
    }
    assert not_merged["matched_cents"] == 9_000_000


def test_elimination_requires_an_independent_reviewer(group):
    entry = group.service.create_elimination(
        group.manager,
        group.company_a,
        group.company_b,
        "2026-10",
        "internal_receivable",
        5_000_000,
        "合成测试：内部往来抵消",
    )
    assert entry["status"] == "pending"
    with pytest.raises(AccountingError) as failure:
        group.service.review_elimination(group.manager, entry["id"], True)
    assert failure.value.status == 403
    assert "不能复核自己" in failure.value.detail

    approved = group.service.review_elimination(
        group.reviewer, entry["id"], True, "独立复核：与双方对账一致"
    )
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == group.reviewer.user_id
    assert approved["state_version"] == 2

    with pytest.raises(AccountingError) as second:
        group.service.review_elimination(group.reviewer, entry["id"], False)
    assert second.value.status == 409


def test_pending_eliminations_are_not_applied_and_missing_companies_named(group):
    entry = group.service.create_elimination(
        group.manager,
        group.company_a,
        group.company_b,
        "2026-10",
        "internal_funding",
        1_200_000,
    )
    sheet = group.service.worksheet(group.manager, "2026-10", [group.company_a, group.company_b])
    assert sheet["elimination_totals"]["approved_cents"] == 0
    assert sheet["elimination_totals"]["pending_count"] == 1
    assert sheet["pending_eliminations"][0]["id"] == entry["id"]
    assert "尚未独立复核" in sheet["pending_eliminations"][0]["label"]

    group.service.review_elimination(group.reviewer, entry["id"], True)
    applied = group.service.worksheet(group.manager, "2026-10", [group.company_a, group.company_b])
    assert applied["elimination_totals"]["approved_cents"] == 1_200_000
    assert applied["elimination_totals"]["approved"] == "12,000.00"
    assert applied["elimination_totals"]["pending_count"] == 0


def test_unreadable_company_is_reported_as_missing_scope(group):
    sheet = group.service.worksheet(
        group.manager, "2026-10", [group.company_a, group.outsider.firm_id]
    )
    assert [row["company_id"] for row in sheet["included_companies"]] == [group.company_a]
    assert sheet["missing_companies"] == [group.outsider.firm_id]
    assert "非经审计" in sheet["statement_note"]


def test_worksheet_only_reads_and_never_writes(group, monkeypatch):
    calls = []
    original = group.db.transaction

    def spy(*args, **kwargs):
        calls.append(bool(kwargs.get("write")))
        return original(*args, **kwargs)

    before = _ledger_counts(group.db, group)
    monkeypatch.setattr(group.db, "transaction", spy)
    group.service.worksheet(group.manager, "2026-10", [group.company_a, group.company_b])
    monkeypatch.undo()
    after = _ledger_counts(group.db, group)
    assert calls and not any(calls)
    assert before == after


def _ledger_counts(db, group):
    with db.transaction(actor=group.manager) as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("vouchers", "voucher_lines", "consolidation_eliminations")
        }


def test_cross_firm_isolation_and_mapping_audit(group):
    with pytest.raises(AccountingError) as denied:
        group.service.parties(group.outsider, group.company_a)
    assert denied.value.status in {403, 404}

    with pytest.raises(AccountingError) as self_link:
        group.service.save_party(
            group.manager, group.company_a, "乙公司", "集团乙公司", group.company_a
        )
    assert self_link.value.status == 422

    party = group.service.save_party(
        group.manager, group.company_a, "Y-GROUP", "集团乙公司", group.company_b
    )
    assert party["party_key"] == "Y-GROUP"
    with group.db.transaction(actor=group.manager) as conn:
        audited = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='consolidation.party.save'"
        ).fetchone()[0]
        stored = conn.execute(
            "SELECT counterparty_company_id FROM group_related_parties WHERE id=?",
            (party["id"],),
        ).fetchone()[0]
    assert audited == 1
    assert stored == group.company_b

    with pytest.raises(AccountingError) as forbidden:
        group.service.worksheet(group.outsider, "2026-10", [group.company_a])
    assert forbidden.value.status in {403, 404}


def test_elimination_content_is_frozen_after_review(group):
    entry = group.service.create_elimination(
        group.manager,
        group.company_a,
        group.company_b,
        "2026-10",
        "internal_revenue",
        2_500_000,
    )
    group.service.review_elimination(group.reviewer, entry["id"], True)
    with group.db.transaction(
        write=True, actor=group.manager, company_id=group.company_a, write_scope="company"
    ) as conn:
        with pytest.raises(Exception) as frozen:
            conn.execute(
                "UPDATE consolidation_eliminations SET amount_cents=1 WHERE id=?",
                (entry["id"],),
            )
        assert "frozen" in str(frozen.value).lower() or "already been reviewed" in str(frozen.value)
