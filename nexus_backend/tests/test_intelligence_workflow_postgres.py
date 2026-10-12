"""Real PostgreSQL exercises complete service payloads with synthetic OCR only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest
from app.accounting.service import fingerprint
from app.business.service import BusinessService
from app.business.storage import AttachmentStore
from app.intelligence.provider import EXTRACTION_PROMPT_VERSION
from app.intelligence.requests import BankLinkRequest, OCRRequest
from app.intelligence.service import IntelligenceService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow import FakeProvider, create_event, draft, extract, handoff, import_bank, invoice_document, propose

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


@pytest.fixture
def pg_ai(business_case, tmp_path):
    raw = business_case
    tax_id = "PG" + uuid4().hex
    with raw.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.companies SET tax_id=%s WHERE id=%s", (tax_id, raw.company))
    storage = AttachmentStore(tmp_path / "postgres-private")
    provider = FakeProvider(invoice_document(invoice_number="PG-" + uuid4().hex, buyer_tax_id=tax_id))
    intelligence = IntelligenceService(raw.db, storage, provider)
    intelligence.company_settings(raw.owner, raw.company, enabled=True)
    raw.service.intelligence = intelligence
    actors = {"employee": raw.employee, "peer": raw.colleague, "approver": raw.approver,
              "finance": raw.finance, "accountant": raw.reviewer, "viewer": raw.viewer,
              "outsider": raw.person("employee", company=raw.other_company)}
    return SimpleNamespace(db=raw.db, ledger=raw.service, business=BusinessService(raw.db),
                           directory=DirectoryService(raw.db), manager=raw.owner, actors=actors,
                           company={"id": raw.company}, other_company={"id": raw.other_company},
                           book={"id": raw.book}, other_book={"id": raw.other_book},
                           intelligence=intelligence, storage=storage, provider=provider, psycopg=raw.psycopg)


def admin_rows(case, sql, params):
    with case.psycopg.connect(ADMIN_URL) as conn:
        return conn.execute(sql, params).fetchall()


def test_pg_ai_full_pipeline_masks_proposals_and_requires_independent_accounting_review(pg_ai):
    case = pg_ai
    event = create_event(case)
    ocr = extract(case, event)
    assert ocr["status"] == "succeeded"
    assert extract(case, event)["id"] == ocr["id"]
    receipt = handoff(case, event)
    proposal = propose(case, receipt["draft_id"])
    assert proposal["status"] == "succeeded", proposal
    assert propose(case, receipt["draft_id"])["id"] == proposal["id"]
    for actor in (case.actors["employee"], case.actors["approver"]):
        detail = case.intelligence.detail(actor, event["id"])
        public = next(run for run in detail["runs"] if run["id"] == proposal["id"])
        assert public["result"] is None and public["book_id"] is None and public["draft_id"] is None
        assert detail["bank_candidates"] == [] and detail["bank_links"] == []
        with pytest.raises(AccountingError):
            case.intelligence.apply(actor, proposal["id"], 1)
    applied = case.intelligence.apply(case.actors["accountant"], proposal["id"], 1)
    assert applied["status"] == "draft" and applied["version"] == 2 and not applied["issues"]
    with pytest.raises(AccountingError):
        case.ledger.approve(case.actors["finance"], case.book["id"], applied["id"], ApprovalRequest(expected_version=2))
    reviewed = case.ledger.approve(case.actors["accountant"], case.book["id"], applied["id"], ApprovalRequest(expected_version=2))
    request = ExportRequest(draft_ids=[reviewed["id"]], idempotency_key="pg-export-" + uuid4().hex)
    exported = case.ledger.export(case.actors["accountant"], case.book["id"], request)
    assert exported["count"] == 1 and "0.30" in exported["csv"] and "2241" in exported["csv"]
    assert case.ledger.export(case.actors["accountant"], case.book["id"], request)["id"] == exported["id"]
    assert case.provider.extract_calls == 1 and case.provider.proposal_calls == 1
    assert not case.intelligence.detail(case.actors["employee"], event["id"])["permissions"]["process"]
    rows = admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_proposal_applications WHERE event_id=%s", (event["id"],))
    assert rows[0][0] == 1


@pytest.mark.parametrize("revoke", ["membership", "inactive_user", "suspended_client"])
def test_pg_ai_revocation_during_external_ocr_never_publishes_late_output(pg_ai, revoke):
    case = pg_ai
    event = create_event(case)

    def revoke_current_access():
        with case.psycopg.connect(ADMIN_URL) as conn:
            if revoke == "membership":
                conn.execute("DELETE FROM accounting.company_memberships WHERE company_id=%s AND user_id=%s", (case.company["id"], case.actors["employee"].user_id))
            elif revoke == "inactive_user":
                conn.execute("UPDATE accounting.users SET active=0 WHERE id=%s", (case.actors["employee"].user_id,))
            else:
                conn.execute("UPDATE accounting.firm_clients SET status='suspended' WHERE firm_id=%s AND company_id=%s", (case.manager.firm_id, case.company["id"]))

    case.provider.extract_hook = revoke_current_access
    with pytest.raises(AccountingError):
        extract(case, event)
    rows = admin_rows(case, "SELECT status,result_json,error_code FROM accounting.ai_runs WHERE event_id=%s", (event["id"],))
    assert rows == [("stale", None, "authorization_changed")]
    assert case.provider.extract_calls == 1
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_findings WHERE event_id=%s", (event["id"],))[0][0] == 0
    with pytest.raises(AccountingError):
        case.intelligence.detail(case.actors["employee"], event["id"])


def test_pg_ai_period_closed_during_external_ocr_abandons_without_output(pg_ai):
    case = pg_ai
    event = create_event(case)
    receipt = handoff(case, event)
    case.provider.extract_hook = lambda: case.ledger.close_period(case.actors["accountant"], case.book["id"], "2026-10")
    with pytest.raises(AccountingError):
        extract(case, receipt["event"])
    assert admin_rows(case, "SELECT status,result_json FROM accounting.ai_runs WHERE event_id=%s", (event["id"],)) == [("stale", None)]
    assert not case.intelligence.detail(case.actors["employee"], event["id"])["permissions"]["process"]


def test_pg_ai_draft_edit_during_external_proposal_rejects_old_output_atomically(pg_ai):
    case = pg_ai
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    original = draft(case, receipt["draft_id"])
    case.provider.proposal_hook = lambda: case.ledger.update_draft(case.actors["accountant"], case.book["id"], original["id"], DraftUpdate(expected_version=1, description="会计在模型请求期间修改摘要", lines=original["lines"]))
    with pytest.raises(AccountingError, match="版本已更新"):
        propose(case, original["id"])
    current = draft(case, original["id"])
    assert current["version"] == 2 and current["description"] == "会计在模型请求期间修改摘要"
    assert admin_rows(case, "SELECT status,result_json FROM accounting.ai_runs WHERE event_id=%s AND kind='proposal'", (event["id"],)) == [("stale", None)]
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_proposal_applications WHERE event_id=%s", (event["id"],))[0][0] == 0


def test_pg_ai_book_access_revoked_during_proposal_cannot_publish_or_read(pg_ai):
    case = pg_ai
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)

    def revoke_book():
        with case.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (case.book["id"], case.actors["accountant"].user_id))

    case.provider.proposal_hook = revoke_book
    with pytest.raises(AccountingError):
        propose(case, receipt["draft_id"])
    assert admin_rows(case, "SELECT status,result_json FROM accounting.ai_runs WHERE event_id=%s AND kind='proposal'", (event["id"],)) == [("stale", None)]
    with case.db.transaction(actor=case.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_runs WHERE event_id=?", (event["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["unknown_account", "unbalanced", "wrong_reference", "unpaid_bank"])
def test_pg_ai_invalid_proposal_keeps_original_draft_and_failed_run_has_no_body(pg_ai, fault):
    case = pg_ai
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    original = draft(case, receipt["draft_id"])

    def corrupt(output):
        if fault == "unknown_account":
            output["lines"][0]["account_code"] = "other-book-only-account"
        elif fault == "unbalanced":
            output["lines"][0]["debit"] = "0.31"
        elif fault == "wrong_reference":
            output["evidence_refs"][0]["attachment_id"] = "unrelated-private-attachment"
        else:
            output["lines"][1]["account_code"] = "1002"

    case.provider.proposal_mutator = corrupt
    result = propose(case, original["id"])
    assert result["status"] == "failed" and result["result"] is None and result["error_code"] == "invalid_proposal"
    assert draft(case, original["id"]) == original
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_proposal_applications WHERE event_id=%s", (event["id"],))[0][0] == 0


def test_pg_ai_concurrent_apply_retries_create_one_receipt_and_one_draft_change(pg_ai):
    case = pg_ai
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    proposal = propose(case, receipt["draft_id"])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: case.intelligence.apply(case.actors["accountant"], proposal["id"], 1), range(3)))
    assert all(result["version"] == 2 and result["status"] == "draft" for result in results)
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_proposal_applications WHERE run_id=%s", (proposal["id"],))[0][0] == 1
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.audit_events WHERE object_id=%s AND action='intelligence.proposal.apply'", (receipt["draft_id"],))[0][0] == 1


def test_pg_ai_network_call_releases_write_transaction_and_duplicate_claim_does_not_recall_model(pg_ai):
    case = pg_ai
    event = create_event(case)
    entered, release = Event(), Event()

    def hold_response():
        entered.set()
        assert release.wait(20), "测试请求未及时释放"

    case.provider.extract_hook = hold_response
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(extract, case, event)
        assert entered.wait(10)
        try:
            # If the initial network call held the write lock, this second
            # request would block instead of independently rejecting the claim.
            with pytest.raises(AccountingError, match="仍在运行"):
                extract(case, event)
        finally:
            release.set()
        assert pending.result()["status"] == "succeeded"
    assert case.provider.extract_calls == 1


def test_pg_ai_foreign_requester_cannot_expire_another_persons_running_claim(pg_ai, monkeypatch):
    case = pg_ai
    event = create_event(case)
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    settings = case.provider.settings
    attachment = event["attachments"][0]
    digest = fingerprint({"attachment_id": attachment["id"], "sha256": attachment["sha256"], "provider": settings.base_url,
                          "format": settings.response_format, "model": settings.vision_model, "prompt": EXTRACTION_PROMPT_VERSION})
    with monkeypatch.context() as patched:
        patched.setattr("app.intelligence.service.now", lambda: old)
        with case.db.transaction(write=True, actor=case.actors["employee"]) as conn:
            current = case.business._event(conn, case.actors["employee"], event["id"])
            run, _ = case.intelligence._claim(conn, case.actors["employee"], current, "ocr", "old-running-claim", digest, settings.vision_model, EXTRACTION_PROMPT_VERSION, attachment_id=attachment["id"])
    with pytest.raises(AccountingError, match="其他人员"):
        extract(case, event, actor="finance", key="old-running-claim")
    assert admin_rows(case, "SELECT status FROM accounting.ai_runs WHERE id=%s", (run["id"],)) == [("running",)]
    expired = extract(case, event, key="old-running-claim")
    assert expired["status"] == "failed" and expired["error_code"] == "expired" and case.provider.extract_calls == 0


def test_pg_ai_paid_bank_link_roundtrip_hides_ledger_from_staff_and_audits_reason(pg_ai):
    case = pg_ai
    event = create_event(case, template="procurement_expense", payment_status="paid")
    extract(case, event)
    receipt = handoff(case, event)
    bank = import_bank(case)
    linked = case.intelligence.link_bank(case.actors["accountant"], event["id"], BankLinkRequest(bank_source_ids=[bank["id"]], reason="已核对本公司银行回单"))
    assert linked["bank_links"][0]["bank_source_id"] == bank["id"]
    assert case.intelligence.link_bank(case.actors["accountant"], event["id"], BankLinkRequest(bank_source_ids=[bank["id"]], reason="网络重试再次确认"))["bank_links"][0]["bank_source_id"] == bank["id"]
    for name in ("employee", "approver"):
        actor = case.actors[name]
        assert case.intelligence.detail(actor, event["id"])["bank_links"] == []
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_bank_links WHERE event_id=?", (event["id"],)).fetchone()[0] == 0
    bank_draft = next(row for row in case.ledger.get_book(case.actors["accountant"], case.book["id"])["drafts"] if row["source_id"] == bank["id"])
    with pytest.raises(AccountingError, match="重复"):
        case.ledger.approve(case.actors["accountant"], case.book["id"], bank_draft["id"], ApprovalRequest(expected_version=bank_draft["version"]))
    unlinked = case.intelligence.unlink_bank(case.actors["accountant"], event["id"], bank["id"], "回单需重新核实，保留原始流水")
    assert not unlinked["bank_links"]
    rows = admin_rows(case, "SELECT details FROM accounting.audit_events WHERE object_id=%s AND action='intelligence.bank.unlink'", (event["id"],))
    assert "回单需重新核实" in rows[0][0]
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.sources WHERE id=%s", (bank["id"],))[0][0] == 1


def test_pg_ai_applied_warning_cannot_disappear_by_editing_same_draft_after_new_ocr(pg_ai):
    case = pg_ai
    event = create_event(case)
    extract(case, event)
    receipt = handoff(case, event)
    case.provider.proposal_mutator = lambda output: output.update(warnings=["请人工确认费用用途"])
    proposal = propose(case, receipt["draft_id"])
    for issue in case.intelligence.detail(case.actors["accountant"], event["id"])["findings"]:
        if issue["severity"] == "blocking" and not issue["resolved"]:
            case.intelligence.resolve(case.actors["accountant"], issue["id"], "会计已核对费用用途与现有原件")
    applied = case.intelligence.apply(case.actors["accountant"], proposal["id"], 1)
    assert not applied["issues"]
    current_event = case.business.get_event(case.actors["employee"], event["id"])
    extract(case, current_event, key="pg-new-ocr-facts")
    assert any("费用用途" in issue for issue in draft(case, receipt["draft_id"])["issues"])
    edited = case.ledger.update_draft(case.actors["accountant"], case.book["id"], applied["id"], DraftUpdate(expected_version=2, description=applied["description"], lines=applied["lines"]))
    assert edited["version"] == 3 and any("费用用途" in issue for issue in edited["issues"])
    with pytest.raises(AccountingError, match="费用用途"):
        case.ledger.approve(case.actors["accountant"], case.book["id"], edited["id"], ApprovalRequest(expected_version=3))


def test_pg_ai_foreign_company_cannot_read_event_runs_or_findings_even_with_raw_sql(pg_ai):
    case = pg_ai
    event = create_event(case)
    case.provider.document = invoice_document(invoice_number="PG-" + uuid4().hex, buyer_tax_id=case.provider.document["buyer_tax_id"], net_amount=None, tax_amount=None)
    extract(case, event)
    issue = next(row for row in case.intelligence.detail(case.actors["finance"], event["id"])["findings"] if row["severity"] == "blocking")
    case.intelligence.resolve(case.actors["finance"], issue["id"], "已核验完整发票原件并保留人工处理依据")
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_findings WHERE event_id=%s", (event["id"],))[0][0] > 0
    assert admin_rows(case, "SELECT COUNT(*) FROM accounting.ai_finding_reviews WHERE event_id=%s", (event["id"],))[0][0] > 0
    for table in ("business_events", "business_attachments", "ai_runs", "ai_findings", "ai_finding_reviews"):
        with case.db.transaction(actor=case.actors["outsider"]) as conn:
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {'id' if table == 'business_events' else 'event_id'}=?", (event["id"],)).fetchone()[0] == 0
    with pytest.raises(AccountingError):
        extract(case, event, actor="outsider")
    assert case.provider.extract_calls == 1
