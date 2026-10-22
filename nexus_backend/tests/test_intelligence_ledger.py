from period_fixtures import restore_period_state
"""Current OCR facts gate review/export; reading guards never writes findings."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, SourceRow
from app.accounting.service import now
from app.business.models import BusinessEventCreate, HandoffRequest
from app.business.service import BusinessService
from app.intelligence.provider import (
    AISettings,
    EXTRACTION_PROMPT_VERSION,
    OpenAICompatibleProvider,
)
from app.intelligence.service import IntelligenceService
from test_business import business
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres


@pytest.fixture(params=["sqlite", "postgres"])
def guard_case(request):
    if request.param == "postgres":
        if not ADMIN_URL or not APP_URL:
            pytest.skip("需要专用 PostgreSQL 管理员及受限运行测试连接")
        raw = request.getfixturevalue("business_case")
        case = SimpleNamespace(db=raw.db, owner=raw.owner, employee=raw.employee, finance=raw.finance,
                               approver=raw.approver, reviewer=raw.reviewer, ledger=raw.service,
                               company=raw.company, book=raw.book)
        case.business = BusinessService(raw.db)
    else:
        raw = request.getfixturevalue("business")
        case = SimpleNamespace(db=raw.db, owner=raw.manager, employee=raw.actors["employee"],
                               finance=raw.actors["finance"], approver=raw.actors["approver"],
                               reviewer=raw.actors["accountant"], ledger=raw.ledger, company=raw.company["id"], book=raw.book["id"])
        case.business = raw.service
    case.ai = IntelligenceService(case.db, None, provider=OpenAICompatibleProvider(AISettings()))
    # This suite compares an explicitly paused legacy ledger against enabling
    # its OCR guard. Product defaults are independently covered as enabled.
    case.ai.company_settings(case.owner, case.company, enabled=False)
    return case


def policy(case, enabled=True):
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("INSERT INTO ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                     (case.owner.firm_id, case.company, int(enabled), case.owner.user_id, now()))


def business_draft(case, paid=False):
    payload = {"occurred_on": "2026-10-03", "description": "办公耗材", "counterparty": "耗材商店",
               "amount": "0.30", "direction": "expense", "payment_status": "paid" if paid else "unpaid", "category": "office"}
    event = case.business.create(case.employee, BusinessEventCreate(company_id=case.company, template_id="procurement_expense", payload=payload))
    attachment_id = uuid4().hex
    event = case.business.add_attachment(case.employee, event["id"], event["version"], {
        "id": attachment_id, "storage_key": "test/" + attachment_id, "filename": "票据.pdf",
        "media_type": "application/pdf", "sha256": "a" * 64, "size_bytes": 100,
    })
    event = case.business.submit(case.employee, event["id"], event["version"])
    event = case.business.approve_department(case.approver, event["id"], event["version"])
    event = case.business.approve_finance(case.finance, event["id"], event["version"])
    result = case.business.handoff(case.finance, event["id"], HandoffRequest(expected_version=event["version"], book_id=case.book, idempotency_key=uuid4().hex))
    return result["event"], result["draft_id"], attachment_id


def document(gross="0.30", **changes):
    result = {"document_type": "invoice", "invoice_number": "TEST" + uuid4().hex,
              "issued_on": "2026-10-03", "seller_name": "耗材商店", "seller_tax_id": None,
              "buyer_name": "试点企业", "buyer_tax_id": None, "gross_amount": gross,
              "net_amount": gross, "tax_amount": "0.00", "currency": "CNY",
              "pages": [{"page": 1, "raw_text": "测试票据"}], "evidence": [], **changes}
    return result


def recorded_ocr(case, event, attachment_id, doc=None):
    run_id, stamp = uuid4().hex, now()
    with case.db.transaction(write=True, actor=case.employee) as conn:
        conn.execute("INSERT INTO ai_runs(id,firm_id,company_id,event_id,kind,attachment_id,input_version,input_fingerprint,idempotency_key,status,provider,model,prompt_version,created_by,created_at) VALUES(?,?,?,?,'ocr',?,?,?,?,'running','test','offline-vision',?,?,?)",
                     (run_id, case.employee.firm_id, case.company, event["id"], attachment_id, event["version"], uuid4().hex, uuid4().hex, EXTRACTION_PROMPT_VERSION, case.employee.user_id, stamp))
        conn.execute("UPDATE ai_runs SET status='succeeded',result_json=?,finished_at=? WHERE id=?", (json.dumps({"documents": [doc or document()]}, ensure_ascii=False), stamp, run_id))
    return run_id


def draft_value(case, draft_id):
    return next(row for row in case.ledger.get_book(case.reviewer, case.book)["drafts"] if row["id"] == draft_id)


def approve(case, draft_id):
    value = draft_value(case, draft_id)
    return case.ledger.approve(case.reviewer, case.book, draft_id, ApprovalRequest(expected_version=value["version"]))


def export(case, draft_id):
    return case.ledger.export(case.reviewer, case.book, ExportRequest(draft_ids=[draft_id], idempotency_key=uuid4().hex))


def finding_count(case):
    with case.db.transaction(actor=case.reviewer) as conn:
        return conn.execute("SELECT COUNT(*) FROM ai_findings WHERE company_id=?", (case.company,)).fetchone()[0]


def bank_draft(case):
    case.ledger.import_sources(case.reviewer, case.book, ImportRequest(source_name="银行测试文件", rows=[SourceRow(
        external_id=uuid4().hex, date="2026-10-03", description="办公耗材", counterparty="耗材商店",
        amount="0.30", direction="expense", evidence_ref="银行测试回单",
    )]))
    case.ledger.suggestions(case.reviewer, case.book)
    return next(row for row in case.ledger.get_book(case.reviewer, case.book)["drafts"] if row["source_kind"] == "bank")


def link_bank(case, event, bank):
    with case.db.transaction(write=True, actor=case.reviewer) as conn:
        conn.execute("INSERT INTO ai_bank_links(bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                     (bank["source_id"], case.reviewer.firm_id, case.company, event["id"], case.book, case.reviewer.user_id, now()))


def test_ledger_explicit_pause_preserves_existing_review_export(guard_case):
    case = guard_case
    _, draft_id, _ = business_draft(case)
    assert draft_value(case, draft_id)["issues"] == []
    assert approve(case, draft_id)["status"] == "approved"
    assert export(case, draft_id)["count"] == 1
    assert finding_count(case) == 0


def test_ledger_enabled_policy_missing_ocr_blocks_review_without_writing_findings(guard_case):
    case = guard_case
    _, draft_id, _ = business_draft(case)
    policy(case)
    before = finding_count(case)
    value = draft_value(case, draft_id)
    assert any("尚未成功识别" in issue for issue in value["issues"])
    with pytest.raises(AccountingError, match="票据核对未完成"):
        approve(case, draft_id)
    assert finding_count(case) == before
    assert draft_value(case, draft_id)["status"] == "draft"
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE object_id=? AND action='draft.approve'", (draft_id,)).fetchone()[0] == 0


def test_ledger_enabling_policy_after_old_review_rechecks_before_export(guard_case):
    case = guard_case
    _, draft_id, _ = business_draft(case)
    approve(case, draft_id)
    policy(case)
    with pytest.raises(AccountingError, match="票据核对未完成"):
        export(case, draft_id)
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (draft_id,)).fetchone()[0] == "approved"
        assert conn.execute("SELECT COUNT(*) FROM exports WHERE book_id=?", (case.book,)).fetchone()[0] == 0


def test_ledger_clean_current_ocr_can_review_export_without_model_config_or_network(guard_case, monkeypatch):
    case = guard_case
    event, draft_id, attachment_id = business_draft(case)
    policy(case)
    recorded_ocr(case, event, attachment_id)
    monkeypatch.setenv("ACCOUNTING_AI_BASE_URL", "invalid://should-not-be-read")
    monkeypatch.setenv("ACCOUNTING_AI_MAX_CONCURRENCY", "bad")

    def no_network(*args, **kwargs):
        raise AssertionError("审核与导出不得调用外部模型")

    monkeypatch.setattr(OpenAICompatibleProvider, "extract_invoice", no_network)
    monkeypatch.setattr(OpenAICompatibleProvider, "suggest_journal", no_network)
    assert case.ledger.intelligence is None
    for finding in case.ai.reconcile(case.reviewer, event["id"])["findings"]:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            case.ai.resolve(
                case.reviewer,
                finding["id"],
                "合成原件已由会计独立核对，确认识别结果与业务一致",
            )
    assert draft_value(case, draft_id)["issues"] == []
    assert approve(case, draft_id)["status"] == "approved"
    assert export(case, draft_id)["count"] == 1
    assert finding_count(case) == 0


def test_ledger_current_document_business_amount_mismatch_blocks_review(guard_case):
    case = guard_case
    event, draft_id, attachment_id = business_draft(case)
    policy(case)
    recorded_ocr(case, event, attachment_id, document(gross="1.00"))
    assert any("票据合计与业务事项金额不一致" in issue for issue in draft_value(case, draft_id)["issues"])
    with pytest.raises(AccountingError, match="票据合计与业务事项金额不一致"):
        approve(case, draft_id)


def test_ledger_manual_current_exception_review_survives_canonical_approval_and_export(guard_case):
    case = guard_case
    event, draft_id, _ = business_draft(case)
    policy(case)
    assessment = case.ai.reconcile(case.reviewer, event["id"])
    for item in assessment["findings"]:
        if item["severity"] == "blocking":
            case.ai.resolve(case.reviewer, item["id"], "已直接核验票据原件，金额无误，保留人工核票依据")
    assert draft_value(case, draft_id)["issues"] == []
    before = finding_count(case)
    approve(case, draft_id)
    assert export(case, draft_id)["count"] == 1
    assert finding_count(case) == before


def test_ledger_new_ocr_facts_do_not_reuse_prior_manual_exception_review(guard_case):
    case = guard_case
    event, draft_id, attachment_id = business_draft(case)
    policy(case)
    recorded_ocr(case, event, attachment_id, document(gross="1.00"))
    assessment = case.ai.reconcile(case.reviewer, event["id"])
    for item in assessment["findings"]:
        if item["severity"] == "blocking":
            case.ai.resolve(case.reviewer, item["id"], "已核对本次原件，记录金额差异原因")
    assert draft_value(case, draft_id)["issues"] == []
    recorded_ocr(case, event, attachment_id, document(gross="2.00"))
    with pytest.raises(AccountingError, match="票据合计与业务事项金额不一致"):
        approve(case, draft_id)


def test_ledger_bound_shared_intelligence_and_unbound_fallback_apply_same_guards(guard_case):
    case = guard_case
    _, draft_id, _ = business_draft(case)
    policy(case)
    first = draft_value(case, draft_id)["issues"]
    case.ledger.intelligence = case.ai
    assert draft_value(case, draft_id)["issues"] == first
    with pytest.raises(AccountingError, match="票据核对未完成"):
        approve(case, draft_id)


def test_ledger_linked_bank_cannot_be_reviewed_even_with_company_policy_off(guard_case):
    case = guard_case
    event, draft_id, _ = business_draft(case, paid=True)
    bank = bank_draft(case)
    link_bank(case, event, bank)
    assert any("禁止重复审核或导出" in issue for issue in draft_value(case, bank["id"])["issues"])
    with pytest.raises(AccountingError, match="禁止重复审核或导出"):
        approve(case, bank["id"])
    assert approve(case, draft_id)["status"] == "approved"
    assert export(case, draft_id)["count"] == 1
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE book_id=?", (case.book,)).fetchone()[0] == 2
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (bank["id"],)).fetchone()[0] == "draft"


def test_ledger_linked_bank_export_has_independent_defense_after_external_status_change(guard_case):
    case = guard_case
    event, _, _ = business_draft(case, paid=True)
    bank = bank_draft(case)
    link_bank(case, event, bank)
    # Simulate an out-of-band status change; the public approve API rejects it.
    with case.db.transaction(write=True, actor=case.reviewer) as conn:
        conn.execute("UPDATE drafts SET status='approved' WHERE id=?", (bank["id"],))
    with pytest.raises(AccountingError, match="禁止重复审核或导出"):
        export(case, bank["id"])
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT COUNT(*) FROM exports WHERE book_id=?", (case.book,)).fetchone()[0] == 0


def test_ledger_unlink_keeps_original_and_restores_independent_bank_workflow(guard_case):
    case = guard_case
    event, _, _ = business_draft(case, paid=True)
    bank = bank_draft(case)
    link_bank(case, event, bank)
    with case.db.transaction(write=True, actor=case.reviewer) as conn:
        conn.execute("DELETE FROM ai_bank_links WHERE bank_source_id=?", (bank["source_id"],))
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE id=?", (bank["source_id"],)).fetchone()[0] == 1
    assert draft_value(case, bank["id"])["issues"] == []
    assert approve(case, bank["id"])["status"] == "approved"
    assert export(case, bank["id"])["count"] == 1


def test_ledger_postgres_closed_period_hides_mapping_but_freezes_staff_processing(guard_case):
    case = guard_case
    if not case.db.is_postgres:
        pytest.skip("PostgreSQL 私有冻结函数边界")
    event, _, _ = business_draft(case)
    restore_period_state(case.ledger.db, case.book)
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_mappings WHERE event_id=?", (event["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT accounting.ai_event_open(?,?,?)", (event["id"], case.employee.firm_id, case.company)).fetchone()[0] is False
