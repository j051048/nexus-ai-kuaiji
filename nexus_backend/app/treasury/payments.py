"""付款指令状态机与资金内控判据（纯函数，不碰数据库）。

铁律：
1. 制单人、资金审批人、凭证复核人必须是三个不同的人；
2. 超过单笔或日累计限额时必须升级到资金主管二次审批；
3. 处于 bank_unknown 的指令禁止重发或切换渠道，只能先查回终态；
4. 幂等键相同即视为同一笔付款，不得生成第二条指令。
"""

from __future__ import annotations

STATES = (
    "draft",
    "pending_approval",
    "approved",
    "queued_for_bank",
    "submitted",
    "succeeded",
    "failed",
    "bounced",
    "bank_unknown",
)

TERMINAL = frozenset({"succeeded", "failed", "bounced"})

TRANSITIONS = {
    "draft": {"pending_approval"},
    "pending_approval": {"approved", "draft"},
    "approved": {"queued_for_bank"},
    "queued_for_bank": {"submitted"},
    "submitted": {"succeeded", "failed", "bounced", "bank_unknown"},
    "bank_unknown": {"succeeded", "failed", "bounced"},
    "succeeded": set(),
    "failed": set(),
    "bounced": set(),
}


class PaymentRuleError(ValueError):
    """违反资金内控判据。"""


def check_separation(maker: str, approver: str, voucher_reviewer: str) -> None:
    """制单、资金审批、凭证复核三权分立。"""
    if not maker or not approver:
        raise PaymentRuleError("付款必须有制单人与资金审批人")
    if maker == approver:
        raise PaymentRuleError("制单人不能审批自己发起的付款")
    if voucher_reviewer and voucher_reviewer in (maker, approver):
        raise PaymentRuleError("资金审批人不能同时是该笔付款凭证的复核人")


def needs_escalation(
    amount_cents: int,
    *,
    single_limit_cents: int,
    daily_used_cents: int,
    daily_limit_cents: int,
) -> bool:
    """超单笔或超日累计都需要二次审批（升级到资金主管）。"""
    if amount_cents <= 0:
        raise PaymentRuleError("付款金额必须为正整数分")
    if single_limit_cents <= 0 or daily_limit_cents <= 0:
        raise PaymentRuleError("限额必须为正整数分")
    return amount_cents > single_limit_cents or daily_used_cents + amount_cents > daily_limit_cents


def advance(state: str, target: str, *, reason: str = "") -> str:
    """只允许白名单内的状态迁移；unknown 只能人工查回终态。"""
    if state not in TRANSITIONS:
        raise PaymentRuleError(f"未知付款状态：{state}")
    if target not in STATES:
        raise PaymentRuleError(f"未知目标状态：{target}")
    if target not in TRANSITIONS[state]:
        if state == "bank_unknown":
            raise PaymentRuleError("银行结果未知时禁止重发或切换渠道，必须先查回原指令终态")
        raise PaymentRuleError(f"不允许从 {state} 迁移到 {target}")
    if target in {"failed", "bounced"} and not reason.strip():
        raise PaymentRuleError("失败或退票必须写明原因")
    return target


def same_payment(idempotency_key: str, existing: dict[str, str]) -> bool:
    """幂等键相同即同一笔付款；重复提交应返回原指令而不是新建。"""
    if not idempotency_key.strip():
        raise PaymentRuleError("付款指令必须有幂等键")
    return existing.get("idempotency_key") == idempotency_key
