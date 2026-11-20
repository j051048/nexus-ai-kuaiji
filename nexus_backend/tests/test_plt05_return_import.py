import base64
import csv
import io

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ExportRequest
from app.accounting.seed import LOCAL_ACTOR
from app.integrations.models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeManualReturn,
    BridgeReturnEvidence,
    BridgeReturnImport,
    BridgeReturnRowHandle,
)
from app.integrations.service import FinancialBridgeService
from test_accounting import service, book, make_draft, approve  # noqa: F401


def _csv(batch, voucher, amount, posted="2026-10-31"):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"])
    writer.writerow([batch, voucher, "T-1", "accepted", "", posted, f"{amount:.2f}", "100201"])
    return buffer.getvalue()


def _artifact(bridge, service, book_id):
    bridge.configure(
        LOCAL_ACTOR,
        book_id,
        BridgeConfiguration(
            expected_version=0,
            external_book_id="tplus-return",
            accounts=[
                {"local_code": "1002", "external_code": "100201"},
                {"local_code": "6602.02", "external_code": "660202"},
            ],
            reason="返回核对合成映射",
            idempotency_key="return-config",
        ),
    )
    draft = make_draft(service, book_id)
    approve(service, book_id, draft)
    exported = service.export(
        LOCAL_ACTOR,
        book_id,
        ExportRequest(draft_ids=[draft["id"]], idempotency_key="return-export"),
    )
    intent = bridge.prepare_export(
        LOCAL_ACTOR,
        book_id,
        BridgeExport(
            export_id=exported["id"],
            configuration_version=1,
            reason="返回核对",
            idempotency_key="return-intent",
        ),
    )
    artifact = bridge.create_artifact(LOCAL_ACTOR, book_id, intent["id"])
    return intent, artifact


