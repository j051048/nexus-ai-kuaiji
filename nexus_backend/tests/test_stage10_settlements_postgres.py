from uuid import uuid4
import json
import pytest
from app.accounting.models import ApprovalRequest, DraftUpdate, ExportRequest
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from app.settlements.models import OpenItemCreate, AllocationPreview, AllocationConfirm, AllocationReverse
from app.settlements.service import SettlementService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_intelligence_workflow_postgres import pg_ai
from test_intelligence_workflow import create_event, handoff, import_bank

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason="需要专用 PG 管理员及受限 API 连接")


def prepare(c, bank_amount="0.30"):
    c.intelligence.company_settings(c.manager,c.company["id"],False)
    original=handoff(c,create_event(c,template="procurement_expense",payment_status="unpaid"))
    party=LedgersService(c.db).create_entity(c.manager,c.book["id"],EntityCreate(kind="counterparty",code="SUP-PG",name="耗材商店"))
    service=SettlementService(c.db)
    draft=c.ledger.get_draft(c.actors["accountant"],c.book["id"],original["draft_id"])
    item=service.open_item(c.actors["accountant"],c.book["id"],OpenItemCreate(source_id=original["source_id"],counterparty_id=party["id"],expected_draft_version=draft["version"],idempotency_key=uuid4().hex,reason="核验未付款采购业务"))
    import_bank(c,external_id="settlement-pg-"+uuid4().hex,amount=bank_amount)
    bank=service.overview(c.actors["accountant"],c.book["id"])["banks"][0]
    return service,original,item,bank


def preview(c,service,item,bank,amount="0.30"):
    return service.preview(c.actors["accountant"],c.book["id"],AllocationPreview(bank_source_id=bank["id"],parts=[{"item_id":item["id"],"amount":amount}],reason="核验回单和应付事项"))


def confirm(c,service,plan):
    return service.confirm(c.actors["accountant"],c.book["id"],plan["id"],AllocationConfirm(idempotency_key=uuid4().hex))


def test_pg_native_review_clearance_and_frozen_reversal(pg_ai):
    c=pg_ai
    service,original,item,bank=prepare(c)
    confirm(c,service,preview(c,service,item,bank))
    draft=c.ledger.get_draft(c.actors["accountant"],c.book["id"],original["draft_id"])
    c.ledger.approve(c.actors["accountant"],c.book["id"],draft["id"],ApprovalRequest(expected_version=draft["version"]))
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert not conn.execute("SELECT accounting.settlement_bank_clearance_valid(?,?)",(c.book["id"],bank["id"])).fetchone()[0]
    clearance=service.clearance(c.actors["accountant"],c.book["id"],bank["id"])
    changed=c.ledger.update_draft(c.actors["accountant"],c.book["id"],bank["draft_id"],DraftUpdate(expected_version=clearance["draft_version"],description=clearance["description"],lines=clearance["lines"],correction_reason="核销应付，不再重复费用"))
    c.ledger.approve(c.actors["accountant"],c.book["id"],bank["draft_id"],ApprovalRequest(expected_version=changed["version"]))
    export=c.ledger.export(c.actors["accountant"],c.book["id"],ExportRequest(draft_ids=[original["draft_id"],bank["draft_id"]],idempotency_key=uuid4().hex))
    assert export["count"]==2
    row=service.overview(c.manager,c.book["id"])["allocations"][0]
    with pytest.raises(Exception):
        service.reversal(c.manager,c.book["id"],row["id"],AllocationReverse(expected_fingerprint=row["fingerprint"],idempotency_key=uuid4().hex,reason="已导出的核销不能撤回"))


def test_pg_previews_replay_and_remaining_cannot_overallocate(pg_ai):
    c=pg_ai
    service,original,item,bank=prepare(c)
    first=preview(c,service,item,bank,"0.20")
    second=preview(c,service,item,bank,"0.20")
    request=AllocationConfirm(idempotency_key=uuid4().hex)
    result=service.confirm(c.actors["accountant"],c.book["id"],first["id"],request)
    assert service.confirm(c.actors["accountant"],c.book["id"],first["id"],request)==result
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.actors["accountant"]) as conn:
            conn.execute("SELECT accounting.settlement_confirm(?,?,?)",(c.book["id"],second["id"],uuid4().hex))
    assert service.overview(c.manager,c.book["id"])["items"][0]["remaining"]=="0.10"


def test_pg_force_rls_no_raw_insert_or_unknown_party_plan(pg_ai):
    c=pg_ai
    service,original,item,bank=prepare(c)
    with c.db.transaction(actor=c.actors["employee"]) as conn:
        assert not conn.execute("SELECT * FROM settlement_items WHERE id=?",(item["id"],)).fetchall()
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True,actor=c.actors["accountant"]) as conn:
            conn.execute("INSERT INTO settlement_previews(id,book_id,firm_id,company_id,created_by,request_json,plan_json,created_at,expires_at) VALUES(?,?,?,?,?,'{}','{}',clock_timestamp()::text,clock_timestamp()::text)",(uuid4().hex,c.book["id"],c.manager.firm_id,c.company["id"],c.actors["accountant"].user_id))
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(write=True,actor=c.actors["accountant"]) as conn:
            conn.execute("SELECT accounting.settlement_make_preview(?,?)",(c.book["id"],json.dumps({"bank_source_id":bank["id"],"parts":[{"item_id":item["id"],"amount":"999.00"}],"reason":"伪造超过原件金额分配"})))
    with c.psycopg.connect(ADMIN_URL) as conn:
        for name in ("settlement_items","settlement_previews","settlement_commands","settlement_allocations","settlement_reversals"):
            assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",("accounting."+name,)).fetchone()==(True,True)
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')",("accounting."+name,)).fetchone()[0]


def test_pg_independent_reversal_restores_balance_and_keeps_allocation(pg_ai):
    c=pg_ai
    service,original,item,bank=prepare(c)
    confirm(c,service,preview(c,service,item,bank))
    row=service.overview(c.manager,c.book["id"])["allocations"][0]
    request=AllocationReverse(expected_fingerprint=row["fingerprint"],idempotency_key=uuid4().hex,reason="独立会计核实选错回单")
    first=service.reversal(c.manager,c.book["id"],row["id"],request)
    assert service.reversal(c.manager,c.book["id"],row["id"],request)==first
    value=service.overview(c.manager,c.book["id"])
    assert value["items"][0]["remaining"]=="0.30" and value["allocations"][0]["reversed"]
