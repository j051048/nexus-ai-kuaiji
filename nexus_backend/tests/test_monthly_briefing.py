"""锁期后的一页纸财税简报：精确聚合、权限边界与锁期前置。"""

from uuid import uuid4

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import (
    ApprovalRequest,
    BookMemberGrant,
    ExportRequest,
    ImportRequest,
    SourceRow,
)
from app.accounting.seed import LOCAL_ACTOR
from app.closing.models import CloseRequest, PreflightRequest, SnapshotReview
from app.closing.service import ClosingService
from app.delivery.models import ReceiptCreate
from app.delivery.service import DeliveryService
from app.monthly.briefing import BriefingService
from app.monthly.models import BaselineCreate, CheckCreate, MonthlyReason
from app.monthly.service import MonthlyService
from test_catalog import case
from test_stage9_delivery import facts


def prepare_book(accounting, book):
    """导入、审核、导出并核验回执，使月结预检只剩锁期本身。"""
    delivery = DeliveryService(accounting.db)
    accounting.import_sources(
        LOCAL_ACTOR,
        book,
        ImportRequest(
            source_name="虚构银行流水",
            rows=[
                SourceRow(
                    external_id="brief-in",
                    date="2026-10-05",
                    description="销售收款",
                    counterparty="客户甲",
                    amount="1000.00",
                    direction="income",
                    evidence_ref="银行流水原件",
                ),
                SourceRow(
                    external_id="brief-out",
                    date="2026-10-06",
                    description="办公用品采购",
                    counterparty="供方乙",
                    amount="300.00",
                    direction="expense",
                    evidence_ref="银行流水原件",
                ),
            ],
        ),
    )
    accounting.suggestions(LOCAL_ACTOR, book)
    drafts = accounting.get_book(LOCAL_ACTOR, book)["drafts"]
    approved = []
    for draft in drafts:
        accounting.approve(
            LOCAL_ACTOR, book, draft["id"], ApprovalRequest(expected_version=draft["version"])
        )
        approved.append(accounting.get_draft(LOCAL_ACTOR, book, draft["id"]))
    receipt = accounting.export(
        LOCAL_ACTOR,
        book,
        ExportRequest(draft_ids=[draft["id"] for draft in drafts], idempotency_key=uuid4().hex),
    )
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        delivery.capture_export_in_transaction(
            conn,
            LOCAL_ACTOR,
            accounting.access.book(conn, LOCAL_ACTOR, book),
            receipt["id"],
            approved,
        )
    body = facts(delivery, book, receipt["id"])
    for index, item in enumerate(body["items"], start=1):
        # 每条导出凭证需要独立的外部凭证号，回执才被视为内容一致。
        item["external_voucher_id"] = f"客户凭证-{index:03d}"
    delivery.create_receipt(
        LOCAL_ACTOR,
        book,
        receipt["id"],
        ReceiptCreate(**body, idempotency_key=uuid4().hex, reason="客户财务提供的合成回执，逐行核对"),
    )


def close_book(accounting, book):
    closing = ClosingService(accounting.db)
    snapshot = closing.preflight(
        LOCAL_ACTOR, book, PreflightRequest(idempotency_key=uuid4().hex)
    )
    assert snapshot["report"]["passed"], snapshot["report"]
    closing.review(
        LOCAL_ACTOR,
        book,
        snapshot["id"],
        SnapshotReview(reason="核实本期资料与银行流水完整并复核通过", materials_confirmed=True),
    )
    closing.close(
        LOCAL_ACTOR,
        book,
        CloseRequest(snapshot_id=snapshot["id"], reason="依据已复核快照完成月结"),
    )


def lock_book(accounting, book):
    prepare_book(accounting, book)
    close_book(accounting, book)


