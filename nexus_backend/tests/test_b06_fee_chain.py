"""B8：规费链路只读统一视图。

断言三件事：链路按真实外键串起来（批次 → 应收 → 核销 → 流水行 → 凭证 → 报表）；
缺一段就写清缺在哪，不编造；跨账套访问一律 404。
"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.posting.models import OpeningBind, PostDraft
from app.posting.service import PostingService
from app.tax.chain import FeeChainView
from app.tax.fees import TaxFeeService
from test_business import business  # noqa: F401
from test_tax06_fee_batch_posting import _frozen_opening
from test_tax06_fees import POLICY, _bank, _masters, _source


def _reviewed_batch(case):
    _driver, _vehicle, driver2, vehicle2 = _masters(case)
    from app.ledgers.models import TransportAssignmentCreate
    from app.ledgers.service import LedgersService

    LedgersService(case.db).create_transport_assignment(
        case.manager,
        case.book["id"],
        TransportAssignmentCreate(
            vehicle_id=vehicle2["id"],
            driver_id=driver2["id"],
            shift="full",
            contract_external_id="C1",
            valid_from="2026-10-05",
            valid_to="2026-10-05",
        ),
    )
    source = _source(case)
    service = TaxFeeService(case.db)
    batch = service.generate(
        case.manager,
        case.book["id"],
        period="2026-10",
        source_file_ids=[source["id"]],
        policy=POLICY,
    )
    service.review(
        case.actors["accountant"],
        case.book["id"],
        batch["id"],
        expected_version=batch["version"],
        reason="独立复核规费批次",
    )
    return service, batch, service.list_receivables(case.actors["accountant"], case.book["id"])


def _statement_line(case, amount_cents):
    identifier = uuid4().hex
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO bank_statement_lines(id,book_id,period,line_date,description,amount_cents,"
            "status,created_by,created_at) VALUES(?,?,'2026-10','2026-10-10','张师傅核算主体 S-1',?,"
            "'unmatched',?,'2026-10-10T00:00:00+00:00')",
            (identifier, case.book["id"], amount_cents, case.manager.user_id),
        )
    return identifier


def test_chain_links_every_segment_or_names_the_gap(business):
    case = business
    service, batch, receivables = _reviewed_batch(case)
    item = receivables["items"][0]
    bank_id = _bank(case, "income", int(item["amount_cents"]), "B-CHAIN-1")
    line_id = _statement_line(case, int(item["amount_cents"]))
    service.allocate_receivable(
        case.actors["accountant"],
        case.book["id"],
        item["receivable_id"],
        bank_source_id=bank_id,
        amount_cents=int(item["amount_cents"]),
        idempotency_key="chain-alloc-1",
        bank_statement_line_id=line_id,
    )
    chain = FeeChainView(case.db).chain(case.manager, case.book["id"], batch["id"])
    segments = {segment["key"]: segment for segment in chain["segments"]}
    assert chain["readonly"] is True
    assert [segment["key"] for segment in chain["segments"]] == [
        "batch",
        "receivables",
        "allocations",
        "statement_lines",
        "vouchers",
        "report",
    ]
    assert segments["batch"]["state"] == "present"
    assert segments["batch"]["items"][0]["status"] == "reviewed"
    assert segments["receivables"]["count"] == len(receivables["items"])
    assert segments["allocations"]["items"][0]["bank_statement_line_id"] == line_id
    assert segments["statement_lines"]["items"][0]["id"] == line_id
    # 银行来源草稿还没记账：凭证段如实缺失，并给出可执行的下一步。
    assert segments["vouchers"]["state"] == "missing"
    assert "尚未记账" in segments["vouchers"]["reason"]
    assert chain["pending_drafts"], "未记账的银行草稿要被点名，方便会计去补"
    assert segments["report"]["state"] == "missing"
    assert len(chain["gaps"]) == 2


def test_chain_shows_the_formal_voucher_once_the_bank_draft_is_posted(business):
    case = business
    _frozen_opening(case)
    service, batch, receivables = _reviewed_batch(case)
    item = receivables["items"][0]
    bank_id = _bank(case, "income", int(item["amount_cents"]), "B-CHAIN-2")
    service.allocate_receivable(
        case.actors["accountant"],
        case.book["id"],
        item["receivable_id"],
        bank_source_id=bank_id,
        amount_cents=int(item["amount_cents"]),
        idempotency_key="chain-alloc-2",
    )
    with case.db.transaction(actor=case.manager) as conn:
        draft = conn.execute(
            "SELECT id,version FROM drafts WHERE source_id=?", (bank_id,)
        ).fetchone()
    # 银行草稿要先由另一名会计复核，才允许正式记账。
    case.ledger.approve(
        case.actors["accountant"],
        case.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=int(draft["version"])),
    )
    with case.db.transaction(actor=case.manager) as conn:
        draft = conn.execute("SELECT id,version FROM drafts WHERE id=?", (draft["id"],)).fetchone()
    posted = PostingService(case.db).post_draft(
        case.manager,
        case.book["id"],
        draft["id"],
        PostDraft(
            command_key="chain-bank-draft-post",
            expected_version=int(draft["version"]),
            voucher_date="2026-10-31",
        ),
    )
    chain = FeeChainView(case.db).chain(case.manager, case.book["id"], batch["id"])
    vouchers = {segment["key"]: segment for segment in chain["segments"]}["vouchers"]
    assert vouchers["state"] == "present"
    assert vouchers["items"][0]["voucher_id"] == posted["voucher"]["id"]
    assert chain["pending_drafts"] == []


def test_chain_shows_the_formal_voucher_once_the_fee_batch_draft_is_posted(business):
    case = business
    _frozen_opening(case)
    _service, batch, _receivables = _reviewed_batch(case)
    with case.db.transaction(actor=case.manager) as conn:
        draft = conn.execute(
            "SELECT id,version FROM drafts WHERE id=?", (batch["draft_id"],)
        ).fetchone()
    posted = PostingService(case.db).post_draft(
        case.manager,
        case.book["id"],
        draft["id"],
        PostDraft(
            command_key="chain-fee-batch-draft-post",
            expected_version=int(draft["version"]),
            voucher_date="2026-10-31",
        ),
    )
    chain = FeeChainView(case.db).chain(case.manager, case.book["id"], batch["id"])
    vouchers = {segment["key"]: segment for segment in chain["segments"]}["vouchers"]
    assert vouchers["state"] == "present"
    assert vouchers["items"][0]["voucher_id"] == posted["voucher"]["id"]
    assert vouchers["items"][0]["source_role"] == "fee_batch"


def test_chain_refuses_a_batch_from_another_book(business):
    case = business
    service, batch, _ = _reviewed_batch(case)
    with pytest.raises(AccountingError) as refused:
        FeeChainView(case.db).chain(case.manager, case.other_book["id"], batch["id"])
    assert refused.value.status in (403, 404)
