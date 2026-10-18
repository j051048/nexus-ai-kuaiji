"""补件、当前证据集、后台任务与审核导出的集成约束。

真实 SQLite 事务及私有文件；模型使用明确的合成输出，不连接供应商。
"""
import io
from uuid import uuid4
import pytest
from PIL import Image
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest
from app.cases.models import CaseCreate, CaseCommand, CaseRespond, CaseReviewResponse
from app.cases.service import CaseService
from app.intelligence.current_evidence import current_attachments
from app.intelligence.requests import OCRRequest
from app.jobs.requests import BatchItem, BatchRequest
from app.jobs.service import QueueService
from test_intelligence_workflow import ai_case, create_event, extract, handoff, invoice_document


def command(task, model=CaseCommand, **changes):
    return model(expected_version=task["version"], idempotency_key=uuid4().hex,
                 reason="合成试点资料已经独立核实", **changes)


@pytest.fixture
def replacement(ai_case):
    c = ai_case
    c.cases = CaseService(c.db, c.business)
    c.ledger.cases = c.cases
    c.ledger.intelligence = c.intelligence
    c.intelligence.ledger.cases = c.cases
    c.queue = QueueService(c.db, c.intelligence)
    event = create_event(c)
    c.provider.document = invoice_document(amount="9.99", net_amount="9.99")
    original_run = extract(c, event, key="stage8-original-incorrect-amount")
    c.mapping = handoff(c, event)
    reviewer, employee = c.actors["accountant"], c.actors["employee"]
    task = c.cases.create(reviewer, CaseCreate(event_id=event["id"], kind="supplement",
        title="补充同一票据清晰扫描", reason="原扫描金额需核对，请上传清晰原件", attachment_required=True,
        idempotency_key=uuid4().hex))
    output = io.BytesIO()
    Image.new("RGB", (50, 25), "black").save(output, "PNG")
    new_attachment = c.storage.put("合成清晰票据.png", "image/png", output.getvalue())
    task = c.cases.add_attachment(employee, task["id"], task["version"], uuid4().hex, new_attachment)["case"]
    task = c.cases.command(employee, task["id"], "respond", command(task, CaseRespond))
    task = c.cases.command(reviewer, task["id"], "review-response", command(task, CaseReviewResponse,
        decision="accept", supersedes_attachment_ids=[event["attachments"][0]["id"]]))
    c.provider.document = invoice_document()
    c.event_id = event["id"]
    c.old_attachment = event["attachments"][0]
    c.new_attachment = new_attachment
    c.task = task
    c.original_run = original_run
    return c


def current_event(c):
    return c.business.get_event(c.actors["accountant"], c.event_id)


def identify_current(c):
    event = current_event(c)
    return c.intelligence.extract(c.actors["accountant"], event["id"], OCRRequest(
        attachment_id=c.new_attachment["id"], expected_version=event["version"], idempotency_key=uuid4().hex))


def test_accepted_supersession_keeps_original_trace_and_excludes_old_ocr_amount(replacement):
    c = replacement
    reviewer = c.actors["accountant"]
    event = current_event(c)
    assert len(event["attachments"]) == 2
    assert next(item for item in event["attachments"] if item["id"] == c.old_attachment["id"])["superseded"]
    with c.db.transaction(actor=reviewer) as conn:
        raw_event = c.business._event(conn, reviewer, event["id"])
        active = current_attachments(c.db, conn, raw_event, c.business._attachments(conn, event["id"]))
        assert [item["id"] for item in active] == [c.new_attachment["id"]]
        assert c.old_attachment["id"] not in c.intelligence._latest(conn, raw_event)
        assert conn.execute("SELECT status FROM ai_runs WHERE id=?", (c.original_run["id"],)).fetchone()[0] == "succeeded"
    assert identify_current(c)["status"] == "succeeded"
    with c.db.transaction(actor=reviewer) as conn:
        assessment = c.intelligence._assessment(conn, reviewer, c.business._event(conn, reviewer, c.event_id))
        assert len(assessment["documents"]) == 1
        assert assessment["documents"][0]["gross_amount"] == "0.30"
        assert [item["attachment_id"] for item in assessment["references"]] == [c.new_attachment["id"]]


def test_superseded_original_is_rejected_for_sync_ocr_and_durable_enqueue(replacement):
    c = replacement
    event = current_event(c)
    before = c.provider.extract_calls
    with pytest.raises(AccountingError, match="替代"):
        c.intelligence.extract(c.actors["accountant"], event["id"], OCRRequest(
            attachment_id=c.old_attachment["id"], expected_version=event["version"], idempotency_key=uuid4().hex))
    with pytest.raises(AccountingError, match="替代"):
        c.queue.enqueue(c.actors["accountant"], BatchRequest(company_id=c.company["id"],
            items=[BatchItem(event_id=event["id"], attachment_id=c.old_attachment["id"], expected_version=event["version"])],
            idempotency_key=uuid4().hex))
    assert c.provider.extract_calls == before
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        raw_event = c.business._event(conn, c.actors["accountant"], c.event_id)
        pending, deps = c.queue.proposals._dependency_plan(conn, raw_event, c.actors["accountant"])
        assert [item["id"] for item in pending] == [c.new_attachment["id"]]
        assert not deps


def test_open_case_blocks_review_and_export_after_ocr_then_close_restores_review_gate(replacement):
    c = replacement
    reviewer = c.actors["accountant"]
    identify_current(c)
    draft = c.ledger.get_draft(reviewer, c.mapping["book_id"], c.mapping["draft_id"])
    assert any("异常待办未关闭" in issue for issue in draft["issues"])
    with pytest.raises(AccountingError, match="异常待办未关闭"):
        c.ledger.approve(reviewer, c.mapping["book_id"], draft["id"], ApprovalRequest(expected_version=draft["version"]))
    with pytest.raises(AccountingError):
        c.ledger.export(reviewer, c.mapping["book_id"], ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    c.cases.command(reviewer, c.task["id"], "close", command(c.task))
    latest = c.ledger.get_draft(reviewer, c.mapping["book_id"], draft["id"])
    assert latest["status"] == "draft" and not latest["issues"]
    with pytest.raises(AccountingError):
        c.ledger.export(reviewer, c.mapping["book_id"], ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    approved = c.ledger.approve(reviewer, c.mapping["book_id"], draft["id"], ApprovalRequest(expected_version=latest["version"]))
    assert approved["status"] == "approved"
    result = c.ledger.export(reviewer, c.mapping["book_id"], ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    assert result["count"] == 1 and "0.30" in result["csv"]
