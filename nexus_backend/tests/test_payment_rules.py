"""付款内控：三权分立、限额升级、unknown 禁重发、幂等。"""

import pytest

from app.treasury.payments import (
    PaymentRuleError,
    advance,
    check_separation,
    needs_escalation,
    same_payment,
)


def test_maker_cannot_approve_own_payment():
    with pytest.raises(PaymentRuleError) as error:
        check_separation("u-maker", "u-maker", "u-reviewer")
    assert "制单人不能审批" in str(error.value)


def test_approver_cannot_be_the_voucher_reviewer():
    with pytest.raises(PaymentRuleError) as error:
        check_separation("u-maker", "u-approver", "u-approver")
    assert "复核人" in str(error.value)


def test_three_distinct_people_pass():
    check_separation("u-maker", "u-approver", "u-reviewer")


def test_over_single_limit_escalates():
    assert needs_escalation(
        1_000_001, single_limit_cents=1_000_000, daily_used_cents=0, daily_limit_cents=5_000_000
    )


def test_daily_accumulation_escalates():
    assert needs_escalation(
        300_000,
        single_limit_cents=1_000_000,
        daily_used_cents=4_800_000,
        daily_limit_cents=5_000_000,
    )


def test_within_limits_needs_no_escalation():
    assert not needs_escalation(
        300_000, single_limit_cents=1_000_000, daily_used_cents=0, daily_limit_cents=5_000_000
    )


def test_bank_unknown_cannot_be_resubmitted():
    with pytest.raises(PaymentRuleError) as error:
        advance("bank_unknown", "submitted")
    assert "禁止重发" in str(error.value)


def test_bank_unknown_can_close_by_query():
    assert advance("bank_unknown", "succeeded") == "succeeded"


def test_failure_requires_reason():
    with pytest.raises(PaymentRuleError) as error:
        advance("submitted", "failed")
    assert "原因" in str(error.value)
    assert advance("submitted", "failed", reason="银行退票：账号销户") == "failed"


def test_illegal_jump_is_refused():
    with pytest.raises(PaymentRuleError):
        advance("draft", "submitted")


def test_idempotency_key_reuses_existing_payment():
    assert same_payment("pay-1", {"idempotency_key": "pay-1"})
    assert not same_payment("pay-2", {"idempotency_key": "pay-1"})
    with pytest.raises(PaymentRuleError):
        same_payment("  ", {})
