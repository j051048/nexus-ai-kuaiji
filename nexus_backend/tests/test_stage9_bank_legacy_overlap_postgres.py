"""受限 PostgreSQL 混合历史来源与稳定身份的原生阻断边界。"""
from uuid import uuid4
import pytest
from app.accounting.bank_imports import BankImportService, DuplicateDistinction
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest, BookCreate
from app.accounting.domain import AccountingError
from app.reconciliation.service import ReconciliationService
from test_accounting import row
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event, handoff
from test_stage9_bank_imports import statement
from test_stage9_reconciliation import selection, command

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行连接")


def source_by_external(service, actor, book_id, external):
    return next(source for source in service.get_book(actor, book_id)["sources"] if source["external_id"] == external)


def test_pg_approved_legacy_overlap_blocks_new_identity_and_native_distinction_preserves_old(business_case):
    c = business_case
    c.service.import_sources(c.owner, c.book, ImportRequest(source_name="旧流水", rows=[row(external_id="legacy-approved")]))
    c.service.suggestions(c.owner, c.book)
    old = c.service.get_book(c.reviewer, c.book)["drafts"][0]
    c.service.approve(c.reviewer, c.book, old["id"], ApprovalRequest(expected_version=1))
    c.service.import_sources(c.owner, c.book, statement())
    c.service.suggestions(c.owner, c.book)
    new = source_by_external(c.service, c.reviewer, c.book, "display-1")
    checks = BankImportService(c.db)
    view = checks.overview(c.reviewer, c.book, new["id"])
    assert view["total"] == 1 and view["candidates"][0]["external_id"] == "legacy-approved"
    old_view = checks.overview(c.reviewer, c.book, old["source_id"])
    assert old_view["identity"] is None and old_view["total"] == 1
    with c.db.transaction(actor=c.reviewer) as conn:
        assert conn.execute("SELECT accounting.bank_source_import_blocked(?,?)", (c.book, new["id"])).fetchone()[0]
        assert conn.execute("SELECT accounting.bank_source_import_blocked(?,?)", (c.book, old["source_id"])).fetchone()[0]
    other = view["candidates"][0]
    checks.distinguish(c.reviewer, c.book, new["id"], other["id"], DuplicateDistinction(source_fingerprint=view["fingerprint"], other_fingerprint=other["fingerprint"], expected_version=1, reason="已比对两份银行原件确认是独立交易"))
    with c.db.transaction(actor=c.reviewer) as conn:
        assert not conn.execute("SELECT accounting.bank_source_import_blocked(?,?)", (c.book, new["id"])).fetchone()[0]
    assert c.service.get_draft(c.reviewer, c.book, old["id"])["status"] == "approved"
    assert len(c.service.get_book(c.reviewer, c.book)["sources"]) == 2


def test_pg_linked_legacy_then_namespaced_duplicate_blocks_business_approval_until_review(pg_ai):
    c = pg_ai
    c.intelligence.company_settings(c.manager, c.company["id"], False)
    event = create_event(c, template="procurement_expense", payment_status="paid")
    mapped = handoff(c, event)
    facts = {"date": "2026-10-03", "counterparty": "耗材商店", "description": "办公用品采购", "amount": "0.30"}
    c.ledger.import_sources(c.manager, c.book["id"], ImportRequest(source_name="旧流水", rows=[row(external_id="legacy-linked", **facts)]))
    c.ledger.suggestions(c.manager, c.book["id"])
    reconciliation = ReconciliationService(c.db)
    candidates = reconciliation.candidates(c.actors["accountant"], c.book["id"], event["id"])
    reconciliation.confirm(c.actors["accountant"], command(selection(candidates)))
    c.ledger.import_sources(c.manager, c.book["id"], statement(external="identified-again", stable="new-identification", **facts))
    c.ledger.suggestions(c.manager, c.book["id"])
    draft = c.ledger.get_draft(c.actors["accountant"], c.book["id"], mapped["draft_id"])
    assert any("独立交易" in issue for issue in draft["issues"])
    with pytest.raises(AccountingError):
        c.ledger.approve(c.actors["accountant"], c.book["id"], draft["id"], ApprovalRequest(expected_version=draft["version"]))
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors["accountant"], c.book["id"], ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    new = source_by_external(c.ledger, c.actors["accountant"], c.book["id"], "identified-again")
    checks = BankImportService(c.db)
    view = checks.overview(c.actors["accountant"], c.book["id"], new["id"])
    other = view["candidates"][0]
    checks.distinguish(c.actors["accountant"], c.book["id"], new["id"], other["id"], DuplicateDistinction(source_fingerprint=view["fingerprint"], other_fingerprint=other["fingerprint"], expected_version=1, reason="已核对两个独立银行交易编号及原始回单"))
    assert not c.ledger.get_draft(c.actors["accountant"], c.book["id"], mapped["draft_id"])["issues"]
    assert len(c.ledger.get_book(c.manager, c.book["id"])["sources"]) == 3


def test_pg_two_legacy_or_known_different_accounts_keep_original_nonoverlap_behavior(business_case):
    c = business_case
    c.service.import_sources(c.owner, c.book, ImportRequest(source_name="旧格式", rows=[row(external_id="legacy-one"), row(external_id="legacy-two")]))
    c.service.suggestions(c.owner, c.book)
    with c.db.transaction(actor=c.reviewer) as conn:
        for source in conn.execute("SELECT id FROM sources WHERE book_id=?", (c.book,)):
            assert not conn.execute("SELECT accounting.bank_source_import_blocked(?,?)", (c.book, source["id"])).fetchone()[0]
    with c.psycopg.connect(ADMIN_URL) as conn:
        sources = conn.execute("SELECT id,fingerprint FROM accounting.sources WHERE book_id=%s ORDER BY id", (c.book,)).fetchall()
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.reviewer) as conn:
            conn.execute("INSERT INTO bank_duplicate_decisions VALUES(?,?,?,?,?,?,?,?,?,?)", (sources[0][0], sources[1][0], c.book, c.owner.firm_id, sources[0][1], sources[1][1], 1, "两条旧来源不满足本次候选规则", c.reviewer.user_id, "2026-10-12"))
    known_book = c.service.create_book(c.owner, BookCreate(name="只含明确账号的测试账套", company_id=c.company, period="2026-10"))["id"]
    c.service.import_sources(c.owner, known_book, statement(account="known-a"))
    c.service.import_sources(c.owner, known_book, statement(account="known-b"))
    with c.db.transaction(actor=c.owner) as conn:
        for source in conn.execute("SELECT id FROM sources WHERE book_id=?", (known_book,)):
            assert not conn.execute("SELECT accounting.bank_source_import_blocked(?,?)", (known_book, source["id"])).fetchone()[0]