def test_return_import_is_idempotent_and_marks_received_posted(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    voucher = intent["package"]["vouchers"][0]
    amount = sum(float(line["debit"] or 0) for line in voucher["lines"])
    payload = BridgeReturnImport(
        batch_code=intent["external_code"],
        content=_csv(intent["external_code"], f"T54-{voucher['external_code']}", amount),
    )
    result = bridge.record_return(LOCAL_ACTOR, book, artifact["id"], payload)
    assert result["received"] is True
    assert result["posted"] is True
    assert result["counts"]["accepted"] == 1
    assert bridge.record_return(LOCAL_ACTOR, book, artifact["id"], payload)["id"] == result["id"]
    listed = bridge.list_returns(LOCAL_ACTOR, book, artifact["id"])
    assert listed["returns"][0]["id"] == result["id"]


def test_return_import_amount_mismatch_is_partial_and_not_received(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    voucher = intent["package"]["vouchers"][0]
    payload = BridgeReturnImport(
        batch_code=intent["external_code"],
        content=_csv(intent["external_code"], f"T54-{voucher['external_code']}", 0.01),
    )
    result = bridge.record_return(LOCAL_ACTOR, book, artifact["id"], payload)
    assert result["result_status"] == "partial"
    assert result["received"] is False
    assert result["posted"] is False
    assert result["counts"]["mismatch"] == 1
    assert result["counts"]["discrepancy"] == 1
    assert result["rows"][0]["status"] == "amount_mismatch"
    tasks = bridge.list_return_tasks(LOCAL_ACTOR, book)
    assert [task["object_id"] for task in tasks] == [f"{result['id']}:{result['rows'][0]['voucher_code']}"]
    assert tasks[0]["available_action"] == "handle_return"
    assert set(tasks[0]) >= {
        "task_type", "object_type", "object_id", "company_id", "book_id",
        "artifact_id", "import_id", "voucher_code", "status",
        "available_action", "business_action", "task_version",
        "amount_cents", "reason",
    }


def test_return_import_rejects_other_batch(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    voucher = intent["package"]["vouchers"][0]
    amount = sum(float(line["debit"] or 0) for line in voucher["lines"])
    with pytest.raises(AccountingError, match="批次标识"):
        bridge.record_return(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReturnImport(
                batch_code="other-batch",
                content=_csv("another", f"T54-{voucher['external_code']}", amount),
            ),
        )


def test_manual_return_requires_evidence_and_received(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    with pytest.raises(AccountingError, match="证据"):
        bridge.record_manual_return(
            LOCAL_ACTOR, book, artifact["id"],
            BridgeManualReturn(batch_code=intent["external_code"], received=True, evidence_ref="ab"),
        )
    with pytest.raises(AccountingError, match="接收"):
        bridge.record_manual_return(
            LOCAL_ACTOR, book, artifact["id"],
            BridgeManualReturn(
                batch_code=intent["external_code"], received=False, posted=True,
                evidence_ref="客户邮件回执 2026-11-01",
            ),
        )


def test_manual_return_marks_received_not_posted(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    payload = BridgeManualReturn(
        batch_code=intent["external_code"], received=True, posted=False,
        evidence_ref="客户邮件回执 2026-11-01",
    )
    result = bridge.record_manual_return(LOCAL_ACTOR, book, artifact["id"], payload)
    assert result["received"] is True
    assert result["posted"] is False
    assert result["counts"]["accepted"] == 1
    assert result["rows"][0]["voucher_code"] == "MANUAL"
    assert result["rows"][0]["status"] == "accepted"
    assert bridge.record_manual_return(LOCAL_ACTOR, book, artifact["id"], payload)["id"] == result["id"]
    with pytest.raises(AccountingError, match="不同"):
        bridge.record_manual_return(
            LOCAL_ACTOR, book, artifact["id"],
            BridgeManualReturn(
                batch_code=intent["external_code"], received=True, posted=True,
                posted_date="2026-11-02",
                evidence_ref="另一份回执 2026-11-02",
            ),
        )


def test_discrepancy_requires_evidence_and_is_idempotent(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    voucher = intent["package"]["vouchers"][0]
    result = bridge.record_return(
        LOCAL_ACTOR, book, artifact["id"],
        BridgeReturnImport(
            batch_code=intent["external_code"],
            content=_csv(intent["external_code"], f"T54-{voucher['external_code']}", 0.01),
        ),
    )
    handle = BridgeReturnRowHandle(
        action="resolved", reason="客户确认手续费", evidence_ref="客户邮件 2026-11-02",
    )
    handled = bridge.handle_return_row(
        LOCAL_ACTOR, book, artifact["id"], result["id"],
        f"T54-{voucher['external_code']}", handle,
    )
    assert handled["rows"][0]["handled"] is True
    assert handled["rows"][0]["handler"]["reason"] == "客户确认手续费"
    assert bridge.handle_return_row(
        LOCAL_ACTOR, book, artifact["id"], result["id"],
        f"T54-{voucher['external_code']}", handle,
    )["rows"][0]["handled"] is True
    with pytest.raises(AccountingError, match="原记录不会覆盖"):
        bridge.handle_return_row(
            LOCAL_ACTOR, book, artifact["id"], result["id"],
            f"T54-{voucher['external_code']}",
            BridgeReturnRowHandle(action="ignored", reason="改为忽略", evidence_ref="客户邮件 2026-11-03"),
        )


def test_manual_return_evidence_round_trip_and_guards(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    manual = bridge.record_manual_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeManualReturn(
            batch_code=intent["external_code"],
            received=True,
            posted=False,
            evidence_ref="客户邮件回执 2026-10-31",
            note="客户软件无文件导出，人工登记",
        ),
    )
    bad = BridgeReturnEvidence(
        filename="fake.pdf",
        media_type="application/pdf",
        content_base64=base64.b64encode(b"not a pdf").decode(),
    )
    with pytest.raises(AccountingError) as exc:
        bridge.attach_evidence(LOCAL_ACTOR, book, artifact["id"], manual["id"], bad)
    assert exc.value.status == 422
    payload = BridgeReturnEvidence(
        filename="客户回执.pdf",
        media_type="application/pdf",
        content_base64=base64.b64encode(b"%PDF-1.4 synthetic receipt").decode(),
    )
    attached = bridge.attach_evidence(LOCAL_ACTOR, book, artifact["id"], manual["id"], payload)
    assert attached["size_bytes"] > 0
    stored = bridge.evidence_for(LOCAL_ACTOR, book, manual["id"], artifact["id"])
    assert stored["filename"] == "客户回执.pdf"
    with pytest.raises(AccountingError) as exc:
        bridge.attach_evidence(LOCAL_ACTOR, book, artifact["id"], manual["id"], payload)
    assert exc.value.status == 409

def test_manual_evidence_missing_is_not_probeable(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    with pytest.raises(AccountingError) as exc:
        bridge.evidence_for(LOCAL_ACTOR, book, "missing-return-import")
    assert exc.value.status == 404

def test_manual_evidence_rejects_invalid_base64_without_leaking(service):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    with pytest.raises(AccountingError) as exc:
        bridge.attach_evidence(
            LOCAL_ACTOR, "missing-book", "missing-artifact", "missing-import",
            BridgeReturnEvidence(filename="x.pdf", media_type="application/pdf", content_base64="not-base64!"),
        )
    assert exc.value.status == 422


def test_return_import_rejects_other_frozen_batch(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    voucher = intent["package"]["vouchers"][0]
    with pytest.raises(AccountingError, match="批次标识"):
        bridge.record_return(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReturnImport(
                batch_code="other-batch",
                content=_csv("other-batch", f"T54-{voucher['external_code']}", 80),
            ),
        )


def test_return_without_amount_or_accounts_is_received_not_posted(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    voucher = intent["package"]["vouchers"][0]
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"])
    writer.writerow([intent["external_code"], f"T54-{voucher['external_code']}", "T-1", "accepted", "", "2026-10-31", "", ""])
    result = bridge.record_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReturnImport(batch_code=intent["external_code"], content=buffer.getvalue()),
    )
    assert result["result_status"] == "accepted"
    assert result["received"] is True
    assert result["posted"] is False
    assert "只能确认接收" in result["rows"][0]["reason"]


def test_manual_posted_needs_date_and_verified_evidence(service, book):  # noqa: F811
    bridge = FinancialBridgeService(service.db)
    intent, artifact = _artifact(bridge, service, book)
    with pytest.raises(AccountingError, match="过账日期"):
        bridge.record_manual_return(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeManualReturn(
                batch_code=intent["external_code"],
                received=True,
                posted=True,
                evidence_ref="客户邮件回执 2026-11-02",
            ),
        )
    manual = bridge.record_manual_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeManualReturn(
            batch_code=intent["external_code"],
            received=True,
            posted=True,
            posted_date="2026-11-02",
            evidence_ref="客户邮件回执 2026-11-02",
        ),
    )
    assert manual["posted_declared"] is True
    assert manual["evidence_verified"] is False
    assert manual["posted"] is False
    bridge.attach_evidence(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        manual["id"],
        BridgeReturnEvidence(
            filename="客户回执.pdf",
            media_type="application/pdf",
            content_base64=base64.b64encode(b"%PDF-1.4 synthetic receipt").decode(),
        ),
    )
    verified = bridge.list_returns(LOCAL_ACTOR, book, artifact["id"])["returns"][0]
    assert verified["source_kind"] == "manual"
    assert verified["evidence_attached"] is True
    assert verified["evidence_verified"] is True
    assert verified["manual_posted_claim"] is True
    # 附原件仅证明文件收存与人工声明，不能冒充按批次/凭证号/金额逐笔核验外部过账
    assert verified["posted"] is False
    assert verified["verification_state"] == "manual_posted_claimed_with_evidence"
    assert verified["evidence_attachment"]["filename"] == "客户回执.pdf"
    assert verified["summary_breakdown"] == {
        "confirmed_count": 0,
        "pending_count": 0,
        "handled_count": 0,
    }
    overview = bridge.overview(LOCAL_ACTOR, book)
    outbox = next(item for item in overview["outbox"] if item["id"] == intent["id"])
    assert outbox["statuses"]["posted"]["at"] is None
    assert outbox["result_summary"] == "received"
    assert outbox["manual_posted_claim"]["evidence_attached"] is True
    assert outbox["manual_posted_claim"]["filename"] == "客户回执.pdf"
    assert verified["rows"][0]["posted_date"] == "2026-11-02"
    assert verified["rows"][0]["reason"] == "手工登记整批接收：客户邮件回执 2026-11-02"
