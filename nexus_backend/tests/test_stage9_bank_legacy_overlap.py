"""历史银行来源不能通过改为命名空间导入绕过重复风险。"""
import sqlite3
from uuid import uuid4
import pytest
from app.accounting.bank_imports import BankImportService, DuplicateDistinction
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest
from app.accounting.domain import AccountingError
from app.accounting.seed import LOCAL_ACTOR
from app.reconciliation.service import ReconciliationService
from test_accounting import service, book, row
from test_business import business, approved, handoff
from test_stage9_bank_imports import statement
from test_stage9_reconciliation import selection, command


def by_external(service, actor, book_id, external):
    return next(source for source in service.get_book(actor, book_id)["sources"] if source["external_id"] == external)


def distinct(checks, actor, book_id, source_id, version):
    view = checks.overview(actor, book_id, source_id)
    other = view["candidates"][0]
    return checks.distinguish(actor, book_id, source_id, other["id"], DuplicateDistinction(source_fingerprint=view["fingerprint"],
        other_fingerprint=other["fingerprint"], expected_version=version, reason="已核对银行原件与两个独立交易编号，确认不同交易"))


def test_approved_legacy_then_identified_import_blocks_new_and_preserves_originals(service, book):
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="旧银行导入", rows=[row(external_id="legacy-approved")]))
    service.suggestions(LOCAL_ACTOR, book)
    old_draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    service.approve(LOCAL_ACTOR, book, old_draft["id"], ApprovalRequest(expected_version=1))
    service.import_sources(LOCAL_ACTOR, book, statement())
    service.suggestions(LOCAL_ACTOR, book)
    new_source = by_external(service, LOCAL_ACTOR, book, "display-1")
    new_draft = next(draft for draft in service.get_book(LOCAL_ACTOR, book)["drafts"] if draft["source_id"] == new_source["id"])
    assert any("独立交易" in issue for issue in new_draft["issues"])
    with pytest.raises(AccountingError):
        service.approve(LOCAL_ACTOR, book, new_draft["id"], ApprovalRequest(expected_version=1))
    checks = BankImportService(service.db)
    from_new = checks.overview(LOCAL_ACTOR, book, new_source["id"])
    assert from_new["candidates"][0]["external_id"] == "legacy-approved"
    assert from_new["candidates"][0]["stable_transaction_id"] == "legacy-approved"
    from_old = checks.overview(LOCAL_ACTOR, book, old_draft["source_id"])
    assert from_old["identity"] is None and from_old["total"] == 1
    assert distinct(checks, LOCAL_ACTOR, book, new_source["id"], 1)["distinct"]
    assert not service.get_draft(LOCAL_ACTOR, book, new_draft["id"])["issues"]
    assert service.get_draft(LOCAL_ACTOR, book, old_draft["id"])["status"] == "approved"
    assert len(service.get_book(LOCAL_ACTOR, book)["sources"]) == 2


def test_identified_then_legacy_import_is_symmetrically_blocked(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="旧格式文件", rows=[row(external_id="later-legacy")]))
    service.suggestions(LOCAL_ACTOR, book)
    detail = service.get_book(LOCAL_ACTOR, book)
    assert all(any("独立交易" in issue for issue in draft["issues"]) for draft in detail["drafts"])
    legacy = by_external(service, LOCAL_ACTOR, book, "later-legacy")
    assert BankImportService(service.db).overview(LOCAL_ACTOR, book, legacy["id"])["total"] == 1


def test_two_legacy_sources_keep_existing_behavior_and_known_accounts_do_not_conflict(service, book):
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="旧格式", rows=[row(external_id="legacy-1"), row(external_id="legacy-2")]))
    service.suggestions(LOCAL_ACTOR, book)
    checks = BankImportService(service.db)
    for source in service.get_book(LOCAL_ACTOR, book)["sources"]:
        assert checks.overview(LOCAL_ACTOR, book, source["id"])["total"] == 0


def test_new_known_different_accounts_are_not_suspected_duplicates(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement(account="account-a"))
    service.import_sources(LOCAL_ACTOR, book, statement(account="account-b"))
    service.suggestions(LOCAL_ACTOR, book)
    for source in service.get_book(LOCAL_ACTOR, book)["sources"]:
        assert BankImportService(service.db).overview(LOCAL_ACTOR, book, source["id"])["total"] == 0


def test_linked_legacy_then_new_identity_blocks_business_until_distinguished(business):
    c = business
    event = handoff(c, approved(c, template="procurement_expense", payment_status="paid"))
    facts = {"date": "2026-10-03", "counterparty": "耗材商店", "description": "办公用品采购", "amount": "0.30"}
    c.ledger.import_sources(c.manager, c.book["id"], ImportRequest(source_name="旧导入", rows=[row(external_id="legacy-linked", **facts)]))
    c.ledger.suggestions(c.manager, c.book["id"])
    reconciliation = ReconciliationService(c.db)
    match = reconciliation.candidates(c.actors["accountant"], c.book["id"], event["event"]["id"])
    reconciliation.confirm(c.actors["accountant"], command(selection(match)))
    c.ledger.import_sources(c.manager, c.book["id"], statement(external="new-identified", stable="identified-transaction", **facts))
    c.ledger.suggestions(c.manager, c.book["id"])
    draft = c.ledger.get_draft(c.actors["accountant"], c.book["id"], event["draft_id"])
    assert any("独立交易" in issue for issue in draft["issues"])
    with pytest.raises(AccountingError):
        c.ledger.approve(c.actors["accountant"], c.book["id"], draft["id"], ApprovalRequest(expected_version=draft["version"]))
    with pytest.raises(AccountingError):
        c.ledger.export(c.actors["accountant"], c.book["id"], ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex))
    new_source = by_external(c.ledger, c.manager, c.book["id"], "new-identified")
    new_draft = next(item for item in c.ledger.get_book(c.manager, c.book["id"])["drafts"] if item["source_id"] == new_source["id"])
    distinct(BankImportService(c.db), c.actors["accountant"], c.book["id"], new_source["id"], new_draft["version"])
    assert not c.ledger.get_draft(c.actors["accountant"], c.book["id"], event["draft_id"])["issues"]
    assert len(c.ledger.get_book(c.manager, c.book["id"])["sources"]) == 3


def test_sqlite_native_decision_cannot_clear_nonmatching_or_two_legacy_facts(service, book):
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="旧格式", rows=[row(external_id="legacy-1"), row(external_id="legacy-2")]))
    service.suggestions(LOCAL_ACTOR, book)
    sources = service.get_book(LOCAL_ACTOR, book)["sources"]
    low, high = sorted(source["id"] for source in sources)
    with service.db.transaction() as conn:
        prints = {source["id"]: conn.execute("SELECT fingerprint FROM sources WHERE id=?", (source["id"],)).fetchone()[0] for source in sources}
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.transaction(write=True) as conn:
            conn.execute("INSERT INTO bank_duplicate_decisions VALUES(?,?,?,?,?,?,?,?,?,?)", (low, high, book, LOCAL_ACTOR.firm_id, prints[low], prints[high], 1, "不能给不满足候选条件的来源制造核验", LOCAL_ACTOR.user_id, "2026-10-12"))
