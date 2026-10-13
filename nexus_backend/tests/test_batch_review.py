from period_fixtures import restore_period_state
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountUpdate
from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookCreate, DraftUpdate, EvidenceRequest, ExportRequest, ImportRequest, SourceRow
from app.accounting.review import ReviewService
from app.accounting.review_models import ReviewConfirmRequest, ReviewPreviewRequest
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService


@pytest.fixture
def case(tmp_path):
    accounting = AccountingService(Database(tmp_path / "review.db"))
    bootstrap(accounting, seed_examples=False)
    return accounting, ReviewService(accounting.db, accounting)


def draft(case, book=None, external_id="row-001", evidence="票据001", description="办公用品"):
    accounting, _ = case
    book = book or accounting.create_book(LOCAL_ACTOR, BookCreate(name="企业" + external_id, period="2026-10"))["id"]
    accounting.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="合成试验", rows=[SourceRow(external_id=external_id, date="2026-10-01", description=description, counterparty="测试供应商", amount="100.00", direction="expense", evidence_ref=evidence)]))
    accounting.suggestions(LOCAL_ACTOR, book)
    with accounting.db.transaction() as conn:
        row = conn.execute("SELECT d.* FROM drafts d JOIN sources s ON s.id=d.source_id WHERE d.book_id=? AND s.external_id=?", (book, external_id)).fetchone()
    return {"book_id": book, "draft_id": row["id"], "expected_version": row["version"]}


def preview(case, items, actor=LOCAL_ACTOR):
    return case[1].preview(actor, ReviewPreviewRequest(items=items))


def confirm(case, value, key="review-confirm-001", actor=LOCAL_ACTOR):
    return case[1].confirm(actor, value["id"], ReviewConfirmRequest(idempotency_key=key))


def status(case, item):
    with case[0].db.transaction() as conn:
        return dict(conn.execute("SELECT * FROM drafts WHERE id=? AND book_id=?", (item["draft_id"], item["book_id"])).fetchone())


def reviewer(case, book_ids=(), role="reviewer", user="reviewer-2"):
    accounting, _ = case
    with accounting.db.transaction(write=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES(?,?)", (user, user))
        conn.execute("INSERT INTO memberships(firm_id,user_id,role) VALUES(?,?,?)", (LOCAL_ACTOR.firm_id, user, role))
        for book_id in book_ids:
            conn.execute("INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'reviewer')", (book_id, LOCAL_ACTOR.firm_id, user))
    return Actor(user_id=user, firm_id=LOCAL_ACTOR.firm_id)


def test_cross_company_preview_checks_and_confirm_keeps_export_explicit(case):
    items = [draft(case), draft(case, external_id="row-002")]
    value = preview(case, items)
    assert value["ready_count"] == 2 and value["blocked_count"] == 0
    assert all(item["lines"] and item["evidence_ref"] and item["review_fingerprint"] for item in value["items"])
    assert all(status(case, item)["status"] == "draft" for item in items)
    result = confirm(case, value)
    assert result["status"] == "completed" and result["approved_count"] == 2
    assert all(status(case, item)["status"] == "approved" for item in items)
    with case[0].db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM learning_samples").fetchone()[0] == 2
    exported = case[0].export(LOCAL_ACTOR, items[0]["book_id"], ExportRequest(draft_ids=[items[0]["draft_id"]], idempotency_key="review-export-001"))
    assert exported["count"] == 1


def test_same_book_blocked_item_rolls_back_all_and_other_book_succeeds(case):
    first = draft(case)
    second = draft(case, book=first["book_id"], external_id="row-002", evidence="")
    third = draft(case, external_id="row-003")
    value = preview(case, [first, second, third])
    assert value["blocked_count"] == 1
    result = confirm(case, value)
    assert result["status"] == "partial" and result["approved_count"] == 1 and result["failed_book_count"] == 1
    assert status(case, first)["status"] == status(case, second)["status"] == "draft"
    assert status(case, third)["status"] == "approved"
    assert confirm(case, value) == result


def test_complete_receipt_remains_stable_after_export(case):
    item = draft(case)
    value = preview(case, [item])
    result = confirm(case, value)
    case[0].export(LOCAL_ACTOR, item["book_id"], ExportRequest(draft_ids=[item["draft_id"]], idempotency_key="batch-export-001"))
    assert confirm(case, value) == result


def test_failed_receipt_not_reexecuted_when_facts_are_fixed(case):
    item = draft(case, evidence="")
    value = preview(case, [item])
    result = confirm(case, value)
    case[0].add_evidence(LOCAL_ACTOR, item["book_id"], status(case, item)["source_id"], EvidenceRequest(evidence_ref="补充票据"))
    assert confirm(case, value) == result and status(case, item)["status"] == "draft"
    item["expected_version"] = status(case, item)["version"]
    new_preview = preview(case, [item])
    assert confirm(case, new_preview, key="review-confirm-002")["status"] == "completed"


def test_all_book_authorization_precedes_preview_and_writes(case):
    first, second = draft(case), draft(case, external_id="row-002")
    actor = reviewer(case, [first["book_id"]])
    assert case[1].list_drafts(actor)["pagination"]["total"] == 1
    with pytest.raises(AccountingError):
        preview(case, [first, second], actor)
    with case[0].db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM review_previews").fetchone()[0] == 0
    assert status(case, first)["status"] == status(case, second)["status"] == "draft"


@pytest.mark.parametrize("role", ["operator", "viewer"])
def test_nonreviewer_book_grant_does_not_open_review_page(case, role):
    item = draft(case)
    actor = reviewer(case, [item["book_id"]], role=role)
    assert case[1].list_drafts(actor)["items"] == []
    with pytest.raises(AccountingError):
        preview(case, [item], actor)


def test_preview_is_private_to_creator_and_revoked_grant_hides_it(case):
    item = draft(case)
    actor = reviewer(case, [item["book_id"]])
    value = preview(case, [item], actor)
    with pytest.raises(AccountingError, match="无权访问"):
        case[1].get_preview(LOCAL_ACTOR, value["id"])
    with case[0].db.transaction(write=True) as conn:
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?", (item["book_id"], actor.user_id))
    with pytest.raises(AccountingError):
        confirm(case, value, actor=actor)
    assert status(case, item)["status"] == "draft"


@pytest.mark.parametrize("change", ["draft", "evidence", "account", "period"])
def test_current_context_changes_invalidate_preview(case, change):
    item = draft(case)
    value = preview(case, [item])
    accounting = case[0]
    if change == "draft":
        current = accounting.get_draft(LOCAL_ACTOR, item["book_id"], item["draft_id"])
        accounting.update_draft(LOCAL_ACTOR, item["book_id"], item["draft_id"], DraftUpdate(expected_version=1, description="会计修改", lines=current["lines"]))
    elif change == "evidence":
        accounting.add_evidence(LOCAL_ACTOR, item["book_id"], status(case, item)["source_id"], EvidenceRequest(evidence_ref="新的证据"))
    elif change == "account":
        CatalogService(accounting.db).update_account(LOCAL_ACTOR, item["book_id"], "6602.02", AccountUpdate(code="6602.02", name="会计修订科目", expected_version=1))
    else:
        restore_period_state(accounting.db, item["book_id"])
    result = confirm(case, value)
    assert result["status"] == "failed" and status(case, item)["status"] == "draft"


def test_concurrent_confirm_produces_one_approval_one_sample_and_stable_receipt(case):
    item = draft(case)
    value = preview(case, [item])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: confirm(case, value), range(3)))
    assert results[0] == results[1] == results[2]
    assert status(case, item)["version"] == 2
    with case[0].db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM review_confirmations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM review_book_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='draft.approve'").fetchone()[0] == 1


