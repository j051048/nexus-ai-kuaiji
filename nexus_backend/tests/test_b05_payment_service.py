"""付款指令服务：幂等、状态机与三权分立走真实写事务。"""

import pytest

from app.accounting.domain import AccountingError
from app.treasury.payments import PaymentRuleError
from app.treasury.service import PaymentService
from review_policy_fixtures import ensure_independent_reviewer
from test_business import business  # noqa: F401


def _service(business):
    return PaymentService(business.db)


def _create(service, business, *, key="pay-1", amount=120_000):
    return service.create(
        business.actors["accountant"],
        business.book["id"],
        payee="供应商甲",
        payee_account="6222 0000 0000 0001",
        amount_cents=amount,
        idempotency_key=key,
        period="2026-10",
    )


def test_create_is_idempotent_and_records_an_event(business):
    service = _service(business)
    first = _create(service, business)
    again = _create(service, business)
    assert again["id"] == first["id"]
    with business.db.transaction() as conn:
        events = conn.execute(
            "SELECT from_status,to_status FROM payment_events WHERE order_id=?", (first["id"],)
        ).fetchall()
    assert [(row["from_status"], row["to_status"]) for row in events] == [("", "draft")]


def test_maker_cannot_approve_own_payment(business):
    service = _service(business)
    order = _create(service, business)
    service.submit(business.actors["accountant"], order["id"])
    with pytest.raises(AccountingError) as caught:
        service.approve(business.actors["accountant"], order["id"])
    assert "制单人不能审批自己发起的付款" in caught.value.detail
    assert caught.value.status == 403


def test_approver_must_differ_from_voucher_reviewer(business):
    service = _service(business)
    order = _create(service, business)
    approver = ensure_independent_reviewer(
        business.db, business.actors["accountant"], business.book["id"]
    )
    service.submit(business.actors["accountant"], order["id"])
    with pytest.raises(AccountingError):
        service.approve(
            business.actors["accountant"], order["id"], voucher_reviewer=approver.user_id
        )


def test_illegal_transition_is_refused(business):
    service = _service(business)
    order = _create(service, business)
    with pytest.raises(AccountingError) as caught:
        service.advance_external(business.actors["accountant"], order["id"], "succeeded")
    assert caught.value.status == 409


def test_bank_unknown_cannot_jump_to_submitted(business):
    service = _service(business)
    order = _create(service, business)
    approver = ensure_independent_reviewer(
        business.db, business.actors["accountant"], business.book["id"]
    )
    actor = business.actors["accountant"]
    service.submit(actor, order["id"])
    service.approve(approver, order["id"])
    service.advance_external(actor, order["id"], "queued_for_bank")
    service.advance_external(actor, order["id"], "submitted")
    service.advance_external(actor, order["id"], "bank_unknown")
    with pytest.raises(AccountingError):
        service.advance_external(actor, order["id"], "submitted")
    assert approver.user_id  # 审批链上确有第二个人


def test_large_amount_is_escalated(business):
    service = _service(business)
    order = _create(service, business, amount=5_000_000)
    assert order["escalated"] == 1
