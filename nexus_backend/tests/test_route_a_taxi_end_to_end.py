"""Route A end-to-end verification:
1. Taxi fee difference full lifecycle (employee upload -> settlement/bank/fee analysis with
   anti-cross-matching guards -> verified relation -> draft -> independent review & posting ->
   external return manual vs file verification -> formal monthly report & profit drilldown).
2. Five-company simultaneous monthly close with 5 staff roles (queue lanes + backlog attribution +
   trustworthy firm insights with missing companies, mixed standards, and strict permission boundaries).
"""

from __future__ import annotations

import base64
import csv
import io
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.accounting.domain import Actor, AccountingError
from app.accounting.models import (
    ApprovalRequest,
    BookCreate,
    BookMemberGrant,
    CompanyCreate,
    ExportRequest,
    FirmMemberGrant,
    ImportRequest,
    SourceRow,
)
from app.accounting.review import ReviewService
from app.assistant import analysis
from app.business.models import BusinessEventCreate, BusinessPayload
from app.business.service import BusinessService
from app.business.storage import AttachmentStore
from app.cases.models import CaseCreate
from app.cases.service import CaseService
from app.integrations.models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeManualReturn,
    BridgeReturnEvidence,
    BridgeReturnImport,
)
from app.integrations.service import FinancialBridgeService
from app.observability.insights import FirmInsightsService
from app.posting.models import OpeningBind, PostDraft
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.tax.relations import SourceRelationService
from app.workspace.queue_lanes import QUEUE_LANE_RULE_VERSION
from app.workspace.service import WorkspaceService
from test_business import approved, business, handoff  # noqa: F401
from test_posting_service import frozen_opening


def _csv_return(batch_code: str, voucher_code: str, amount: float) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"])
    writer.writerow([batch_code, voucher_code, "T-202610-001", "accepted", "", "2026-10-31", f"{amount:.2f}", "100201"])
    return buf.getvalue()


def _source(conn, book_id, source_id, name, amount_cents, kind="business", evidence="合成原件", dt="2026-10-18", direction="income"):
    conn.execute(
        "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
        "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            book_id,
            f"ext-{source_id}",
            name,
            dt,
            name,
            "合成出行平台",
            amount_cents,
            direction,
            evidence,
            uuid4().hex,
            kind,
        ),
    )


