"""PLT05-C：导出后映射变更的可查可拦，以及导出后被冲销凭证的标注。

补导（只重发失败凭证）本轮**未实现**，原因写在 `_reissue_blocked_reason()`：
`bridge_outbox.export_id` / `external_code` 均为 UNIQUE，`bridge_scope_guard` 又要求
导出行与冻结清单逐条一致，子集补导必须先做结构性迁移并改写守卫，不能在服务层绕过。
"""

import csv
import io
import sqlite3

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.seed import LOCAL_ACTOR
from app.integrations.models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeManualReturn,
    BridgeReceiptImport,
    BridgeReturnImport,
)
from app.integrations.service import FinancialBridgeService
from app.integrations import tplus54
from app.posting.models import PostDraft, ReverseVoucher
from app.posting.service import PostingService
from review_policy_fixtures import reverse_reviewed
from test_accounting import service, book  # noqa: F401
from test_business import business  # noqa: F401
from test_tax06_fee_batch_posting import _frozen_opening
from test_tax06_fees import _bank
from test_tax08_tplus54 import configured, frozen_intent, receipt


def _return_csv(batch_code: str, vouchers: list[dict]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        ["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"]
    )
    for idx, voucher in enumerate(vouchers, start=1):
        amount = sum(float(line.get("debit") or 0) for line in voucher.get("lines") or [])
        writer.writerow(
            [
                batch_code,
                f"T54-{voucher['external_code']}",
                f"T-EXT-{idx}",
                "accepted",
                "",
                "2026-10-31",
                f"{amount:.2f}",
                "",
            ]
        )
    return buffer.getvalue()


def test_mapping_change_after_export_blocks_receipt_and_stays_visible(service, book):
    bridge = configured(service, book)
    intent = frozen_intent(service, book, bridge)
    artifact = bridge.create_artifact(LOCAL_ACTOR, book, intent["id"])
    exported = bridge.get_outbox(LOCAL_ACTOR, book, intent["id"])
    assert exported["mapping_changed"] is False
    assert exported["result_summary"] == "result_unknown"

    # 导出后重新核定科目映射：版本前进，导出用版本保持不变。
    bridge.configure(
        LOCAL_ACTOR,
        book,
        BridgeConfiguration(
            expected_version=1,
            external_book_id="tplus-demo",
            accounts=[
                {"local_code": "1002", "external_code": "100201"},
                {"local_code": "6602.02", "external_code": "660299"},
            ],
            reason="导出后科目映射再次核定",
            idempotency_key="tplus54-config-v2",
        ),
    )
    after = bridge.get_outbox(LOCAL_ACTOR, book, intent["id"])
    assert after["mapping_changed"] is True
    assert after["confirmation_required"] is True
    assert after["mapping_version"] == 1
    assert after["current_mapping_version"] == 2
    # 只导出的事实不能被说成“已接收/已过账”。
    assert after["statuses"]["received"]["at"] is None
    assert after["statuses"]["posted"]["at"] is None
    assert after["result_unknown"] is True

    codes = tplus54.build_rows(intent["package"])[1]
    try:
        bridge.record_receipt(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReceiptImport(
                receipt_code="PLT05-C-MAP-1",
                content=receipt(codes),
                reason="映射变更后直接登记接收",
            ),
        )
    except Exception as error:  # noqa: BLE001 - 断言领域错误文案
        assert "映射已变更" in str(error)
        assert "不能直接登记接收" in str(error)
    else:  # pragma: no cover - 必须被拒绝
        raise AssertionError("导出后映射已变更时必须拒绝直接登记接收")

    # 返回文件导入与手工登记两条入口同样必须拦截，不能绕过映射变更门禁。
    with pytest.raises(AccountingError) as return_refused:
        bridge.record_return(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReturnImport(
                batch_code=intent["external_code"],
                content=_return_csv(intent["external_code"], intent["package"]["vouchers"]),
            ),
        )
    assert return_refused.value.status == 409 and "映射已变更" in return_refused.value.detail

    with pytest.raises(AccountingError) as manual_refused:
        bridge.record_manual_return(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeManualReturn(
                batch_code=intent["external_code"],
                received=True,
                posted=False,
                evidence_ref="manual-evidence-after-mapping-change",
                note="映射变更后尝试绕过手工登记",
            ),
        )
    assert manual_refused.value.status == 409 and "映射已变更" in manual_refused.value.detail


