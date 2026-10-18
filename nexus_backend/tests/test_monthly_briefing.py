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
from app.dispositions.service import SourceDispositionService
from app.monthly.briefing import BriefingService
from app.monthly.models import BaselineCreate, CheckCreate, MonthlyReason
from app.monthly.service import MonthlyService
from test_accounting import add_actor
from test_catalog import case
from test_stage10_dispositions import duplicates
from test_stage9_delivery import facts


def prepare_book(accounting, book, rows=None):
    """导入、审核、导出并核验回执，使月结预检只剩锁期本身。"""
    accounting.import_sources(
        LOCAL_ACTOR,
        book,
        ImportRequest(
            source_name="虚构银行流水",
            rows=rows
            if rows is not None
            else [
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
    approve_export_and_receipt(accounting, book, accounting.get_book(LOCAL_ACTOR, book)["drafts"])


def approve_export_and_receipt(accounting, book, drafts):
    delivery = DeliveryService(accounting.db)
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
        "basis": "按有效银行来源流水的收入 / 支出合计（已排除已隔离重复与替代前来源）",
    }
    assert len(result["top_expenses"]) == 1
    assert result["top_expenses"][0]["amount"] == "300.00"
    assert result["balances"]["receivable"] == "1500.00"
    assert result["balances"]["payable"] == "800.00"
    assert result["balances"]["basis"] == "人工确认的期末参考余额"
    assert result["tax"]["change"] == "0.00"
    assert result["risks"] == []
    assert any("不是财务报表" in note for note in result["notes"])


def test_briefing_negative_net_flow_is_signed_not_a_server_error(case):
    accounting, _, book = case
    prepare_book(
        accounting,
        book,
        rows=[
            SourceRow(
                external_id="brief-only-out",
                date="2026-10-06",
                description="办公用品采购",
                counterparty="供方乙",
                amount="300.00",
                direction="expense",
                evidence_ref="银行流水原件",
            )
        ],
    )
    close_book(accounting, book)
    result = BriefingService(accounting.db, accounting).briefing(LOCAL_ACTOR, book)
    assert result["flows"] == {
        "income": "0.00",
        "expense": "300.00",
        "net": "-300.00",
        "basis": "按有效银行来源流水的收入 / 支出合计（已排除已隔离重复与替代前来源）",
    }
    assert result["top_expenses"][0]["amount"] == "300.00"
    assert any("净流出" in risk["text"] for risk in result["risks"])


def test_briefing_excludes_isolated_duplicate_sources(case):
    accounting, _, book = case
    isolated, candidate, request = duplicates(accounting, book)
    SourceDispositionService(accounting.db).confirm(
        LOCAL_ACTOR, book, isolated["source_id"], candidate["id"], request
    )
    canonical = next(
        draft
        for draft in accounting.get_book(LOCAL_ACTOR, book)["drafts"]
        if draft["source_id"] == candidate["id"]
    )
    approve_export_and_receipt(accounting, book, [canonical])
    close_book(accounting, book)
    result = BriefingService(accounting.db, accounting).briefing(LOCAL_ACTOR, book)
    # Two duplicate bank rows of 0.30 exist, but only the canonical source is active.
    assert result["flows"]["expense"] == "0.30"
    assert result["flows"]["net"] == "-0.30"
    assert len(result["top_expenses"]) == 1
    assert result["top_expenses"][0]["amount"] == "0.30"


def test_briefing_expense_reversal_keeps_negative_sign(case):
    accounting, _, book = case
    prepare_book(accounting, book)
    close_book(accounting, book)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
            "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
            "VALUES('reversal-source',?,'reversal-001','虚构冲回','2026-10-20',"
            "'费用冲回','示例供方',40000,'expense','冲回凭证','%s','bank')"
            % ("a" * 64),
            (book,),
        )
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation,"
            "approved_by,approved_at,exported_at) VALUES('reversal-draft',?,'reversal-source',"
            "'费用冲回','exported',2,'虚构冲回','local-accountant',"
            "'2026-10-20T00:00:00+00:00','2026-10-20T00:00:00+00:00')",
            (book,),
        )
        conn.execute(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
            "VALUES('reversal-draft',?,1,'1002',40000,0)",
            (book,),
        )
        conn.execute(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
            "VALUES('reversal-draft',?,2,'6602.02',0,40000)",
            (book,),
        )
    result = BriefingService(accounting.db, accounting).briefing(LOCAL_ACTOR, book)
    assert result["top_expenses"][0]["amount"] == "-100.00"


def test_briefing_refund_keeps_negative_sign(case):
    accounting, _, book = case
    prepare_book(accounting, book)
    close_book(accounting, book)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
            "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
            "VALUES('refund-source',?,'refund-001','虚构退款','2026-10-20',"
            "'供应商退款','示例供方',40000,'income','退款凭证','%s','bank')"
            % ("c" * 64),
            (book,),
        )
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation,"
            "approved_by,approved_at,exported_at) VALUES('refund-draft',?,'refund-source',"
            "'供应商退款','exported',2,'虚构退款','local-accountant',"
            "'2026-10-20T00:00:00+00:00','2026-10-20T00:00:00+00:00')",
            (book,),
        )
        conn.execute(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
            "VALUES('refund-draft',?,1,'1002',40000,0)",
            (book,),
        )
        conn.execute(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
            "VALUES('refund-draft',?,2,'6602.02',0,40000)",
            (book,),
        )
    result = BriefingService(accounting.db, accounting).briefing(LOCAL_ACTOR, book)
    assert result["top_expenses"][0]["amount"] == "-100.00"


def test_briefing_tax_payment_is_signed_negative(case):
    accounting, _, book = case
    prepare_book(accounting, book)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute(
            "INSERT INTO accounts(book_id,code,name,category,normal_balance) "
            "VALUES(?,'2221','应交税费','liability','credit')",
            (book,),
        )
    close_book(accounting, book)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
            "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
            "VALUES('tax-source',?,'tax-001','虚构缴税','2026-10-20',"
            "'缴纳增值税','税务机关',10000,'expense','完税凭证','%s','bank')"
            % ("b" * 64),
            (book,),
        )
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation,"
            "approved_by,approved_at,exported_at) VALUES('tax-draft',?,'tax-source',"
            "'缴纳增值税','exported',2,'虚构缴税','local-accountant',"
            "'2026-10-20T00:00:00+00:00','2026-10-20T00:00:00+00:00')",
            (book,),
        )
        conn.execute(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
            "VALUES('tax-draft',?,1,'2221',10000,0)",
            (book,),
        )
        conn.execute(
            "INSERT INTO draft_lines(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
            "VALUES('tax-draft',?,2,'1002',0,10000)",
            (book,),
        )
    result = BriefingService(accounting.db, accounting).briefing(LOCAL_ACTOR, book)
    assert result["tax"]["change"] == "-100.00"


def test_briefing_rejects_another_firm_without_book_access(case):
    accounting, _, book = case
    prepare_book(accounting, book)
    close_book(accounting, book)
    stranger = add_actor(
        accounting,
        user_id="brief-other-firm",
        firm_id="brief-other-firm-id",
        role="reviewer",
    )
    with pytest.raises(AccountingError) as missing:
        BriefingService(accounting.db, accounting).briefing(stranger, book)
    assert missing.value.status == 404


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