def test_briefing_requires_locked_period_and_aggregates_exact_cents(case):
    accounting, _, book = case
    service = BriefingService(accounting.db, accounting)
    with pytest.raises(AccountingError) as locked:
        service.briefing(LOCAL_ACTOR, book)
    assert locked.value.status == 409
    prepare_book(accounting, book)
    # 期末参考余额必须在期间开放时录入、经独立确认并形成已核对的月度检查。
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute(
            "INSERT INTO users(id,name,email) VALUES('brief-reviewer','独立复核余额','brief-reviewer@example.test')"
        )
        conn.execute(
            "INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,'reviewer')",
            (LOCAL_ACTOR.firm_id, "brief-reviewer"),
        )
        conn.execute(
            "INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'reviewer')",
            (book, LOCAL_ACTOR.firm_id, "brief-reviewer"),
        )
    reviewer = Actor("brief-reviewer", LOCAL_ACTOR.firm_id)
    monthly = MonthlyService(accounting.db, accounting)

    def confirmed_baseline(kind, lines):
        row = monthly.create_baseline(
            LOCAL_ACTOR,
            book,
            BaselineCreate(
                kind=kind,
                external_reference="客户提供单月已记账余额表",
                evidence_ref="私有原件:合成余额表",
                reason="仅核对客户提供的科目余额",
                lines=lines,
                idempotency_key=uuid4().hex,
            ),
        )
        return monthly.confirm_baseline(
            reviewer, book, row["id"], MonthlyReason(reason="独立核对客户所提供余额")
        )

    opening = confirmed_baseline(
        "opening",
        [
            {"account_code": "1122", "balance": "1500.00"},
            {"account_code": "1002", "balance": "300.00"},
        ],
    )
    closing = confirmed_baseline(
        "closing",
        [
            {"account_code": "1122", "balance": "1500.00"},
            {"account_code": "2202", "balance": "800.00"},
            {"account_code": "1002", "balance": "700.00"},
        ],
    )
    check = monthly.create_check(
        LOCAL_ACTOR,
        book,
        CheckCreate(
            opening_id=opening["id"], closing_id=closing["id"], idempotency_key=uuid4().hex
        ),
    )
    monthly.close_check(
        LOCAL_ACTOR,
        book,
        check["id"],
        MonthlyReason(reason="客户只提供部分资料，差异保留后续追查"),
    )
    close_book(accounting, book)
    result = service.briefing(LOCAL_ACTOR, book)
    assert result["book"]["period_status"] == "closed" and result["book"]["period"] == "2026-10"
    assert result["flows"] == {
        "income": "1000.00",
        "expense": "300.00",
        "net": "700.00",
        "basis": "按银行来源流水的收入 / 支出合计",
    }
    assert len(result["top_expenses"]) == 1
    assert result["top_expenses"][0]["amount"] == "300.00"
    assert result["balances"]["receivable"] == "1500.00"
    assert result["balances"]["payable"] == "800.00"
    assert result["balances"]["basis"] == "人工确认的期末参考余额"
    assert result["tax"]["change"] == "0.00"
    assert result["risks"] == []
    assert any("不是财务报表" in note for note in result["notes"])


def test_briefing_scope_and_capability_boundaries(case):
    accounting, _, book = case
    service = BriefingService(accounting.db, accounting)
    lock_book(accounting, book)
    directory = DirectoryService(accounting.db)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        company_id = conn.execute(
            "SELECT company_id FROM books WHERE id=?", (book,)
        ).fetchone()["company_id"]
        conn.execute(
            "INSERT INTO users(id,name,email) VALUES('boss','企业老板','boss@example.test')"
        )
        conn.execute(
            "INSERT INTO users(id,name,email) VALUES('outsider','无关人员','outsider@example.test')"
        )
        conn.execute(
            "INSERT INTO company_memberships(company_id,user_id,role,department_id) VALUES(?,?,'admin',NULL)",
            (company_id, "boss"),
        )
    directory.grant_book_member(LOCAL_ACTOR, book, "boss", BookMemberGrant(role="editor"))
    assert service.briefing(Actor("boss", LOCAL_ACTOR.firm_id), book)["flows"]["net"] == "700.00"
    with pytest.raises(AccountingError) as missing:
        service.briefing(Actor("outsider", LOCAL_ACTOR.firm_id), book)
    assert missing.value.status == 404
