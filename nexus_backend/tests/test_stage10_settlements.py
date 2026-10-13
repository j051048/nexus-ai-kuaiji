from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.settlements.models import OpenItemCreate, AllocationPreview, AllocationConfirm, AllocationReverse
from app.settlements.service import SettlementService, split_cents
from test_business import business, approved, handoff
from test_stage9_reconciliation import banks


def setup(c, amount="0.30", bank_amounts=("0.30",)):
    original = handoff(c, approved(c, template="procurement_expense", amount=amount))
    master = LedgersService(c.db)
    party = master.create_entity(c.manager,c.book["id"],EntityCreate(kind="counterparty",code="SUPPLIER-001",name="耗材商店"))
    service = SettlementService(c.db)
    item = service.open_item(c.actors["accountant"],c.book["id"],OpenItemCreate(source_id=original["source_id"],counterparty_id=party["id"],expected_draft_version=1,idempotency_key=uuid4().hex,reason="核对未付款业务及应付科目"))
    banks(c,bank_amounts)
    bank = service.overview(c.actors["accountant"],c.book["id"])["banks"][0]
    return service, original, item, bank


def preview(c,service,item,bank,amount="0.30"):
    return service.preview(c.actors["accountant"],c.book["id"],AllocationPreview(bank_source_id=bank["id"],parts=[{"item_id":item["id"],"amount":amount}],reason="核对银行回单及未结业务"))


def allocate(c,service,item,bank,amount="0.30"):
    p=preview(c,service,item,bank,amount)
    return service.confirm(c.actors["accountant"],c.book["id"],p["id"],AllocationConfirm(idempotency_key=uuid4().hex))


def test_partial_payment_recomputed_remaining_and_concurrent_previews_cannot_overfill(business):
    c=business
    service, original, item, bank=setup(c,bank_amounts=("0.10","0.20"))
    bank=next(row for row in service.overview(c.manager,c.book["id"])["banks"] if row["amount"]=="0.10")
    p=preview(c,service,item,bank,"0.10")
    request=AllocationConfirm(idempotency_key=uuid4().hex)
    first=service.confirm(c.actors["accountant"],c.book["id"],p["id"],request)
    assert service.confirm(c.actors["accountant"],c.book["id"],p["id"],request)==first
    value=service.overview(c.manager,c.book["id"])
    assert value["items"][0]["remaining"]=="0.20"
    other=next(row for row in value["banks"] if row["amount"]=="0.20")
    allocate(c,service,item,other,"0.20")
    assert service.overview(c.manager,c.book["id"])["items"][0]["remaining"]=="0.00"
    with pytest.raises(AccountingError):
        preview(c,service,item,bank,"0.01")


def test_full_allocated_bank_clears_reviewed_payable_then_exports_without_repeating_expense(business):
    c=business
    service,original,item,bank=setup(c)
    allocate(c,service,item,bank)
    clearance=service.clearance(c.actors["accountant"],c.book["id"],bank["id"])
    assert "先复核" in "".join(clearance["issues"])
    original_draft=c.ledger.get_draft(c.actors["accountant"],c.book["id"],original["draft_id"])
    c.ledger.approve(c.actors["accountant"],c.book["id"],original["draft_id"],ApprovalRequest(expected_version=original_draft["version"]))
    clearance=service.clearance(c.actors["accountant"],c.book["id"],bank["id"])
    assert not clearance["issues"] and {row["account_code"] for row in clearance["lines"]}=={item["clearing_account"],"1002"}
    bank_draft=c.ledger.get_draft(c.actors["accountant"],c.book["id"],bank["draft_id"])
    with pytest.raises(AccountingError):
        c.ledger.approve(c.actors["accountant"],c.book["id"],bank["draft_id"],ApprovalRequest(expected_version=bank_draft["version"]))
    changed=c.ledger.update_draft(c.actors["accountant"],c.book["id"],bank["draft_id"],DraftUpdate(expected_version=bank_draft["version"],description=clearance["description"],lines=clearance["lines"],correction_reason="按核销结果清偿应付款"))
    assert not changed["issues"]
    c.ledger.approve(c.actors["accountant"],c.book["id"],bank["draft_id"],ApprovalRequest(expected_version=changed["version"]))
    result=c.ledger.export(c.actors["accountant"],c.book["id"],ExportRequest(draft_ids=[original["draft_id"],bank["draft_id"]],idempotency_key=uuid4().hex))
    assert result["count"]==2


