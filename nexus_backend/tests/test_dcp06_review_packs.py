"""DCP06-A：批量准备复核资料只汇总对象与阻断，不批准、不截断、不越权。"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import BookCreate
from app.accounting.review import ReviewService
from app.review_packs.service import ReviewPackService
from test_business import business  # noqa: F401


@pytest.fixture
def packs(business):
    business.packs = ReviewPackService(business.db, ReviewService(business.db))
    return business


def draft(case, book, amount=10_000, day="20"):
    source_id, draft_id = uuid4().hex, uuid4().hex
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
            "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,'bank')",
            (
                source_id,
                book,
                uuid4().hex,
                "银行流水",
                f"2026-{day[:2]}-{day[2:]}",
                "收款",
                "示例客户",
                amount,
                "income",
                "回单",
                uuid4().hex + uuid4().hex,
            ),
        )
        conn.execute(
            "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation) "
            "VALUES(?,?,?,?,'draft',1,'')",
            (draft_id, book, source_id, "收款"),
        )
    return draft_id


def test_pack_lists_ready_and_blocked_companies(packs):
    for _ in range(2):
        draft(packs, packs.book["id"])
    result = packs.packs.build(
        packs.actors["accountant"],
        [packs.company["id"], packs.other["id"], "does-not-exist"],
        "2026-10",
    )
    ready = next(item for item in result["items"] if item["company_id"] == packs.company["id"])
    assert ready["status"] == "ready"
    assert ready["draft_count"] == 2
    assert len(ready["drafts"]) == 2
    assert len(ready["manifest"]) == 64
    assert all(item["version"] == 1 for item in ready["drafts"])

    # 第二家企业有账套但当前账号没有该账套授权：不暴露对象，只报 blocked。
    empty = next(item for item in result["items"] if item["company_id"] == packs.other["id"])
    assert empty["status"] == "blocked"
    assert empty["blockers"] == ["no_access"]

    missing = next(item for item in result["items"] if item["company_id"] == "does-not-exist")
    assert missing["blockers"] == ["no_book"]
    assert result["ready"] == 1 and result["blocked"] == 2
    assert len(result["manifest"]) == 64


def test_only_the_requested_period_is_packed(packs):
    draft(packs, packs.book["id"])
    other_period = packs.ledger.create_book(
        packs.manager,
        BookCreate(name="九月账套", period="2026-09", company_id=packs.company["id"]),
    )
    draft(packs, other_period["id"])
    result = packs.packs.build(packs.actors["accountant"], [packs.company["id"]], "2026-10")
    assert result["items"][0]["draft_count"] == 1


def test_manifest_changes_when_a_draft_is_added(packs):
    draft(packs, packs.book["id"])
    first = packs.packs.build(packs.actors["accountant"], [packs.company["id"]], "2026-10")
    draft(packs, packs.book["id"])
    second = packs.packs.build(packs.actors["accountant"], [packs.company["id"]], "2026-10")
    assert first["manifest"] != second["manifest"]
    assert first["items"][0]["manifest"] != second["items"][0]["manifest"]


def test_company_finance_cannot_build_a_firm_pack(packs):
    draft(packs, packs.book["id"])
    with pytest.raises(AccountingError) as error:
        packs.packs.build(packs.actors["finance"], [packs.company["id"]], "2026-10")
    assert error.value.status == 403


def test_duplicate_company_ids_collapse_and_period_is_validated(packs):
    draft(packs, packs.book["id"])
    result = packs.packs.build(
        packs.actors["accountant"],
        [packs.company["id"], packs.company["id"]],
        "2026-10",
    )
    assert len(result["items"]) == 1
    with pytest.raises(AccountingError) as error:
        packs.packs.build(packs.actors["accountant"], [packs.company["id"]], "2026-13")
    assert error.value.status == 422