def test_route_a_taxi_fee_difference_full_lifecycle(business, tmp_path):
    case = business
    storage = AttachmentStore(tmp_path / "originals")
    biz = BusinessService(case.db)

    # 1) 员工上传带 PDF 原件的规费/报销资料
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"
    event = biz.create(
        case.actors["employee"],
        BusinessEventCreate(
            company_id=case.company["id"],
            template_id="reimbursement",
            payload=BusinessPayload(
                occurred_on="2026-10-18",
                description="合成出租车平台结算扣费说明",
                counterparty="合成出行平台",
                amount="350.00",
                direction="expense",
                payment_status="unpaid",
                category="office",
            ),
        ),
    )
    meta = storage.put_original("规费扣款说明.pdf", "application/pdf", pdf)
    attached = biz.add_attachment(case.actors["employee"], event["id"], event["version"], meta)
    assert len(attached["attachments"]) == 1

    # 2) 导入正常差额组合 + 四组边界防错样本（同日同额无关流水、跨期凭据、对平样本、他企同额流水）
    with case.db.transaction(write=True) as conn:
        _source(conn, case.book["id"], "taxi-settle-1", "10月平台结算单", 10_000_000)
        _source(conn, case.book["id"], "taxi-bank-1", "10月银行到账", 9_965_000, "bank", "合成银行回单-01")
        _source(conn, case.book["id"], "taxi-fee-real", "10月平台服务费明细", 35_000, "business", "合成服务费单-01", direction="expense")
        _source(conn, case.book["id"], "taxi-fee-unrelated", "同日同额无关扣款", 35_000, "business", "合成其他凭据-02", direction="expense")
        _source(conn, case.book["id"], "taxi-fee-prev-month", "上月平台手续费", 35_000, "business", "合成上月凭据", dt="2026-09-30", direction="expense")
        _source(conn, case.book["id"], "taxi-settle-balanced", "10月已对平结算单", 5_000_000)
        _source(conn, case.book["id"], "taxi-bank-balanced", "10月已对平银行到账", 5_000_000, "bank", "合成银行回单-02")
        _source(conn, case.other_book["id"], "other-company-fee", "另一法人同额350手续费", 35_000, "business", "他企原件", direction="expense")

    # 2a) 对平样本 -> reason='none', status='complete', 无缺失项
    balanced = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "settlement_difference",
        settlement_source_id="taxi-settle-balanced",
        bank_source_id="taxi-bank-balanced",
    )
    assert balanced["status"] == "complete"
    assert balanced["facts"]["reason"] == "none"
    assert balanced["facts"]["difference_cents"] == 0
    assert balanced["missing"] == []

    # 2b) 跨期凭据 -> cross_period_evidence
    cross_period = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "settlement_difference",
        settlement_source_id="taxi-settle-1",
        bank_source_id="taxi-bank-1",
        fee_source_id="taxi-fee-prev-month",
    )
    assert cross_period["status"] == "partial"
    assert cross_period["facts"]["reason"] == "cross_period_evidence"

    # 2c) 他企同额凭据 -> 404 防串企
    with pytest.raises(AccountingError) as cross_co:
        analysis.analyze(
            case.db,
            case.manager,
            case.book["id"],
            "2026-10",
            "settlement_difference",
            settlement_source_id="taxi-settle-1",
            bank_source_id="taxi-bank-1",
            fee_source_id="other-company-fee",
        )
    assert cross_co.value.status == 404

    # 2d) 同日同额无关凭据 -> 仅核验结算单与到账关系时，无关手续费被拒认（fee_relation_mismatch）
    relations = SourceRelationService(case.db)
    relations.verify(
        case.manager,
        case.book["id"],
        settlement_source_id="taxi-settle-1",
        bank_source_id="taxi-bank-1",
        reason="先核对结算单与银行到账关系",
    )
    unrelated = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "settlement_difference",
        settlement_source_id="taxi-settle-1",
        bank_source_id="taxi-bank-1",
        fee_source_id="taxi-fee-unrelated",
    )
    assert unrelated["status"] == "partial"
    assert unrelated["facts"]["reason"] == "fee_relation_mismatch"

    # 2e) 会计核验三者来源关系 -> 助手确认 350.00 元由真实手续费解释
    relations.verify(
        case.manager,
        case.book["id"],
        settlement_source_id="taxi-settle-1",
        bank_source_id="taxi-bank-1",
        fee_source_id="taxi-fee-real",
        reason="核对平台结算单、银行到账与真实服务费明细",
    )
    explained = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "settlement_difference",
        settlement_source_id="taxi-settle-1",
        bank_source_id="taxi-bank-1",
        fee_source_id="taxi-fee-real",
    )
    assert explained["status"] == "complete"
    assert explained["facts"]["reason"] == "fee_evidence"
    assert explained["facts"]["difference"] == "350.00"
    assert explained["facts"]["fee_total"] == "350.00"

    # 3) 正式期初 + 生成凭证草稿 + 禁止自审 + 独立复核人审核并过账
    opening = frozen_opening(case)
    posting = PostingService(case.db)
    posting.bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="路线 A 出租车正式期初",
        ),
    )
    case.ledger.import_sources(
        case.manager,
        case.book["id"],
        ImportRequest(
            source_name="合成平台服务费确认单",
            rows=[
                SourceRow(
                    external_id="taxi-posted-fee",
                    date="2026-10-18",
                    description="平台服务手续费办公支出",
                    counterparty="合成出行平台",
                    amount="350.00",
                    direction="expense",
                    evidence_ref="合成服务费单-01",
                ),
                SourceRow(
                    external_id="taxi-posted-fee-2",
                    date="2026-10-19",
                    description="平台通道服务费办公支出",
                    counterparty="合成出行平台",
                    amount="120.00",
                    direction="expense",
                    evidence_ref="合成服务费单-02",
                ),
            ],
        ),
    )
    case.ledger.suggestions(case.manager, case.book["id"])
    book_view = case.ledger.get_book(case.manager, case.book["id"])
    posted_source = next(
        s for s in book_view["sources"] if s["external_id"] == "taxi-posted-fee"
    )
    posted_source_2 = next(
        s for s in book_view["sources"] if s["external_id"] == "taxi-posted-fee-2"
    )
    target_draft = next(
        d for d in book_view["drafts"] if d["source_id"] == posted_source["id"]
    )
    target_draft_2 = next(
        d for d in book_view["drafts"] if d["source_id"] == posted_source_2["id"]
    )

    with pytest.raises(AccountingError) as self_err:
        case.ledger.approve(
            case.manager,
            case.book["id"],
            target_draft["id"],
            ApprovalRequest(expected_version=target_draft["version"]),
        )
    assert self_err.value.status == 403

    reviewer = case.actors["accountant"]
    approved_res = case.ledger.approve(
        reviewer,
        case.book["id"],
        target_draft["id"],
        ApprovalRequest(expected_version=target_draft["version"]),
    )
    approved_res_2 = case.ledger.approve(
        reviewer,
        case.book["id"],
        target_draft_2["id"],
        ApprovalRequest(expected_version=target_draft_2["version"]),
    )
    posted = posting.post_draft(
        reviewer,
        case.book["id"],
        target_draft["id"],
        PostDraft(
            command_key="route-a-post-" + target_draft["id"],
            expected_version=approved_res["version"],
            voucher_date="2026-10-18",
        ),
    )
    posting.post_draft(
        reviewer,
        case.book["id"],
        target_draft_2["id"],
        PostDraft(
            command_key="route-a-post-" + target_draft_2["id"],
            expected_version=approved_res_2["version"],
            voucher_date="2026-10-19",
        ),
    )
    voucher = posted.get("voucher", posted)
    assert voucher["status"] == "posted"

    # 4) 外部回执核验：手工回执 + 补传 PDF 附件绝不冒充逐笔过账；逐笔文件导入才标记 posted=True
    bridge = FinancialBridgeService(case.db)
    bridge.configure(
        case.manager,
        case.book["id"],
        BridgeConfiguration(
            expected_version=0,
            external_book_id="tplus-taxi-001",
            accounts=[
                {"local_code": "1002", "external_code": "100201"},
                {"local_code": "6602.02", "external_code": "660202"},
            ],
            reason="出租车 T+ 对接科目映射",
            idempotency_key="route-a-bridge-cfg",
        ),
    )
    exported = case.ledger.export(
        case.manager,
        case.book["id"],
        ExportRequest(draft_ids=[target_draft["id"]], idempotency_key="route-a-export"),
    )
    intent = bridge.prepare_export(
        case.manager,
        case.book["id"],
        BridgeExport(
            export_id=exported["id"],
            configuration_version=1,
            reason="出租车月度交付",
            idempotency_key="route-a-intent",
        ),
    )
    artifact = bridge.create_artifact(case.manager, case.book["id"], intent["id"])

    manual = bridge.record_manual_return(
        reviewer,
        case.book["id"],
        artifact["id"],
        BridgeManualReturn(
            batch_code=intent["external_code"],
            received=True,
            posted=True,
            posted_date="2026-10-31",
            note="人工在外部账套核对并拟补传截图",
            evidence_ref="手工截图编号 M-01",
        ),
    )
    assert manual["posted"] is False
    assert manual["manual_posted_claim"] is True

    bridge.attach_evidence(
        reviewer,
        case.book["id"],
        artifact["id"],
        manual["id"],
        BridgeReturnEvidence(
            filename="外部系统回执截图.pdf",
            media_type="application/pdf",
            content_base64=base64.b64encode(pdf).decode("ascii"),
        ),
    )
    after_manual = bridge.list_returns(reviewer, case.book["id"], artifact["id"])["returns"][0]
    assert after_manual["source_kind"] == "manual"
    assert after_manual["evidence_attached"] is True
    assert after_manual["manual_posted_claim"] is True
    assert after_manual["posted"] is False  # 关键 P0-1 门禁：附件不等于逐笔过账！
    outbox_before = bridge.overview(reviewer, case.book["id"])["outbox"][0]
    assert outbox_before["statuses"]["posted"]["at"] is None
    assert outbox_before["manual_posted_claim"]["at"] is not None
    assert outbox_before["manual_posted_claim"]["evidence_attached"] is True
    assert outbox_before["manual_posted_claim"]["filename"] == "外部系统回执截图.pdf"

    # 另生成一份独立冻结交付包验证：错批次文件被 422 拒绝，正确批次逐笔 CSV 核验才标记 posted=True
    exported_csv = case.ledger.export(
        case.manager,
        case.book["id"],
        ExportRequest(draft_ids=[target_draft_2["id"]], idempotency_key="route-a-export-csv"),
    )
    intent_csv = bridge.prepare_export(
        case.manager,
        case.book["id"],
        BridgeExport(
            export_id=exported_csv["id"],
            configuration_version=1,
            reason="出租车月度逐笔文件交付",
            idempotency_key="route-a-intent-csv",
        ),
    )
    artifact_csv = bridge.create_artifact(case.manager, case.book["id"], intent_csv["id"])
    voucher_pkg = intent_csv["package"]["vouchers"][0]
    voucher_amount = sum(float(line["debit"] or 0) for line in voucher_pkg["lines"])
    with pytest.raises(AccountingError, match="批次标识与冻结交付批次不一致") as wrong_batch:
        bridge.record_return(
            reviewer,
            case.book["id"],
            artifact_csv["id"],
            BridgeReturnImport(
                batch_code="WRONG-BATCH-999",
                content=_csv_return("WRONG-BATCH-999", f"T54-{voucher_pkg['external_code']}", voucher_amount),
            ),
        )
    assert wrong_batch.value.status == 422

    verified = bridge.record_return(
        reviewer,
        case.book["id"],
        artifact_csv["id"],
        BridgeReturnImport(
            batch_code=intent_csv["external_code"],
            content=_csv_return(
                intent_csv["external_code"],
                f"T54-{voucher_pkg['external_code']}",
                voucher_amount,
            ),
        ),
    )
    assert verified["source_kind"] == "file"
    assert verified["posted"] is True
    assert verified["summary_breakdown"]["confirmed_count"] == 1

    # 5) 正式月报生成、核验、独立复核，并验证利润表明细可追溯（不串现金流）
    reports = ReportService(case.db)
    report_res = reports.create(case.manager, case.book["id"], "2026-10")
    report = report_res["report"]
    assert report["mode"] == "formal"
    reports.validate(case.manager, report["id"])
    reviewed_view = reports.review(
        reviewer,
        report["id"],
        ReportCommand(expected_version=report["version"], reason="出租车 10 月正式月报独立复核"),
    )
    assert reviewed_view["report"]["status"] == "reviewed"
    profit_stmt = reviewed_view["content"]["profit_statement"]
    assert profit_stmt["expense_cents"] >= 35_000