def test_preview_and_receipts_are_immutable_and_forged_success_is_rejected(case):
    item = draft(case)
    value = preview(case, [item])
    command_id, _ = case[1]._command(LOCAL_ACTOR, value["id"], ReviewConfirmRequest(idempotency_key="receipt-test-001"))
    with case[0].db.transaction(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE review_previews SET snapshot_json='[]' WHERE id=?", (value["id"],))
        with pytest.raises(sqlite3.IntegrityError, match="approved"):
            conn.execute("INSERT INTO review_book_receipts VALUES(?,?,?,'now')", (command_id, item["book_id"], json.dumps({"book_id": item["book_id"], "status": "succeeded", "approved_ids": [item["draft_id"]]})))
    result = confirm(case, value, key="receipt-test-001")
    with case[0].db.transaction(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM review_book_receipts WHERE confirmation_id=?", (result["id"],))


def test_parent_command_can_resume_unfinished_books_after_process_restart(case):
    item = draft(case)
    value = preview(case, [item])
    case[1]._command(LOCAL_ACTOR, value["id"], ReviewConfirmRequest(idempotency_key="restart-test-001"))
    accounting = AccountingService(case[0].db)
    restarted = (accounting, ReviewService(accounting.db, accounting))
    assert confirm(restarted, value, key="restart-test-001")["approved_count"] == 1


def test_idempotency_key_cannot_switch_preview_and_preview_cannot_switch_key(case):
    item = draft(case)
    first, second = preview(case, [item]), preview(case, [item])
    confirm(case, first)
    with pytest.raises(AccountingError, match="幂等键"):
        confirm(case, second)
    with pytest.raises(AccountingError, match="原幂等键"):
        confirm(case, first, key="new-command-001")


def test_sql_pagination_and_authorized_filters(case):
    first, second = draft(case), draft(case, external_id="row-002", evidence="")
    page = case[1].list_drafts(LOCAL_ACTOR, limit=1)
    assert page["pagination"] == {"limit": 1, "offset": 0, "total": 2, "has_more": True}
    assert len(page["items"]) == 1 and len(page["companies"]) == 2
    assert case[1].list_drafts(LOCAL_ACTOR, risk="basic_exception")["items"][0]["draft_id"] == second["draft_id"]
    assert case[1].list_drafts(LOCAL_ACTOR, template="bank", risk="basic_clear")["items"][0]["draft_id"] == first["draft_id"]
    assert case[1].list_drafts(LOCAL_ACTOR, company_id="invisible")["items"] == []


def test_expired_preview_is_not_a_durable_approval_authorization(case, monkeypatch):
    item = draft(case)
    value = preview(case, [item])
    import app.accounting.review as review_module
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=31)
    monkeypatch.setattr(review_module, "datetime", Later)
    with pytest.raises(AccountingError, match="过期"):
        confirm(case, value)
    assert status(case, item)["status"] == "draft"


def test_unfinished_command_expires_and_records_failure_before_resume(case, monkeypatch):
    item = draft(case)
    value = preview(case, [item])
    case[1]._command(LOCAL_ACTOR, value["id"], ReviewConfirmRequest(idempotency_key="expired-command-001"))
    import app.accounting.review as review_module
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=31)
    monkeypatch.setattr(review_module, "datetime", Later)
    result = confirm(case, value, key="expired-command-001")
    assert result["status"] == "failed" and "过期" in result["results"][0]["error"]
    assert status(case, item)["status"] == "draft"


