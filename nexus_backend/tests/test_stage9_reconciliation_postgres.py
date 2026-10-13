"""真实受限 PostgreSQL 原子关联及禁止伪造回执验证。"""
import json
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ImportRequest
from app.accounting.service import fingerprint
from app.reconciliation.service import ReconciliationService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event, handoff, import_bank
from test_stage9_reconciliation import command, selection

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员与受限运行连接")


def prepare(c, amounts=("0.30",)):
    c.intelligence.company_settings(c.manager, c.company["id"], False)
    event = create_event(c, template="procurement_expense", payment_status="paid")
    match = handoff(c, event)
    for amount in amounts:
        import_bank(c, external_id="recon-pg-" + uuid4().hex, amount=amount)
    service = ReconciliationService(c.db)
    result = service.candidates(c.actors["accountant"], c.book["id"], event["id"])
    return service, match, result


def test_pg_disabled_model_split_matching_creates_one_scoped_immutable_receipt(pg_ai):
    c = pg_ai
    service, match, result = prepare(c, ("0.10", "0.20"))
    request = command(selection(result))
    receipt = service.confirm(c.actors["accountant"], request)
    assert service.confirm(c.actors["accountant"], request)["id"] == receipt["id"]
    assert receipt["review_required"] and receipt["items"][0]["links_current"]
    assert c.provider.extract_calls == c.provider.proposal_calls == 0
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links WHERE event_id=?", (match["event"]["id"],)).fetchone()[0] == 2
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert not conn.execute("SELECT * FROM reconciliation_receipts WHERE id=?", (receipt["id"],)).fetchall()


def test_pg_native_confirmation_rejects_missing_versions_or_fingerprints(pg_ai):
    c = pg_ai
    service, _, result = prepare(c)
    request = command(selection(result))
    payload = request.model_dump()
    payload["items"][0]["banks"][0].pop("source_fingerprint")
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute("SELECT accounting.confirm_reconciliation(?,?,?)", (request.idempotency_key, fingerprint(payload), json.dumps(payload)))
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links WHERE book_id=?", (c.book["id"],)).fetchone()[0] == 0


def test_pg_raw_receipt_insert_cannot_claim_work_without_canonical_links(pg_ai):
    c = pg_ai
    _, _, result = prepare(c)
    request = command(selection(result))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute("INSERT INTO reconciliation_receipts(id,firm_id,created_by,idempotency_key,request_fingerprint,request_json,reason,created_at) VALUES(?,?,?,?,?,?,?,'2026-10-12')", (uuid4().hex, c.manager.firm_id, c.actors["accountant"].user_id, request.idempotency_key, fingerprint(request.model_dump()), json.dumps(request.model_dump()), request.reason))
    with c.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='accounting.reconciliation_receipts'::regclass").fetchone() == (True, True)
        assert not conn.execute("SELECT has_table_privilege('accounting_worker','accounting.reconciliation_receipts','SELECT')").fetchone()[0]


def test_pg_native_confirmation_rejects_other_company_and_scope_revocation(pg_ai):
    c = pg_ai
    service, _, result = prepare(c)
    request = command(selection(result))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.actors["outsider"]) as conn:
            conn.execute("SELECT accounting.confirm_reconciliation(?,?,?)", (request.idempotency_key, fingerprint(request.model_dump()), json.dumps(request.model_dump())))
    receipt = service.confirm(c.actors["accountant"], request)
    c.directory.revoke_book_member(c.manager, c.book["id"], c.actors["accountant"].user_id)
    with pytest.raises(AccountingError):
        service.confirm(c.actors["accountant"], request)


def test_pg_candidate_sql_pagination_and_native_amount_guard(pg_ai):
    c = pg_ai
    service, _, result = prepare(c, ("0.10", "0.20"))
    page = service.candidates(c.actors["accountant"], c.book["id"], result["event"]["event_id"], limit=1)
    assert len(page["candidates"]) == 1 and page["total"] == 2 and page["has_more"]
    request = command(selection(result, result["candidates"][:1]))
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute("SELECT accounting.confirm_reconciliation(?,?,?)", (request.idempotency_key, fingerprint(request.model_dump()), json.dumps(request.model_dump())))


def test_pg_bank_pending_case_blocks_python_and_native_confirmation(pg_ai):
    from app.cases.models import CaseCreate
    from app.cases.service import CaseService
    from app.business.service import BusinessService
    c = pg_ai
    service, _, result = prepare(c)
    reviewer = c.actors["accountant"]
    request = command(selection(result))
    with c.db.transaction(actor=reviewer) as conn:
        bank_draft = conn.execute("SELECT id FROM drafts WHERE source_id=? AND book_id=?", (result["candidates"][0]["source_id"], c.book["id"])).fetchone()[0]
    CaseService(c.db, BusinessService(c.db)).create(reviewer, CaseCreate(book_id=c.book["id"], draft_id=bank_draft,
        title="内部付款调查", reason="仅会计可见的核验依据", idempotency_key=uuid4().hex))
    current = service.candidates(c.actors["finance"], c.book["id"], result["event"]["event_id"])
    assert not current["candidates"][0]["selectable"] and "仅会计可见的核验依据" not in str(current)
    with pytest.raises(AccountingError):
        service.confirm(c.actors["finance"], request)
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=reviewer) as conn:
            conn.execute("SELECT accounting.confirm_reconciliation(?,?,?)", (request.idempotency_key, fingerprint(request.model_dump()), json.dumps(request.model_dump())))