def test_received_batch_keeps_status_and_marks_voided_after_export(business):
    """已接收的批次在映射变更后不受影响且可查；导出后真实记账并冲销时返回视图标注 True。"""
    case = business
    _frozen_opening(case)
    # 业务流水建草稿 → 独立复核 → 导出 → 生成适配文件 → 登记返回文件。
    bank_id = _bank(case, "income", 10_000, "PLT05-VOID-1")
    with case.db.transaction(actor=case.manager) as conn:
        draft = conn.execute(
            "SELECT id,version FROM drafts WHERE source_id=?", (bank_id,)
        ).fetchone()
        lines = conn.execute(
            "SELECT DISTINCT account_code FROM draft_lines WHERE draft_id=?", (draft["id"],)
        ).fetchall()
    codes = sorted({row["account_code"] for row in lines})

    case.ledger.approve(
        case.actors["accountant"],
        case.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=int(draft["version"])),
    )
    bridge = FinancialBridgeService(case.db)
    bridge.configure(
        case.manager,
        case.book["id"],
        BridgeConfiguration(
            expected_version=0,
            external_book_id="tplus-void-demo",
            accounts=[{"local_code": code, "external_code": f"EXT-{code}"} for code in codes],
            reason="已接收后冲销测试映射",
            idempotency_key="plt05-void-cfg-v1",
        ),
    )
    exported = case.ledger.export(
        case.manager,
        case.book["id"],
        ExportRequest(draft_ids=[draft["id"]], idempotency_key="plt05-void-export"),
    )
    intent = bridge.prepare_export(
        case.manager,
        case.book["id"],
        BridgeExport(
            export_id=exported["id"],
            configuration_version=1,
            reason="已接收后冲销测试意图",
            idempotency_key="plt05-void-intent",
        ),
    )
    artifact = bridge.create_artifact(case.manager, case.book["id"], intent["id"])
    voucher = intent["package"]["vouchers"][0]
    voucher_code = f"T54-{voucher['external_code']}"
    imported = bridge.record_return(
        case.manager,
        case.book["id"],
        artifact["id"],
        BridgeReturnImport(
            batch_code=intent["external_code"],
            content=_return_csv(intent["external_code"], intent["package"]["vouchers"]),
        ),
    )
    assert imported["received"] is True
    assert imported["rows"][0]["voided_after_export"] is False

    # 已接收之后再变更科目映射：批次仍为已接收，版本差异可查，不再要求确认。
    bridge.configure(
        case.manager,
        case.book["id"],
        BridgeConfiguration(
            expected_version=1,
            external_book_id="tplus-void-demo",
            accounts=[{"local_code": code, "external_code": f"EXT2-{code}"} for code in codes],
            reason="已接收后更新映射",
            idempotency_key="plt05-void-cfg-v2",
        ),
    )
    outbox = bridge.get_outbox(case.manager, case.book["id"], intent["id"])
    assert outbox["statuses"]["received"]["at"] is not None
    assert outbox["mapping_changed"] is True
    assert outbox["confirmation_required"] is False

    # 真实记账并走独立复核反向冲销：真实 _return_view 必须命中 T54-{external_code} 并标注 True。
    with case.db.transaction(actor=case.manager) as conn:
        approved_draft = conn.execute(
            "SELECT id,version FROM drafts WHERE id=?", (draft["id"],)
        ).fetchone()
    posting = PostingService(case.db)
    posted = posting.post_draft(
        case.manager,
        case.book["id"],
        approved_draft["id"],
        PostDraft(
            command_key="plt05-void-post",
            expected_version=int(approved_draft["version"]),
            voucher_date="2026-10-31",
        ),
    )
    reverse_reviewed(
        posting,
        case.manager,
        posted["voucher"]["id"],
        ReverseVoucher(
            command_key="plt05-void-reverse",
            voucher_date="2026-10-31",
            reason="导出后核对发现差错，真实反向冲销",
        ),
    )
    refreshed = bridge.list_returns(case.manager, case.book["id"], artifact["id"])["returns"][0]
    by_code = {row["voucher_code"]: row for row in refreshed["rows"]}
    assert by_code[voucher_code]["voided_after_export"] is True


def test_voided_draft_annotation_query_contract():
    """`_voided_draft_ids` 必须只认「导出草稿 → 正式凭证 → 反向冲销」这条链。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE drafts(id TEXT PRIMARY KEY, book_id TEXT NOT NULL);
        CREATE TABLE voucher_sources(source_kind TEXT, source_id TEXT, voucher_id TEXT);
        CREATE TABLE voucher_relations(original_voucher_id TEXT, related_voucher_id TEXT, kind TEXT);
        INSERT INTO drafts VALUES('d1','b1'),('d2','b1'),('d3','b2');
        INSERT INTO voucher_sources VALUES('draft','d1','v1'),('draft','d2','v2'),('manual','d3','v3');
        INSERT INTO voucher_relations VALUES('v1','vr1','reversal'),('v2','vr2','adjustment');
        """
    )
    voided = FinancialBridgeService._voided_draft_ids(conn, "b1")
    assert voided == {"d1": "vr1"}
    # 另一个账套的草稿不会被串进来。
    assert FinancialBridgeService._voided_draft_ids(conn, "b2") == {}