def test_later_learning_hook_failure_rolls_back_earlier_approval_and_sample(case, monkeypatch):
    first = draft(case)
    second = draft(case, book=first["book_id"], external_id="row-002")
    value = preview(case, [first, second])
    learning = case[0]._learning_guard()
    original = learning.capture_in_transaction
    seen = []
    def fail_second(*args):
        result = original(*args)
        seen.append(result)
        if len(seen) == 2:
            raise AccountingError("测试学习记录写入失败", 409)
        return result
    monkeypatch.setattr(learning, "capture_in_transaction", fail_second)
    assert confirm(case, value)["status"] == "failed"
    assert status(case, first)["status"] == status(case, second)["status"] == "draft"
    with case[0].db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM learning_samples").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='draft.approve'").fetchone()[0] == 0


def test_open_case_changes_full_preview_guard_and_cannot_be_batch_overridden(case):
    from app.cases.models import CaseCreate
    item = draft(case)
    value = preview(case, [item])
    case[0]._case_guard().create(LOCAL_ACTOR, CaseCreate(book_id=item["book_id"], draft_id=item["draft_id"], title="核对供应商", reason="交易实质待会计判断", idempotency_key="open-case-001"))
    assert confirm(case, value)["status"] == "failed"
    value = preview(case, [item])
    assert value["blocked_count"] == 1
    assert any("异常待办" in issue for issue in value["items"][0]["issues"])


def test_mapped_business_applicant_cannot_review_own_voucher_in_batch(case):
    item = draft(case)
    accounting = case[0]
    with accounting.db.transaction(write=True) as conn:
        source = conn.execute("SELECT s.*,b.company_id FROM sources s JOIN books b ON b.id=s.book_id WHERE s.id=?", (status(case, item)["source_id"],)).fetchone()
        payload = json.dumps({"occurred_on": "2026-10-01", "description": "办公用品", "counterparty": "供应商", "amount": "100.00", "direction": "expense", "payment_status": "unpaid", "category": "office"})
        conn.execute("INSERT INTO business_events(id,firm_id,company_id,applicant_id,template_id,status,payload,submitted_version,created_at,updated_at) VALUES('own-event',?,?,?,'procurement_expense','handed_off',?,1,'now','now')", (LOCAL_ACTOR.firm_id, source["company_id"], LOCAL_ACTOR.user_id, payload))
        conn.execute("INSERT INTO business_versions VALUES('own-event',?,?,1,?,'{}','[]','now',?)", (LOCAL_ACTOR.firm_id, source["company_id"], payload, LOCAL_ACTOR.user_id))
        conn.execute("INSERT INTO business_mappings VALUES('own-event',?,?,?,?,?,1,'own-event-handoff','fp',?,'now')", (LOCAL_ACTOR.firm_id, source["company_id"], item["book_id"], source["id"], item["draft_id"], LOCAL_ACTOR.user_id))
        conn.execute("UPDATE sources SET source_kind='business' WHERE id=?", (source["id"],))
    value = preview(case, [item])
    assert value["blocked_count"] == 1 and "申请人不能审核本人" in " ".join(value["items"][0]["issues"])
    assert confirm(case, value)["status"] == "failed"


@pytest.mark.parametrize("items", [[], [{"book_id": "b", "draft_id": "d", "expected_version": True}], [{"book_id": "b", "draft_id": "d", "expected_version": 1}] * 2])
def test_ambiguous_selection_is_rejected(items):
    with pytest.raises(ValidationError):
        ReviewPreviewRequest(items=items)
