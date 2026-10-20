"""Stage 4 domain tests use explicit synthetic model output, never a live supplier."""
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.accounting.database import Database
from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import ApprovalRequest, BookCreate, BookMemberGrant, CompanyCreate, CompanyMemberGrant, DepartmentCreate, DraftUpdate, ExportRequest, FirmCreate, FirmMemberGrant, ImportRequest, SourceRow
from app.accounting.service import AccountingService
from app.business.models import BusinessEventCreate, BusinessEventUpdate, HandoffRequest
from app.business.service import BusinessService
from app.business.storage import AttachmentStore
from app.intelligence.models import InvoiceExtraction, JournalSuggestion, ProviderResult, ProviderUsage
from app.intelligence.provider import AISettings, EXTRACTION_PROMPT_VERSION, JOURNAL_PROMPT_VERSION, ProviderError
from app.intelligence.requests import BankLinkRequest, OCRRequest, ProposalRequest
from app.intelligence.service import IntelligenceService
from app.main import create_app
from app.closing.models import CloseRequest, PreflightRequest, SnapshotReview
from app.closing.service import ClosingService
from app.delivery.models import ReceiptCreate
from app.delivery.service import DeliveryService
from app.operations.pricing import Pricing, Rate
from uuid import uuid4
import json


def invoice_document(amount="0.30", net_amount="0.30", tax_amount="0.00", **changes):
    values = {
        "invoice_number": "MVP-2026-001", "issued_on": "2026-10-03", "seller_name": "耗材商店",
        "seller_tax_id": "913101TESTSELLER", "buyer_name": "第一客户企业", "buyer_tax_id": "913101TESTBUYER",
        "gross_amount": amount, "net_amount": net_amount, "tax_amount": tax_amount, "currency": "CNY",
        **changes,
    }
    text = "\n".join(f"{field}: {value}" for field, value in values.items() if value is not None)
    return {
        "document_type": "invoice", **values, "pages": [{"page": 1, "raw_text": text}],
        "evidence": [{"field": field, "page": 1, "quote": f"{field}: {value}"} for field, value in values.items() if value is not None],
    }


class FakeProvider:
    """Explicit fixture: no OCR inference and no network requests."""
    def __init__(self, document=None, settings=None):
        self.settings = settings or AISettings(base_url="https://supplier.example.invalid/v1", api_key="test-only-not-a-secret", vision_model="synthetic-vision", journal_model="synthetic-journal")
        self.document = document or invoice_document()
        self.extract_calls = 0
        self.proposal_calls = 0
        self.failure = None
        self.extract_hook = None
        self.proposal_hook = None
        self.proposal_mutator = None
        self.contexts = []
        self.usage = None

    def extract_invoice(self, pages, **kwargs):
        self.extract_calls += 1
        assert pages and pages[0].image_data_url.startswith("data:image/")
        if not self.settings.extraction_available:
            raise ProviderError("unavailable", "测试供应商尚未配置")
        if self.extract_hook:
            self.extract_hook()
        if self.failure:
            raise self.failure
        output = InvoiceExtraction.model_validate({"documents": [self.document]})
        return ProviderResult(output, "synthetic-test-provider", self.settings.vision_model, EXTRACTION_PROMPT_VERSION, self.usage)

    def suggest_journal(self, context, **kwargs):
        self.proposal_calls += 1
        self.contexts.append(context)
        if not self.settings.suggestion_available:
            raise ProviderError("unavailable", "测试分录供应商尚未配置")
        if self.failure:
            raise self.failure
        if self.proposal_hook:
            self.proposal_hook()
        document = context["documents"][0] if context["documents"] else None
        refs = [{"attachment_id": document["attachment_id"], "extraction_id": document["extraction_id"], "field": "gross_amount", "page": document["evidence"][0]["page"]}] if document else []
        amount = context["business"]["amount"]
        output = {
            "description": "办公耗材发票核对后的分录建议", "lines": [
                {"account_code": "6602.02", "debit": amount, "credit": "0.00"},
                {"account_code": "1002" if context["business"]["payment_status"] == "paid" else "2241", "debit": "0.00", "credit": amount},
            ], "reasoning": "测试已审核业务事项，费用待企业付款，使用应付款科目。",
            "evidence_refs": refs, "warnings": [],
        }
        if self.proposal_mutator:
            self.proposal_mutator(output)
        validated = JournalSuggestion.model_validate(output)
        return ProviderResult(validated, "synthetic-test-provider", self.settings.journal_model, JOURNAL_PROMPT_VERSION, self.usage)