def test_item_can_receive_more_payments_after_original_business_review(business):
    c=business
    service, original, item, bank=setup(c,bank_amounts=("0.10","0.20"))
    draft=c.ledger.get_draft(c.actors["accountant"],c.book["id"],original["draft_id"])
    c.ledger.approve(c.actors["accountant"],c.book["id"],draft["id"],ApprovalRequest(expected_version=draft["version"]))
    for row in service.overview(c.manager,c.book["id"])["banks"]:
        allocate(c,service,item,row,row["amount"])
    assert service.overview(c.manager,c.book["id"])["items"][0]["remaining"]=="0.00"


def test_merged_payment_allocates_one_bank_to_multiple_originals(business):
    c=business
    service,original,item,bank=setup(c,amount="0.10",bank_amounts=("0.30",))
    second=handoff(c,approved(c,template="procurement_expense",amount="0.20"))
    other=service.open_item(c.actors["accountant"],c.book["id"],OpenItemCreate(source_id=second["source_id"],counterparty_id=item["counterparty_id"],expected_draft_version=1,idempotency_key=uuid4().hex,reason="核验第二笔未结费用"))
    p=service.preview(c.actors["accountant"],c.book["id"],AllocationPreview(bank_source_id=bank["id"],parts=[{"item_id":item["id"],"amount":"0.10"},{"item_id":other["id"],"amount":"0.20"}],reason="同供应商合并付款核对"))
    receipt=service.confirm(c.actors["accountant"],c.book["id"],p["id"],AllocationConfirm(idempotency_key=uuid4().hex))
    assert len(receipt["allocations"])==2 and sum(row["amount_cents"] for row in receipt["allocations"])==30


def test_stale_preview_and_other_company_scope_are_rejected(business):
    c=business
    service,original,item,bank=setup(c)
    p=preview(c,service,item,bank)
    d=c.ledger.get_draft(c.actors["accountant"],c.book["id"],bank["draft_id"])
    c.ledger.update_draft(c.actors["accountant"],c.book["id"],d["id"],DraftUpdate(expected_version=d["version"],description="补充银行摘要",lines=d["lines"]))
    with pytest.raises(AccountingError,match="已改变"):
        service.confirm(c.actors["accountant"],c.book["id"],p["id"],AllocationConfirm(idempotency_key=uuid4().hex))
    for actor in (c.actors["employee"],c.actors["finance"],c.actors["company_admin"]):
        with pytest.raises(AccountingError):
            service.overview(actor,c.book["id"])
    with pytest.raises(AccountingError):
        service.preview(c.actors["accountant"],c.other_book["id"],AllocationPreview(bank_source_id=bank["id"],parts=[{"item_id":item["id"],"amount":"0.30"}],reason="跨企业伪造分配"))


def test_independent_reversal_appends_history_and_restores_both_balances(business):
    c=business
    service,original,item,bank=setup(c)
    allocate(c,service,item,bank)
    record=service.overview(c.manager,c.book["id"])["allocations"][0]
    request=AllocationReverse(expected_fingerprint=record["fingerprint"],idempotency_key=uuid4().hex,reason="另一会计核实选错付款回单")
    with pytest.raises(AccountingError,match="另一名"):
        service.reversal(c.actors["accountant"],c.book["id"],record["id"],request)
    first=service.reversal(c.manager,c.book["id"],record["id"],request)
    assert service.reversal(c.manager,c.book["id"],record["id"],request)==first
    value=service.overview(c.manager,c.book["id"])
    assert value["items"][0]["remaining"]=="0.30" and value["banks"][0]["remaining"]=="0.30"
    assert len(value["allocations"])==1 and value["allocations"][0]["reversed"]


def test_largest_remainder_has_stable_ties_and_exact_total():
    assert split_cents(1,[("c",1),("b",1),("a",1)])==[{"key":"a","amount":"0.01"},{"key":"b","amount":"0.00"},{"key":"c","amount":"0.00"}]
    values=split_cents(123456789,[("a",11),("b",17),("c",19)])
    from app.accounting.models import money_to_cents
    assert sum(money_to_cents(row["amount"]) for row in values)==123456789


def test_concurrent_confirmations_preserve_bank_and_item_remaining(business):
    c=business
    service,original,item,bank=setup(c)
    plans=[preview(c,service,item,bank,"0.20") for _ in range(2)]
    def invoke(p):
        try:
            return service.confirm(c.actors["accountant"],c.book["id"],p["id"],AllocationConfirm(idempotency_key=uuid4().hex))
        except AccountingError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts=list(executor.map(invoke,plans))
    assert sum(value is not None for value in receipts)==1
    value=service.overview(c.manager,c.book["id"])
    assert value["items"][0]["remaining"]=="0.10" and value["banks"][0]["remaining"]=="0.10"
    assert len(value["allocations"])==1
