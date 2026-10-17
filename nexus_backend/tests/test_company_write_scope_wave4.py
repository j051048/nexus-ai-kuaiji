"""第四批企业写锁：相似流水核验与历史未结清偿的单企业写路径。"""

from uuid import uuid4

from app.accounting.bank_imports import BankImportService, DuplicateDistinction
from app.accounting.seed import LOCAL_ACTOR
from app.legacy_migration.settlements import LegacyAllocationReverse
from test_accounting import service, book  # noqa: F401
from test_business import business  # noqa: F401
from test_company_write_scope_wave1 import assert_scoped, tap
from test_stage9_bank_imports import statement
from test_stage13_legacy_settlements import bank, confirm, legacy_case, preview  # noqa: F401


def test_bank_duplicate_distinction_is_company_scoped(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    service.import_sources(
        LOCAL_ACTOR,
        book,
        statement(external="display-2", stable="transaction-2", namespace="aggregator"),
    )
    service.suggestions(LOCAL_ACTOR, book)
    draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    checks = BankImportService(service.db)
    view = checks.overview(LOCAL_ACTOR, book, draft["source_id"])
    other = view["candidates"][0]
    request = DuplicateDistinction(
        source_fingerprint=view["fingerprint"],
        other_fingerprint=other["fingerprint"],
        expected_version=1,
        reason="已核对两份独立银行回单",
    )
    calls = tap(service.db)
    assert checks.distinguish(LOCAL_ACTOR, book, view["source_id"], other["id"], request)[
        "distinct"
    ]
    company_id = service.get_book(LOCAL_ACTOR, book)["book"]["company_id"]
    assert_scoped(calls, company_id)


def test_legacy_clearance_confirm_and_reverse_are_company_scoped(legacy_case):
    c = legacy_case
    prepared = preview(c, bank(c))
    calls = tap(c.db)
    receipt = confirm(c, prepared)
    assert_scoped(calls, c.company["id"])

    calls = tap(c.db)
    c.settlements.reverse(
        c.manager,
        c.book["id"],
        c.project,
        receipt["id"],
        LegacyAllocationReverse(
            expected_fingerprint=receipt["fingerprint"],
            idempotency_key=uuid4().hex,
            reason="独立复核确认所选历史欠款错误",
        ),
    )
    assert_scoped(calls, c.company["id"])