@pytest.fixture
def ai_case(tmp_path):
    db = Database(tmp_path / "intelligence-workflow.db")
    directory, ledger, business = DirectoryService(db), AccountingService(db), BusinessService(db)
    def register(name):
        identity = SimpleNamespace(user_id=name, display_name=name, email=f"{name}@example.test")
        directory.register_identity(identity)
        return identity
    owner = register("owner")
    firm = directory.create_firm(owner.user_id, FirmCreate(name="第四阶段合成资料试点"))
    manager = Actor(owner.user_id, firm["id"])
    company = directory.create_company(manager, CompanyCreate(name="第一客户企业", tax_id="913101TESTBUYER"))
    other_company = directory.create_company(manager, CompanyCreate(name="第二客户企业"))
    department = directory.create_department(manager, company["id"], DepartmentCreate(name="业务部"))
    book = ledger.create_book(manager, BookCreate(name="第一客户账套", period="2026-10", company_id=company["id"]))
    other_book = ledger.create_book(manager, BookCreate(name="第二客户账套", period="2026-10", company_id=other_company["id"]))
    actors = {}
    for name, role in (("employee", "employee"), ("peer", "employee"), ("approver", "employee"), ("finance", "finance")):
        person = register(name)
        directory.grant_company_member(manager, company["id"], CompanyMemberGrant(email=person.email, role=role, department_id=department["id"]))
        actors[name] = Actor(person.user_id, firm["id"])
    outsider = register("outsider")
    directory.grant_company_member(manager, other_company["id"], CompanyMemberGrant(email=outsider.email, role="employee"))
    actors["outsider"] = Actor(outsider.user_id, firm["id"])
    accountant = register("accountant")
    directory.grant_firm_member(manager, FirmMemberGrant(email=accountant.email, role="reviewer"))
    directory.grant_book_member(manager, book["id"], accountant.user_id, BookMemberGrant(role="reviewer"))
    directory.grant_book_member(manager, book["id"], "finance", BookMemberGrant(role="editor"))
    actors["accountant"] = Actor(accountant.user_id, firm["id"])
    business.grant_department_approver(manager, company["id"], department["id"], "approver")
    storage, provider = AttachmentStore(tmp_path / "private"), FakeProvider()
    intelligence = IntelligenceService(db, storage, provider)
    intelligence.company_settings(manager, company["id"], enabled=True)
    return SimpleNamespace(db=db, directory=directory, ledger=ledger, business=business, intelligence=intelligence, storage=storage, provider=provider, manager=manager, actors=actors, company=company, other_company=other_company, book=book, other_book=other_book)


def create_event(ai_case, actor="employee", template="reimbursement", **changes):
    payload = {"occurred_on": "2026-10-03", "description": "垫付办公耗材", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office", **changes}
    event = ai_case.business.create(ai_case.actors[actor], BusinessEventCreate(company_id=ai_case.company["id"], template_id=template, payload=payload))
    output = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(output, "PNG")
    attachment = ai_case.storage.put("合成发票.png", "image/png", output.getvalue())
    return ai_case.business.add_attachment(ai_case.actors[actor], event["id"], event["version"], attachment)


def extract(ai_case, event, actor="employee", key="synthetic-ocr-key"):
    return ai_case.intelligence.extract(ai_case.actors[actor], event["id"], OCRRequest(attachment_id=event["attachments"][0]["id"], expected_version=event["version"], idempotency_key=key))


def handoff(ai_case, event):
    event = ai_case.business.submit(ai_case.actors["employee"], event["id"], event["version"])
    event = ai_case.business.approve_department(ai_case.actors["approver"], event["id"], event["version"])
    event = ai_case.business.approve_finance(ai_case.actors["finance"], event["id"], event["version"])
    return ai_case.business.handoff(ai_case.actors["finance"], event["id"], HandoffRequest(expected_version=event["version"], book_id=ai_case.book["id"], idempotency_key="synthetic-handoff-" + event["id"]))


def test_config_is_public_and_only_company_manager_can_enable(ai_case):
    config = ai_case.intelligence.config(ai_case.actors["employee"])
    assert config["vision_ready"] and config["journal_ready"]
    assert "api_key" not in str(config) and "test-only-not-a-secret" not in str(config)
    assert ai_case.intelligence.company_settings(ai_case.actors["employee"], ai_case.company["id"]) == {"enabled": True, "can_manage": False}
    with pytest.raises(AccountingError) as denied:
        ai_case.intelligence.company_settings(ai_case.actors["employee"], ai_case.company["id"], enabled=False)
    assert denied.value.status == 403


def test_ocr_stores_strict_output_once_without_mutating_business_facts(ai_case):
    event = create_event(ai_case)
    run = extract(ai_case, event)
    assert run["status"] == "succeeded" and run["result"]["documents"][0]["gross_amount"] == "0.30"
    assert extract(ai_case, event)["id"] == run["id"] and ai_case.provider.extract_calls == 1
    latest = ai_case.business.get_event(ai_case.actors["employee"], event["id"])
    assert latest["version"] == event["version"] and latest["payload"] == event["payload"]
    detail = ai_case.intelligence.reconcile(ai_case.actors["finance"], event["id"])
    assert detail["findings"] == [] and detail["bank_candidates"] == []


def test_disabled_company_does_not_send_employee_evidence(ai_case):
    event = create_event(ai_case)
    ai_case.intelligence.company_settings(ai_case.manager, ai_case.company["id"], enabled=False)
    with pytest.raises(AccountingError, match="尚未启用"):
        extract(ai_case, event)
    assert ai_case.provider.extract_calls == 0


def test_unconfigured_supplier_returns_persisted_failure_instead_of_fake_success(ai_case):
    event = create_event(ai_case)
    ai_case.provider.settings = AISettings()
    run = extract(ai_case, event)
    assert run["status"] == "failed" and run["result"] is None and run["error_code"] == "unavailable"
    assert not ai_case.intelligence.config(ai_case.actors["employee"])["vision_ready"]
    assert ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])["findings"]


