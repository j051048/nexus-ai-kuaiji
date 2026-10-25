"""Bank coverage uses immutable import originals and independent review."""

from types import SimpleNamespace

import pytest

from app.accounting.domain import AccountingError
from app.business.storage import AttachmentStore
from app.import_wizard.models import MappingSettings, PreviewRequest
from app.import_wizard.service import ImportWizardService
from app.subledger.models import BankAccountCreate, BankCoverageCreate
from app.subledger.service import SubledgerService
from test_b04_pilot import setup_b03
from test_business import business


@pytest.fixture
def coverage_case(business, tmp_path):
    c = business
    opening, _ = setup_b03(c)
    storage = AttachmentStore(tmp_path / "bank-originals")
    sub = SubledgerService(c.db)
    sub.storage = storage
    account = sub.create_bank_account(
        c.manager,
        BankAccountCreate(
            book_id=c.book["id"],
            name="证据基本户",
            ledger_account_id=opening["bank"]["id"],
            ledger_account_version=1,
            reason="银行覆盖证据测试",
        ),
    )
    wizard = ImportWizardService(c.db, storage)
    original = wizard.upload(
        c.manager,
        c.book["id"],
        "流水.csv",
        "编号,日期,摘要,对方,金额,方向,凭据\nE001,2026-10-01,办公用品,虚构商店,0.30,支出,回单001\n".encode(),
    )
    preview = wizard.preview(
        c.manager,
        c.book["id"],
        PreviewRequest(
            file_id=original["file_id"],
            namespace="coverage",
            bank_account=account["id"],
            mapping=MappingSettings(
                columns={
                    "external_id": 0,
                    "date": 1,
                    "description": 2,
                    "counterparty": 3,
                    "amount": 4,
                    "direction": 5,
                    "evidence_ref": 6,
                }
            ),
        ),
    )
    wizard.confirm(c.manager, c.book["id"], preview["id"])
    return SimpleNamespace(
        c=c, sub=sub, storage=storage, original=original, preview=preview, account=account
    )


def declaration(c, **changes):
    return BankCoverageCreate(
        **{
            "book_id": c.c.book["id"],
            "account_id": c.account["id"],
            "period": "2026-10",
            "opening": "10000.00",
            "closing": "9999.70",
            "reason": "核对本月银行流水",
            "source_file_id": c.original["file_id"],
            "source_sha256": c.original["sha256"],
            "source_preview_id": c.preview["id"],
            **changes,
        }
    )


def review(c, actor, coverage, **changes):
    from app.subledger.models import BankCoverageReview

    return c.sub.review_bank_coverage(
        actor,
        coverage["id"],
        BankCoverageReview(
            **{
                "expected_version": coverage["version"],
                "decision": "verified",
                "reason": "已核对原件及银行账户",
                **changes,
            }
        ),
    )


def test_free_text_cannot_become_verified_coverage(coverage_case):
    c = coverage_case
    request = BankCoverageCreate(
        book_id=c.c.book["id"],
        account_id=c.account["id"],
        period="2026-10",
        opening="0.00",
        closing="0.00",
        source_ref="我说这是银行证明",
        reason="伪造文本不能作为证据",
    )
    with pytest.raises(AccountingError):
        c.sub.declare_bank_coverage(c.c.manager, request)


def test_pending_independent_review_idempotency_and_terminal_immutability(coverage_case):
    c = coverage_case
    row = c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    assert row["state"] == "pending_review" and row["verified_by"] is None
    assert c.sub.declare_bank_coverage(c.c.manager, declaration(c))["id"] == row["id"]
    assert not c.sub.bank_reconciliation(c.c.manager, c.c.book["id"])["coverage_ok"]
    with pytest.raises(AccountingError) as denied:
        review(c, c.c.manager, row)
    assert denied.value.status == 403
    done = review(c, c.c.actors["accountant"], row)
    assert done["state"] == "verified" and done["verified_by"] == c.c.actors["accountant"].user_id
    assert c.sub.bank_reconciliation(c.c.manager, c.c.book["id"])["coverage_ok"]
    assert review(c, c.c.actors["accountant"], row)["id"] == done["id"]
    with pytest.raises(AccountingError):
        review(c, c.c.actors["accountant"], done, decision="rejected")


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "f" * 64},
        {"source_file_id": "missing"},
        {"source_preview_id": "missing"},
        {"source_ref": "arbitrary"},
        {"period": "2026-09"},
        {"closing": "9999.69"},
    ],
)
def test_invalid_source_or_amount_is_rejected(coverage_case, change):
    c = coverage_case
    with pytest.raises(AccountingError):
        c.sub.declare_bank_coverage(c.c.manager, declaration(c, **change))


