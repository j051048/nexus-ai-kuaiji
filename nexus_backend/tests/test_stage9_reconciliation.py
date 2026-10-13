from period_fixtures import restore_period_state
"""确定性匹配的事务测试，模型关闭且从未调用供应商。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest
from app.reconciliation.models import ConfirmRequest, MatchItem, BankSelection
from app.reconciliation.service import ReconciliationService
from test_business import business, approved, handoff


def paid(c, amount="0.30", **changes):
    return handoff(c, approved(c, template="procurement_expense", payment_status="paid", amount=amount, **changes))


def banks(c, amounts=("0.30",), **changes):
    c.ledger.import_sources(c.manager, c.book["id"], ImportRequest(source_name="第九阶段虚构银行流水", rows=[{
        "external_id": "recon-bank-" + uuid4().hex, "date": "2026-10-03", "description": "办公用品采购付款", "counterparty": "耗材商店", "amount": amount,
        "direction": "expense", "evidence_ref": "虚构付款回单", **changes,
    } for amount in amounts]))
    c.ledger.suggestions(c.manager, c.book["id"])


def selection(result, picked=None):
    event = result["event"]
    rows = picked if picked is not None else result["candidates"]
    return MatchItem(**{key: event[key] for key in ("book_id", "event_id", "event_version", "fact_version", "business_draft_version", "business_source_fingerprint")},
        banks=[BankSelection(**{key: row[key] for key in ("source_id", "source_fingerprint", "draft_version")}) for row in rows])


def command(item, key=None):
    return ConfirmRequest(items=[item], idempotency_key=key or uuid4().hex, reason="已核对付款回单、合同用途及收付金额")


def setup(c, amounts=("0.30",)):
    match = paid(c)
    banks(c, amounts)
    service = ReconciliationService(c.db)
    result = service.candidates(c.actors["accountant"], c.book["id"], match["event"]["id"])
    return service, match, result


def test_model_disabled_single_match_keeps_ledger_review_and_prevents_bank_double_booking(business):
    c = business
    service, match, result = setup(c)
    assert result["candidates"][0]["selectable"] and result["exact_amount_count"] == 1
    receipt = service.confirm(c.actors["accountant"], command(selection(result)))
    assert receipt["review_required"] and receipt["items"][0]["links_current"]
    assert service.events(c.actors["accountant"], c.book["id"])["total"] == 0
    assert service.events(c.actors["accountant"], c.book["id"], status="matched")["total"] == 1
    draft = c.ledger.get_draft(c.actors["accountant"], c.book["id"], match["draft_id"])
    assert draft["status"] == "draft"
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        bank_draft = conn.execute("SELECT id,version FROM drafts WHERE source_id=?", (result["candidates"][0]["source_id"],)).fetchone()
    with pytest.raises(AccountingError):
        c.ledger.approve(c.actors["accountant"], c.book["id"], bank_draft["id"], ApprovalRequest(expected_version=bank_draft["version"]))


def test_split_banks_match_only_exact_sum_and_retries_are_single_receipt(business):
    c = business
    service, _, result = setup(c, ("0.10", "0.20"))
    with pytest.raises(AccountingError, match="合计"):
        service.confirm(c.actors["accountant"], command(selection(result, result["candidates"][:1])))
    request = command(selection(result))
    first, second = [service.confirm(c.actors["accountant"], request) for _ in range(2)]
    assert first["id"] == second["id"]
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_receipts").fetchone()[0] == 1


def test_ambiguous_equal_amounts_are_explicit_and_never_automatically_linked(business):
    c = business
    service, _, result = setup(c, ("0.30", "0.30"))
    assert result["ambiguous"] and result["exact_amount_count"] == 2
    assert all(any("人工辨别" in risk for risk in item["risks"]) for item in result["candidates"])
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links").fetchone()[0] == 0
    receipt = service.confirm(c.actors["accountant"], command(selection(result, result["candidates"][:1])))
    assert len(receipt["items"][0]["bank_source_ids"]) == 1


@pytest.mark.parametrize("field,value", [("event_version", 999), ("fact_version", 999), ("business_draft_version", 999), ("business_source_fingerprint", "forged-source")])
def test_changed_business_versions_or_fingerprint_reject_without_mutation(business, field, value):
    c = business
    service, _, result = setup(c)
    item = selection(result).model_copy(update={field: value})
    with pytest.raises(AccountingError):
        service.confirm(c.actors["accountant"], command(item))
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links").fetchone()[0] == 0


def test_bank_versions_direction_counterparty_date_and_period_remain_current(business):
    c = business
    service, _, result = setup(c)
    item = selection(result)
    bad = item.model_copy(update={"banks": [item.banks[0].model_copy(update={"draft_version": 999})]})
    with pytest.raises(AccountingError, match="版本"):
        service.confirm(c.actors["accountant"], command(bad))
    with c.db.transaction(write=True, actor=c.manager) as conn:
        restore_period_state(c.ledger.db, c.book["id"], conn=conn)
    with pytest.raises(AccountingError):
        service.confirm(c.actors["accountant"], command(item))


def test_candidate_sql_filters_other_party_direction_and_outside_seven_days(business):
    c = business
    match = paid(c)
    banks(c, counterparty="其他供应商")
    banks(c, direction="income")
    banks(c, date="2026-10-20")
    service = ReconciliationService(c.db)
    assert service.candidates(c.actors["accountant"], c.book["id"], match["event"]["id"])["total"] == 0


def test_unpaid_or_cross_company_sources_never_produce_candidates(business):
    c = business
    match = handoff(c, approved(c))
    service = ReconciliationService(c.db)
    with pytest.raises(AccountingError, match="已收付"):
        service.candidates(c.actors["accountant"], c.book["id"], match["event"]["id"])
    for actor in (c.actors["employee"], c.actors["peer"]):
        with pytest.raises(AccountingError):
            service.events(actor, c.book["id"])
    assert service.events(c.manager, c.other_book["id"])["total"] == 0


def test_all_books_authorized_before_atomic_confirmation_and_replay_rechecks_grants(business):
    c = business
    service, _, result = setup(c)
    item = selection(result)
    other_item = item.model_copy(update={"book_id": c.other_book["id"], "event_id": "different-event", "banks": [item.banks[0].model_copy(update={"source_id": "different-bank"})]})
    request = ConfirmRequest(items=[item, other_item], idempotency_key=uuid4().hex, reason="核对两家企业")
    with pytest.raises(AccountingError):
        service.confirm(c.actors["accountant"], request)
    with c.db.transaction(actor=c.actors["accountant"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links").fetchone()[0] == 0
    request = command(item)
    service.confirm(c.actors["accountant"], request)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id='accountant'", (c.book["id"],))
    with pytest.raises(AccountingError):
        service.confirm(c.actors["accountant"], request)


def test_candidate_paging_uses_bounded_sql_and_concurrent_confirm_returns_one_receipt(business):
    c = business
    service, _, result = setup(c, ("0.10", "0.20"))
    first = service.candidates(c.actors["accountant"], c.book["id"], result["event"]["event_id"], limit=1)
    second = service.candidates(c.actors["accountant"], c.book["id"], result["event"]["event_id"], limit=1, offset=1)
    assert first["total"] == 2 and first["has_more"] and not second["has_more"]
    assert first["candidates"][0]["source_id"] != second["candidates"][0]["source_id"]
    request = command(selection(result))
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: service.confirm(c.actors["accountant"], request), range(2)))
    assert receipts[0]["id"] == receipts[1]["id"]


def test_bank_pending_case_blocks_confirmation_and_only_generic_risk_is_visible(business):
    from app.cases.models import CaseCreate
    from app.cases.service import CaseService
    c = business
    service, _, result = setup(c)
    reviewer = c.actors["accountant"]
    with c.db.transaction(actor=reviewer) as conn:
        bank_draft_id = conn.execute("SELECT id FROM drafts WHERE source_id=? AND book_id=?", (result["candidates"][0]["source_id"], c.book["id"])).fetchone()[0]
    CaseService(c.db, c.service).create(reviewer, CaseCreate(book_id=c.book["id"], draft_id=bank_draft_id, title="内部调查不能泄露", reason="会计专属内部原因", idempotency_key=uuid4().hex))
    current = service.candidates(c.actors["finance"], c.book["id"], result["event"]["event_id"])
    assert not current["candidates"][0]["selectable"]
    assert any("异常待办未关闭" in risk for risk in current["candidates"][0]["risks"])
    assert "会计专属内部原因" not in str(current) and "内部调查不能泄露" not in str(current)
    with pytest.raises(AccountingError, match="未关闭异常"):
        service.confirm(c.actors["finance"], command(selection(result)))
    with c.db.transaction(actor=reviewer) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_bank_links").fetchone()[0] == 0


def test_bank_case_opened_after_link_blocks_business_review_and_export(business):
    from app.cases.models import CaseCreate
    from app.cases.service import CaseService
    c = business
    service, match, result = setup(c)
    reviewer = c.actors["accountant"]
    service.confirm(reviewer, command(selection(result)))
    with c.db.transaction(actor=reviewer) as conn:
        bank_draft_id = conn.execute("SELECT id FROM drafts WHERE source_id=? AND book_id=?", (result["candidates"][0]["source_id"], c.book["id"])).fetchone()[0]
    CaseService(c.db, c.service).create(reviewer, CaseCreate(book_id=c.book["id"], draft_id=bank_draft_id, title="付款回单待核实", reason="关联后收到新的付款异常信息", idempotency_key=uuid4().hex))
    business_draft = c.ledger.get_draft(reviewer, c.book["id"], match["draft_id"])
    assert any("异常" in issue for issue in business_draft["issues"])
    with pytest.raises(AccountingError):
        c.ledger.approve(reviewer, c.book["id"], match["draft_id"], ApprovalRequest(expected_version=business_draft["version"]))
    with pytest.raises(AccountingError):
        c.ledger.export(reviewer, c.book["id"], ExportRequest(draft_ids=[match["draft_id"]], idempotency_key=uuid4().hex))