@pytest.mark.parametrize("actor", ["peer", "outsider", "accountant"])
def test_ocr_and_result_reads_do_not_cross_business_scope(ai_case, actor):
    event = create_event(ai_case)
    with pytest.raises(AccountingError) as denied:
        extract(ai_case, event, actor)
    assert denied.value.status == 404 and ai_case.provider.extract_calls == 0
    with pytest.raises(AccountingError) as denied:
        ai_case.intelligence.detail(ai_case.actors[actor], event["id"])
    assert denied.value.status == 404


def test_attachment_must_belong_to_the_authorized_event(ai_case):
    own = create_event(ai_case)
    peer = create_event(ai_case, "peer")
    with pytest.raises(AccountingError) as denied:
        ai_case.intelligence.extract(ai_case.actors["employee"], own["id"], OCRRequest(attachment_id=peer["attachments"][0]["id"], expected_version=own["version"], idempotency_key="cross-attachment-key"))
    assert denied.value.status == 404 and ai_case.provider.extract_calls == 0


def test_failure_retry_keeps_one_claim_and_explicit_new_request_is_a_new_call(ai_case):
    event = create_event(ai_case)
    ai_case.provider.failure = ProviderError("timeout", "测试模型超时")
    failed = extract(ai_case, event)
    assert failed["status"] == "failed" and failed["error_code"] == "timeout"
    assert extract(ai_case, event)["id"] == failed["id"] and ai_case.provider.extract_calls == 1
    ai_case.provider.failure = None
    succeeded = extract(ai_case, event, key="explicit-second-ocr-key")
    assert succeeded["status"] == "succeeded" and succeeded["id"] != failed["id"]
    assert ai_case.provider.extract_calls == 2


def test_missing_tax_is_blocking_and_cannot_be_treated_as_zero_or_self_resolved(ai_case):
    event = create_event(ai_case)
    ai_case.provider.document = invoice_document(net_amount=None, tax_amount=None)
    extract(ai_case, event)
    instantaneous = ai_case.intelligence.detail(ai_case.actors["finance"], event["id"])
    tax = next(issue for issue in instantaneous["findings"] if issue["code"].endswith("_tax_missing"))
    assert tax["severity"] == "blocking" and tax["id"]
    reconciled = ai_case.intelligence.reconcile(ai_case.actors["finance"], event["id"])
    tax = next(issue for issue in reconciled["findings"] if issue["code"].endswith("_tax_missing"))
    with pytest.raises(AccountingError) as denied:
        ai_case.intelligence.resolve(ai_case.actors["employee"], tax["id"], "申请人自称已核对")
    assert denied.value.status == 403
    resolved = ai_case.intelligence.resolve(ai_case.actors["finance"], tax["id"], "已补充完整发票，并人工核对缺失税额")
    assert resolved["resolved"] and "补充完整发票" in resolved["resolution_reason"]