def test_other_company_original_and_wrong_bank_batch_are_rejected(coverage_case):
    c = coverage_case
    with c.c.db.transaction(actor=c.c.manager) as conn:
        other_book = c.c.other_book["id"]
    with pytest.raises(AccountingError):
        c.sub.declare_bank_coverage(c.c.manager, declaration(c, book_id=other_book))
    from app.ledgers.models import EntityCreate
    from app.ledgers.service import LedgersService

    project = LedgersService(c.c.db).create_entity(
        c.c.manager,
        c.c.book["id"],
        EntityCreate(kind="project", code="SECOND-BANK", name="另一银行独立辅助桶"),
    )
    second = c.sub.create_bank_account(
        c.c.manager,
        BankAccountCreate(
            book_id=c.c.book["id"],
            name="另一银行户",
            ledger_account_id=c.sub.list_bank_accounts(c.c.manager, c.c.book["id"])[0][
                "ledger_account_id"
            ],
            ledger_account_version=1,
            reason="跨银行证据负向",
            aux={"project": project["id"]},
        ),
    )
    with pytest.raises(AccountingError):
        c.sub.declare_bank_coverage(c.c.manager, declaration(c, account_id=second["id"]))


@pytest.mark.parametrize("missing", [False, True])
def test_corrupt_or_missing_original_rechecked_at_review(coverage_case, missing):
    c = coverage_case
    row = c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    with c.c.db.transaction(actor=c.c.manager) as conn:
        key = conn.execute(
            "SELECT storage_key FROM bank_import_files WHERE id=?", (c.original["file_id"],)
        ).fetchone()[0]
    path = c.storage._path(key)
    if missing:
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises(AccountingError):
        review(c, c.c.actors["accountant"], row)
    assert c.sub.list_bank_coverages(c.c.manager, c.c.book["id"])[0]["state"] == "pending_review"


def test_rejected_and_unauthorized_review_never_passes_coverage(coverage_case):
    c = coverage_case
    row = c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    with pytest.raises(AccountingError):
        review(c, c.c.actors["employee"], row)
    rejected = review(c, c.c.actors["accountant"], row, decision="rejected")
    assert rejected["state"] == "rejected"
    assert not c.sub.bank_reconciliation(c.c.manager, c.c.book["id"])["coverage_ok"]


def test_rejected_history_is_preserved_when_new_declaration_is_reviewed(coverage_case):
    c = coverage_case
    old = c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    review(c, c.c.actors["accountant"], old, decision="rejected")
    newer = c.sub.declare_bank_coverage(
        c.c.manager, declaration(c, reason="补充原件核对说明后重提")
    )
    assert newer["id"] != old["id"]
    review(c, c.c.actors["accountant"], newer)
    rows = c.sub.list_bank_coverages(c.c.manager, c.c.book["id"])
    assert len(rows) == 2 and {row["state"] for row in rows} == {"rejected", "verified"}


def test_missing_storage_never_creates_an_unverifiable_declaration(coverage_case):
    c = coverage_case
    c.sub.storage = None
    with pytest.raises(AccountingError) as unavailable:
        c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    assert unavailable.value.status == 503
    assert c.sub.list_bank_coverages(c.c.manager, c.c.book["id"]) == []


def test_inactive_account_cannot_hide_pending_coverage(coverage_case):
    c = coverage_case
    c.sub.declare_bank_coverage(c.c.manager, declaration(c))
    with c.c.db.transaction(write=True, actor=c.c.manager) as conn:
        conn.execute("UPDATE bank_accounts SET active=FALSE WHERE id=?", (c.account["id"],))
    with c.c.db.transaction(actor=c.c.manager) as conn:
        result = c.sub.bank_reconciliation_in_transaction(
            conn,
            c.c.manager,
            c.c.book["id"],
            trial={
                "rows": [
                    {
                        "account_id": "synthetic-zero",
                        "code": "1002",
                        "is_leaf": True,
                        "closing_cents": 0,
                        "opening_cents": 0,
                    }
                ]
            },
        )
    assert (
        result.get("multi_account") is True and not result["coverage_ok"] and not result["passed"]
    )


def test_signed_bank_balances_have_the_same_api_and_sql_contract(coverage_case):
    c = coverage_case
    row = c.sub.declare_bank_coverage(
        c.c.manager, declaration(c, opening="-10.00", closing="-10.30")
    )
    done = review(c, c.c.actors["accountant"], row)
    assert done["state"] == "verified" and done["closing_cents"] == -1030
