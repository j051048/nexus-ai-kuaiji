"""第二批企业写锁：凭证复核/导出、批量复核、科目模板、银行导入、重复隔离与关联回执。"""

from uuid import uuid4

from app.accounting.models import ApprovalRequest, ExportRequest
from app.accounting.review_models import ReviewConfirmRequest
from app.accounting.seed import LOCAL_ACTOR
from app.business.storage import AttachmentStore
from app.dispositions.service import SourceDispositionService
from app.import_wizard.service import ImportWizardService
from test_accounting import service, book, add_actor  # noqa: F401
from test_batch_review import (  # noqa: F401
    case as review_case,
    draft as review_draft,
    preview as review_preview,
)
from test_business import business  # noqa: F401
from test_catalog import case as catalog_case, template as catalog_template  # noqa: F401
from test_company_write_scope_wave1 import assert_scoped, tap, writes
from test_stage9_reconciliation import command, selection, setup as reconciliation_setup
from test_stage10_dispositions import duplicates
from test_stage12_import_wizard import preview as wizard_preview
from test_stage12_workspace import bank


def company_of(db, book_id):
    with db.transaction() as conn:
        return conn.execute("SELECT company_id FROM books WHERE id=?", (book_id,)).fetchone()[0]


def test_journal_import_review_export_are_company_scoped(business):
    c = business
    actor, book_id = c.actors["accountant"], c.book["id"]
    calls = tap(c.db)
    draft = bank(c)
    c.ledger.approve(
        actor, book_id, draft["id"], ApprovalRequest(expected_version=draft["version"])
    )
    c.ledger.export(
        actor, book_id, ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex)
    )
    assert_scoped(calls, c.company["id"])


def test_batch_review_receipt_is_company_scoped(review_case):
    accounting, review = review_case
    item = review_draft(review_case)
    value = review_preview(review_case, [item])
    calls = tap(accounting.db)
    review.confirm(
        LOCAL_ACTOR, value["id"], ReviewConfirmRequest(idempotency_key="wave2-review-scope")
    )
    scoped = [call for call in writes(calls) if call.get("write_scope") == "company"]
    assert len(scoped) == 1, "按账套写入复核回执的事务必须带企业范围"
    assert scoped[0]["company_id"] == company_of(accounting.db, item["book_id"])


def test_bank_import_wizard_is_company_scoped(service, book, tmp_path):
    wizard = ImportWizardService(service.db, AttachmentStore(tmp_path / "wizard"), service)
    calls = tap(service.db)
    result, _item = wizard_preview(wizard, LOCAL_ACTOR, book)
    wizard.confirm(LOCAL_ACTOR, book, result["id"])
    assert_scoped(calls, company_of(service.db, book))


def test_duplicate_isolation_is_company_scoped(service, book):
    draft, candidate, request = duplicates(service, book)
    guard = SourceDispositionService(service.db)
    calls = tap(service.db)
    receipt = guard.confirm(LOCAL_ACTOR, book, draft["source_id"], candidate["id"], request)
    assert receipt["inactive"]
    assert_scoped(calls, company_of(service.db, book))


def test_catalog_template_is_company_scoped(catalog_case):
    accounting, catalog, book = catalog_case
    calls = tap(accounting.db)
    saved = catalog.save_template(LOCAL_ACTOR, book, catalog_template())
    assert saved["id"]
    assert_scoped(calls, company_of(accounting.db, book))


def test_reconciliation_confirm_is_company_scoped(business):
    c = business
    service, _match, result = reconciliation_setup(c)
    calls = tap(c.db)
    service.confirm(c.actors["accountant"], command(selection(result)))
    assert_scoped(calls, c.company["id"])