def test_old_resolution_does_not_clear_a_mismatch_after_business_facts_change(ai_case):
    event = create_event(ai_case, amount="0.40")
    extract(ai_case, event)
    reconciled = ai_case.intelligence.reconcile(ai_case.actors["finance"], event["id"])
    mismatch = next(issue for issue in reconciled["findings"] if "amount" in issue["code"])
    ai_case.intelligence.resolve(ai_case.actors["finance"], mismatch["id"], "测试已核对其中差异")
    assert next(issue for issue in ai_case.intelligence.detail(ai_case.actors["finance"], event["id"])["findings"] if issue["code"] == mismatch["code"])["resolved"]
    updated = ai_case.business.update(ai_case.actors["employee"], event["id"], BusinessEventUpdate(expected_version=event["version"], payload={**event["payload"], "amount": "0.50"}))
    current = ai_case.intelligence.detail(ai_case.actors["finance"], updated["id"])
    changed = next(issue for issue in current["findings"] if issue["code"] == mismatch["code"])
    assert not changed["resolved"] and changed["id"] is None


def test_immediate_ocr_missing_finding_is_registered_by_reconcile(ai_case):
    event = create_event(ai_case)
    immediate = ai_case.intelligence.detail(ai_case.actors["finance"], event["id"])
    assert immediate["findings"] and all(issue["id"] is None for issue in immediate["findings"])
    registered = ai_case.intelligence.reconcile(ai_case.actors["finance"], event["id"])
    assert all(issue["id"] for issue in registered["findings"])


def test_revocation_during_model_call_abandons_output(ai_case):
    event = create_event(ai_case)
    ai_case.provider.extract_hook = lambda: ai_case.directory.revoke_company_member(ai_case.manager, ai_case.company["id"], "employee")
    with pytest.raises(AccountingError):
        extract(ai_case, event)
    with ai_case.db.transaction() as conn:
        row = conn.execute("SELECT status,result_json FROM ai_runs WHERE event_id=?", (event["id"],)).fetchone()
    assert row["status"] == "stale" and row["result_json"] is None
    assert ai_case.provider.extract_calls == 1


def draft(ai_case, draft_id):
    return next(row for row in ai_case.ledger.get_book(ai_case.actors["accountant"], ai_case.book["id"])["drafts"] if row["id"] == draft_id)


def propose(ai_case, draft_id, version=1, actor="accountant", key="synthetic-proposal-key"):
    return ai_case.intelligence.propose(ai_case.actors[actor], ai_case.book["id"], draft_id, ProposalRequest(expected_version=version, idempotency_key=key))


def test_positive_ocr_proposal_apply_independent_review_and_export_is_exact_and_idempotent(ai_case):
    event = create_event(ai_case)
    extraction = extract(ai_case, event)
    receipt = handoff(ai_case, event)
    initial = draft(ai_case, receipt["draft_id"])
    proposal = propose(ai_case, initial["id"])
    assert proposal["status"] == "succeeded", proposal
    refs = proposal["result"]["evidence_refs"]
    assert refs[0]["extraction_id"] == extraction["id"] and refs[0]["attachment_id"] == event["attachments"][0]["id"]
    assert propose(ai_case, initial["id"])["id"] == proposal["id"] and ai_case.provider.proposal_calls == 1
    assert draft(ai_case, initial["id"])["version"] == initial["version"]
    employee_view = ai_case.intelligence.detail(ai_case.actors["employee"], event["id"])
    public_proposal = next(row for row in employee_view["runs"] if row["id"] == proposal["id"])
    assert public_proposal["result"] is None and public_proposal["book_id"] is None and public_proposal["draft_id"] is None
    with pytest.raises(AccountingError):
        ai_case.intelligence.apply(ai_case.actors["employee"], proposal["id"], 1)
    applied = ai_case.intelligence.apply(ai_case.actors["accountant"], proposal["id"], 1)
    assert applied["status"] == "draft" and applied["version"] == 2 and applied["issues"] == []
    assert applied["description"] == proposal["result"]["description"]
    assert ai_case.intelligence.apply(ai_case.actors["accountant"], proposal["id"], 1)["version"] == 2
    with pytest.raises(AccountingError):
        ai_case.ledger.approve(ai_case.actors["finance"], ai_case.book["id"], initial["id"], ApprovalRequest(expected_version=2))
    reviewed = ai_case.ledger.approve(ai_case.actors["accountant"], ai_case.book["id"], initial["id"], ApprovalRequest(expected_version=2))
    assert reviewed["status"] == "approved"
    request = ExportRequest(draft_ids=[initial["id"]], idempotency_key="synthetic-export-key")
    exported = ai_case.ledger.export(ai_case.actors["accountant"], ai_case.book["id"], request)
    assert "0.30" in exported["csv"] and "2241" in exported["csv"] and exported["count"] == 1
    assert ai_case.ledger.export(ai_case.actors["accountant"], ai_case.book["id"], request)["id"] == exported["id"]
    frozen = ai_case.intelligence.detail(ai_case.actors["accountant"], event["id"])["permissions"]
    assert not frozen["process"] and not frozen["apply_proposal"] and not frozen["resolve"]