def test_route_a_five_companies_monthly_close_coordination(business):
    case = business
    # 已有 2 家企业（case.company、case.other），再新增 3 家凑足 5 家同时月结
    companies_and_books = [
        (case.company, case.book, "small_enterprise", True),
        (case.other, case.other_book, "enterprise", True),
    ]
    for idx, (name, std, do_review) in enumerate(
        [
            ("合成出租车二公司", "small_enterprise", True),
            ("合成汽服维保公司", "enterprise", False),
            ("合成广告传媒公司", "small_enterprise", False),
        ],
        start=3,
    ):
        co = case.directory.create_company(case.manager, CompanyCreate(name=name))
        bk = case.ledger.create_book(
            case.manager, BookCreate(name=f"{name}10月账套", period="2026-10", company_id=co["id"])
        )
        companies_and_books.append((co, bk, std, do_review))

    # 配置 5 人分工：manager(主管) + accountant(复核人1) + reviewer2(复核人2) + editor1(仅限第1家) + employee
    def make_staff(uid, display, role):
        ident = SimpleNamespace(user_id=uid, display_name=display, email=f"{uid}@example.test")
        case.directory.register_identity(ident)
        case.directory.grant_firm_member(case.manager, FirmMemberGrant(email=ident.email, role=role))
        return Actor(uid, case.manager.firm_id)

    reviewer2 = make_staff("reviewer2", "第二独立复核人", "reviewer")
    editor1 = make_staff("editor1", "第一公司专职会计", "operator")
    case.directory.grant_book_member(
        case.manager, case.book["id"], editor1.user_id, BookMemberGrant(role="editor")
    )

    reports = ReportService(case.db)
    for idx, (co, bk, std, do_review) in enumerate(companies_and_books):
        rev = case.actors["accountant"] if idx % 2 == 0 else reviewer2
        case.directory.grant_book_member(
            case.manager, bk["id"], rev.user_id, BookMemberGrant(role="reviewer")
        )
        sub_case = SimpleNamespace(**{**vars(case), "company": co, "book": bk})
        opening = frozen_opening(sub_case)
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE accounting_policies SET standard_kind=? "
                "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)",
                (std, bk["id"]),
            )
        PostingService(case.db).bind_opening(
            case.manager,
            OpeningBind(book_id=bk["id"], package_version_id=opening["version_id"], reason="五企月结期初"),
        )
        if do_review:
            rep = reports.create(case.manager, bk["id"], "2026-10")["report"]
            reports.validate(case.manager, rep["id"])
            reports.review(
                rev,
                rep["id"],
                ReportCommand(expected_version=rep["version"], reason="五企独立复核"),
            )

    # 给第 1 家制造一条 2 天后到期的紧急差异待办，验证共享队列 lane 与快到期过滤
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    soon_due = (today + timedelta(days=2)).isoformat()
    ev = approved(case, description="五企月结紧急补件")
    ho = handoff(case, ev)
    cases = CaseService(case.db, case.service)
    urgent_case = cases.create(
        case.manager,
        CaseCreate(
            book_id=case.book["id"],
            event_id=ev["id"],
            draft_id=ho["draft_id"],
            kind="exception",
            title="五企月结快到期核对",
            reason="需在关账前完成核对",
            priority="urgent",
            due_on=soon_due,
            idempotency_key="route-a-5co-due-soon",
        ),
    )

    ws = WorkspaceService(
        case.db, case.ledger, case.service, ReviewService(case.db, case.ledger), cases
    )
    due_soon_view = ws.list(case.manager, bucket="due_soon", use_projection=False)
    assert due_soon_view["queue_lanes"]["rule_version"] == QUEUE_LANE_RULE_VERSION
    assert any(item["ref"] == urgent_case["id"] for item in due_soon_view["tasks"])
    assert sum(item["count"] for item in due_soon_view["backlog_summary"]) == len(due_soon_view["tasks"])

    # 集团总览：纳入 3/5 家，点名 2 家缺报，不同准则分列不混算，标注管理汇总非合并报表
    insights = FirmInsightsService(case.db).overview(case.manager, "2026-10")
    fin = insights["financial"]
    assert fin["known_books"] == 3
    assert fin["missing_books"] == 2
    assert "已纳入 3/5 家" in fin["coverage_label"]
    assert fin["mixed_standards"] is True
    assert fin["totals_comparable"] is False
    assert "管理汇总，非合并财务报表" in fin["management_summary_notice"]
    assert {row["company_name"] for row in fin["missing_companies"]} == {
        "合成汽服维保公司",
        "合成广告传媒公司",
    }

    # 权限边界：只授第 1 家权限的 editor1 既不能看第 2 家账套，也不能看集团总览
    with pytest.raises(AccountingError) as denied_book:
        case.ledger.get_book(editor1, case.other_book["id"])
    assert denied_book.value.status in (403, 404)
    with pytest.raises(AccountingError) as denied_insights:
        FirmInsightsService(case.db).overview(editor1, "2026-10")
    assert denied_insights.value.status == 403