def test_same_customer_invoice_export_delivery_and_close(ai_case):
    ai_case.provider.usage = ProviderUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    ai_case.intelligence._meter().pricing = Pricing(
        ocr=Rate("1.000000", "2.000000"),
        proposal=Rate("1.000000", "2.000000"),
    )
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    draft = next(
        row
        for row in ai_case.ledger.get_book(ai_case.actors["accountant"], ai_case.book["id"])["drafts"]
        if row["id"] == receipt["draft_id"]
    )
    proposal = propose(ai_case, draft["id"])
    ai_case.intelligence.apply(ai_case.actors["accountant"], proposal["id"], 1)
    approved = ai_case.ledger.approve(
        ai_case.actors["accountant"],
        ai_case.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=2),
    )
    export = ai_case.ledger.export(
        ai_case.actors["accountant"],
        ai_case.book["id"],
        ExportRequest(draft_ids=[draft["id"]], idempotency_key="same-customer-export"),
    )
    delivery = DeliveryService(ai_case.db)
    body = json.loads(delivery.template(
        ai_case.actors["accountant"], ai_case.book["id"], export["id"]
    ))
    for item in body["items"]:
        item.update(status="accepted", external_voucher_id="客户凭证-001")
    created = delivery.create_receipt(
        ai_case.actors["accountant"],
        ai_case.book["id"],
        export["id"],
        ReceiptCreate(**body, idempotency_key=uuid4().hex, reason="同一客户合成回执"),
    )
    assert created["summary"]["verified"] == 1
    closing = ClosingService(ai_case.db)
    snapshot = closing.preflight(
        ai_case.actors["accountant"],
        ai_case.book["id"],
        PreflightRequest(idempotency_key=uuid4().hex),
    )
    assert snapshot["report"]["passed"], snapshot["report"]["counts"]
    closing.review(
        ai_case.actors["accountant"],
        ai_case.book["id"],
        snapshot["id"],
        SnapshotReview(reason="同一客户回执与导出已核对", materials_confirmed=True),
    )
    request = CloseRequest(snapshot_id=snapshot["id"], reason="同一客户合成月结")
    closed = closing.close(ai_case.actors["accountant"], ai_case.book["id"], request)
    assert closed["status"] == "closed"
    assert closing.close(ai_case.actors["accountant"], ai_case.book["id"], request)["replayed"]
    assert approved["status"] == "approved"


@pytest.mark.parametrize("fault", ["account", "balance", "unpaid_bank", "missing_refs", "wrong_ref_page"])
def test_invalid_model_journal_never_changes_the_existing_draft(ai_case, fault):
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    original = draft(ai_case, receipt["draft_id"])
    def corrupt(output):
        if fault == "account":
            output["lines"][0]["account_code"] = "nonexistent-account"
        elif fault == "balance":
            output["lines"][0]["debit"] = "0.31"
        elif fault == "unpaid_bank":
            output["lines"][1]["account_code"] = "1002"
        elif fault == "missing_refs":
            output["evidence_refs"] = []
        else:
            output["evidence_refs"][0]["page"] = 2
    ai_case.provider.proposal_mutator = corrupt
    result = propose(ai_case, original["id"])
    assert result["status"] == "failed" and result["error_code"] == "invalid_proposal" and result["result"] is None
    assert draft(ai_case, original["id"]) == original
    with pytest.raises(AccountingError):
        ai_case.intelligence.apply(ai_case.actors["accountant"], result["id"], original["version"])


def test_human_resolution_is_required_before_applying_amount_mismatch_proposal(ai_case):
    event = create_event(ai_case, amount="0.40")
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    suggestion = propose(ai_case, receipt["draft_id"])
    assert suggestion["status"] == "succeeded"
    with pytest.raises(AccountingError, match="异常"):
        ai_case.intelligence.apply(ai_case.actors["accountant"], suggestion["id"], 1)
    findings = ai_case.intelligence.detail(ai_case.actors["accountant"], event["id"])["findings"]
    for finding in findings:
        if finding["severity"] == "blocking" and not finding["resolved"]:
            ai_case.intelligence.resolve(ai_case.actors["accountant"], finding["id"], "已核对完整凭据与业务事项，差异经人工确认")
    result = ai_case.intelligence.apply(ai_case.actors["accountant"], suggestion["id"], 1)
    assert result["status"] == "draft" and result["version"] == 2


def test_changed_draft_version_rejects_old_suggestion_without_mutation(ai_case):
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    suggestion = propose(ai_case, receipt["draft_id"])
    original = draft(ai_case, receipt["draft_id"])
    updated = ai_case.ledger.update_draft(ai_case.actors["accountant"], ai_case.book["id"], original["id"], DraftUpdate(expected_version=1, description="会计已经手工修改", lines=original["lines"]))
    with pytest.raises(AccountingError):
        ai_case.intelligence.apply(ai_case.actors["accountant"], suggestion["id"], updated["version"])
    assert draft(ai_case, original["id"])["description"] == "会计已经手工修改"


def test_other_book_and_actor_cannot_generate_or_apply_a_business_proposal(ai_case):
    event = create_event(ai_case)
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    with pytest.raises(AccountingError):
        ai_case.intelligence.propose(ai_case.actors["accountant"], ai_case.other_book["id"], receipt["draft_id"], ProposalRequest(expected_version=1, idempotency_key="cross-book-proposal"))
    with pytest.raises(AccountingError):
        propose(ai_case, receipt["draft_id"], actor="employee")
    assert ai_case.provider.proposal_calls == 0
    proposal = propose(ai_case, receipt["draft_id"])
    with pytest.raises(AccountingError):
        ai_case.intelligence.apply(ai_case.actors["outsider"], proposal["id"], 1)
    assert draft(ai_case, receipt["draft_id"])["version"] == 1


def import_bank(ai_case, book_id=None, external_id="bank-synthetic-1", amount="0.30", direction="expense"):
    target = book_id or ai_case.book["id"]
    row = SourceRow(external_id=external_id, date="2026-10-03", description="办公耗材银行付款", counterparty="耗材商店", amount=amount, direction=direction, evidence_ref="synthetic-bank-receipt")
    ai_case.ledger.import_sources(ai_case.manager, target, ImportRequest(source_name="合成银行流水.csv", rows=[row]))
    ai_case.ledger.suggestions(ai_case.manager, target)
    return next(source for source in ai_case.ledger.get_book(ai_case.manager, target)["sources"] if source["external_id"] == external_id)


def test_paid_event_bank_link_blocks_duplicate_bank_draft_review_and_export(ai_case):
    event = create_event(ai_case, template="procurement_expense", payment_status="paid")
    extract(ai_case, event)
    receipt = handoff(ai_case, event)
    bank = import_bank(ai_case)
    detail = ai_case.intelligence.detail(ai_case.actors["accountant"], event["id"])
    assert bank["id"] in [candidate["id"] for candidate in detail["bank_candidates"]]
    assert any(issue["code"] == "bank_match_review" for issue in detail["findings"])
    linked = ai_case.intelligence.link_bank(ai_case.actors["accountant"], event["id"], BankLinkRequest(bank_source_ids=[bank["id"]], reason="已核对银行回单金额及交易对方"))
    assert linked["bank_links"][0]["bank_source_id"] == bank["id"]
    assert not any(issue["code"] == "bank_match_review" for issue in linked["findings"])
    bank_draft = next(row for row in ai_case.ledger.get_book(ai_case.actors["accountant"], ai_case.book["id"])["drafts"] if row["source_id"] == bank["id"])
    assert any("重复" in issue for issue in bank_draft["issues"])
    with pytest.raises(AccountingError):
        ai_case.ledger.approve(ai_case.actors["accountant"], ai_case.book["id"], bank_draft["id"], ApprovalRequest(expected_version=bank_draft["version"]))
    proposal = propose(ai_case, receipt["draft_id"])
    assert proposal["status"] == "succeeded"
    applied = ai_case.intelligence.apply(ai_case.actors["accountant"], proposal["id"], 1)
    assert applied["issues"] == [] and any(line["account_code"] == "1002" and line["credit"] == "0.30" for line in applied["lines"])
    unlinked = ai_case.intelligence.unlink_bank(ai_case.actors["accountant"], event["id"], bank["id"], "发现关联回单需要重新核对")
    assert not unlinked["bank_links"] and any(issue["code"] == "bank_match_review" for issue in unlinked["findings"])


def test_bank_link_never_crosses_company_book_or_direction(ai_case):
    event = create_event(ai_case, template="procurement_expense", payment_status="paid")
    extract(ai_case, event)
    handoff(ai_case, event)
    cross_book = import_bank(ai_case, book_id=ai_case.other_book["id"])
    income = import_bank(ai_case, external_id="bank-income", direction="income")
    short = import_bank(ai_case, external_id="bank-short", amount="0.20")
    for source in (cross_book, income, short):
        with pytest.raises(AccountingError):
            ai_case.intelligence.link_bank(ai_case.actors["accountant"], event["id"], BankLinkRequest(bank_source_ids=[source["id"]], reason="测试错误关联"))
    assert ai_case.intelligence.detail(ai_case.actors["accountant"], event["id"])["bank_links"] == []


@pytest.fixture
def ai_client(tmp_path):
    provider = FakeProvider()
    app = create_app(tmp_path / "http-intelligence.db", seed=True, attachment_root=tmp_path / "http-private", intelligence_provider=provider)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 6789)) as client:
        client.provider = provider
        yield client


def http_auth(client, persona):
    response = client.get("/api/session", params={"persona": persona})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def http_create(client, employee, template="reimbursement", payment_status="unpaid"):
    company = client.get("/api/business/context", headers=employee).json()["companies"][0]
    response = client.post("/api/business/events", headers=employee, json={"company_id": company["id"], "template_id": template, "payload": {
        "occurred_on": "2026-10-03", "description": "第四阶段HTTP合成办公支出", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": payment_status, "category": "office",
    }})
    assert response.status_code == 201, response.text
    event = response.json()
    output = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(output, "PNG")
    uploaded = client.post(f"/api/business/events/{event['id']}/attachments?expected_version={event['version']}", headers=employee, files={"file": ("合成发票.png", output.getvalue(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json(), company


def http_handoff(client, event, employee, department, finance):
    path = f"/api/business/events/{event['id']}"
    for endpoint, headers in (("submit", employee), ("department-approve", department), ("finance-approve", finance)):
        response = client.post(path + "/" + endpoint, headers=headers, json={"expected_version": event["version"]})
        assert response.status_code == 200, response.text
        event = response.json()
    book = next(row for row in client.get("/api/workbench", headers=finance).json()["books"] if row["company_id"] == event["company_id"])
    response = client.post(path + "/handoff", headers=finance, json={"expected_version": event["version"], "book_id": book["id"], "idempotency_key": "synthetic-http-handoff"})
    assert response.status_code == 200, response.text
    return response.json(), book


def test_http_end_to_end_synthetic_ocr_apply_review_export_and_private_original(ai_client):
    client = ai_client
    employee, department, finance, accountant = [http_auth(client, role) for role in ("employee", "department", "finance", "accountant")]
    event, company = http_create(client, employee)
    client.provider.document = invoice_document(buyer_name=company["name"], buyer_tax_id=None)
    enabled = client.put(f"/api/companies/{company['id']}/intelligence/settings", headers=accountant, json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    body = {"attachment_id": event["attachments"][0]["id"], "expected_version": event["version"], "idempotency_key": "synthetic-http-ocr"}
    path = f"/api/business/events/{event['id']}"
    assert client.post(path + "/ocr", json=body).status_code == 401
    assert client.get(path + "/intelligence", headers=accountant).status_code == 404
    ocr = client.post(path + "/ocr", headers=employee, json=body)
    assert ocr.status_code == 200 and ocr.json()["status"] == "succeeded", ocr.text
    assert client.post(path + "/ocr", headers=employee, json=body).json()["id"] == ocr.json()["id"]
    receipt, book = http_handoff(client, event, employee, department, finance)
    proposal_url = f"/api/books/{book['id']}/drafts/{receipt['draft_id']}/ai-proposals"
    proposal_body = {"expected_version": 1, "idempotency_key": "synthetic-http-proposal"}
    assert client.post(proposal_url, headers=employee, json=proposal_body).status_code in {403, 404}
    proposal = client.post(proposal_url, headers=accountant, json=proposal_body)
    assert proposal.status_code == 200 and proposal.json()["status"] == "succeeded", proposal.text
    result_url = f"/api/intelligence/proposals/{proposal.json()['id']}/apply"
    assert client.post(result_url, headers=department, json={"expected_version": 1}).status_code in {403, 404}
    applied = client.post(result_url, headers=accountant, json={"expected_version": 1})
    assert applied.status_code == 200 and applied.json()["status"] == "draft" and applied.json()["version"] == 2, applied.text
    assert client.post(f"/api/books/{book['id']}/exports", headers=accountant, json={"draft_ids": [receipt["draft_id"]], "idempotency_key": "synthetic-premature-export"}).status_code == 409
    reviewed = client.post(f"/api/books/{book['id']}/drafts/{receipt['draft_id']}/approve", headers=accountant, json={"expected_version": 2})
    assert reviewed.status_code == 200, reviewed.text
    export = client.post(f"/api/books/{book['id']}/exports", headers=accountant, json={"draft_ids": [receipt["draft_id"]], "idempotency_key": "synthetic-http-export"})
    assert export.status_code == 200 and "0.30" in export.json()["csv"] and "2241" in export.json()["csv"], export.text
    original = client.get(f"/api/business/attachments/{event['attachments'][0]['id']}/content", headers=accountant)
    assert original.status_code == 200 and original.headers["content-type"] == "image/png"
    assert client.provider.extract_calls == 1 and client.provider.proposal_calls == 1


def test_http_same_customer_export_receipt_reaches_close_gate(ai_client):
    client = ai_client
    client.provider.usage = ProviderUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    client.app.state.intelligence._meter().pricing = Pricing(
        ocr=Rate("1.000000", "2.000000"),
        proposal=Rate("1.000000", "2.000000"),
    )
    employee, department, finance, accountant = [
        http_auth(client, role) for role in ("employee", "department", "finance", "accountant")
    ]
    event, company = http_create(client, employee)
    client.provider.document = invoice_document(buyer_name=company["name"], buyer_tax_id=None)
    assert client.put(
        f"/api/companies/{company['id']}/intelligence/settings",
        headers=accountant,
        json={"enabled": True},
    ).status_code == 200
    ocr = client.post(
        f"/api/business/events/{event['id']}/ocr",
        headers=employee,
        json={"attachment_id": event["attachments"][0]["id"], "expected_version": event["version"], "idempotency_key": "http-close-ocr"},
    )
    assert ocr.status_code == 200 and ocr.json()["status"] == "succeeded", ocr.text
    receipt, book = http_handoff(client, event, employee, department, finance)
    proposal = client.post(
        f"/api/books/{book['id']}/drafts/{receipt['draft_id']}/ai-proposals",
        headers=accountant,
        json={"expected_version": 1, "idempotency_key": "http-close-proposal"},
    )
    assert proposal.status_code == 200 and proposal.json()["status"] == "succeeded", proposal.text
    applied = client.post(
        f"/api/intelligence/proposals/{proposal.json()['id']}/apply",
        headers=accountant,
        json={"expected_version": 1},
    )
    assert applied.status_code == 200 and applied.json()["version"] == 2, applied.text
    reviewed = client.post(
        f"/api/books/{book['id']}/drafts/{receipt['draft_id']}/approve",
        headers=accountant,
        json={"expected_version": 2},
    )
    assert reviewed.status_code == 200, reviewed.text
    export = client.post(
        f"/api/books/{book['id']}/exports",
        headers=accountant,
        json={"draft_ids": [receipt["draft_id"]], "idempotency_key": "http-close-export"},
    )
    assert export.status_code == 200, export.text
    template = client.get(
        f"/api/books/{book['id']}/delivery/exports/{export.json()['id']}/template",
        headers=accountant,
        params={"format": "json"},
    )
    assert template.status_code == 200, template.text
    body = template.json()
    for item in body["items"]:
        item.update(status="accepted", external_voucher_id="客户凭证-HTTP-001")
    imported = client.post(
        f"/api/books/{book['id']}/delivery/exports/{export.json()['id']}/receipts",
        headers=accountant,
        json={**body, "idempotency_key": "http-close-receipt", "reason": "HTTP合成回执"},
    )
    assert imported.status_code == 201 and imported.json()["summary"]["verified"] == 1, imported.text
    preflight = client.post(
        f"/api/books/{book['id']}/closing/preflights",
        headers=accountant,
        json={"idempotency_key": "http-close-preflight"},
    )
    assert preflight.status_code == 201, preflight.text
    snapshot = preflight.json()
    # The HTTP fixture uses the seeded demo company, so unrelated seed books
    # leave explicit blockers. The clean same-customer close is covered by the
    # service-level test above; this test proves the HTTP close gate is visible.
    assert snapshot["report"]["passed"] is False
    assert {"background_pending", "unmatched_bank", "unreviewed"} <= set(
        snapshot["report"]["counts"]
    )


def test_http_scope_settings_and_unlink_reason_require_json_body(ai_client):
    client = ai_client
    employee, accountant = http_auth(client, "employee"), http_auth(client, "accountant")
    event, company = http_create(client, employee)
    setting_url = f"/api/companies/{company['id']}/intelligence/settings"
    denied = client.put(setting_url, headers=employee, json={"enabled": True})
    assert denied.status_code == 403
    assert client.put(setting_url, headers=accountant, json={"enabled": "true"}).status_code == 422
    client.put(setting_url, headers=accountant, json={"enabled": True})
    config = client.get("/api/intelligence/config", headers=employee)
    assert config.status_code == 200 and "test-only-not-a-secret" not in config.text and "api_key" not in config.text
    url = f"/api/business/events/{event['id']}/bank-links/nonexistent-source"
    assert client.request("DELETE", url + "?reason=legacy-query-reason", headers=accountant).status_code == 422
    assert client.get("/api/intelligence/exceptions?limit=0", headers=employee).status_code == 422
