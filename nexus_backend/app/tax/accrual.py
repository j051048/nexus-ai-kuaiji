"""Accrual voucher lines for the four-tax package.

Pure translation: the package (already computed from a *reviewed* VAT
workpaper) becomes balanced double-entry lines. Nothing here decides a tax
amount, guesses an account code, or writes to the ledger — the caller persists
the draft with its own write transaction and its own permission checks.

Account codes must be supplied by config or the caller's chart; when a chart is
given every code is verified to exist, because a silently missing account is how
tax accruals end up in the wrong place.
"""

from ..accounting.domain import AccountingError

#: Default rails; callers should pass codes verified against their chart of accounts.
DEFAULT_ACCOUNTS = {
    "surtax_expense": "6403",  # 税金及附加
    "stamp_expense": "6403",
    "cit_expense": "6801",  # 所得税费用
    "surtax_payable": "2221.03",
    "stamp_payable": "2221.04",
    "cit_payable": "2221.05",
}


def _cents(value, label):
    if type(value) is not int:
        raise AccountingError(f"{label}必须是整数分", 422)
    if value < 0:
        raise AccountingError(f"{label}不能为负（多缴请走退税或留抵）", 422)
    return value


def accrual_lines(package, *, accounts=None, chart=None):
    """Return ``{"entries": [...], "debit_cents": n, "credit_cents": n}``."""
    source = package.get("source") if isinstance(package, dict) else None
    if not source or source.get("status") != "reviewed":
        raise AccountingError(
            "计提凭证只能依据已复核的增值税底稿；当前来源未复核，不能生成草稿", 409
        )
    rails = {**DEFAULT_ACCOUNTS, **(accounts or {})}
    if chart is not None:
        missing = sorted({code for code in rails.values()} - set(chart))
        if missing:
            raise AccountingError(f"计提科目不在当前科目表中：{', '.join(missing)}", 409)

    entries = []

    def post(expense_key, payable_key, cents, memo):
        cents = _cents(cents, memo)
        if not cents:
            return
        entries.append(
            {"account": rails[expense_key], "side": "debit", "cents": cents, "memo": memo}
        )
        entries.append(
            {"account": rails[payable_key], "side": "credit", "cents": cents, "memo": memo}
        )

    post("surtax_expense", "surtax_payable", package["surtax"]["total_cents"], "附加税计提")
    cit_due = package["cit_prepayment"]["due_cents"]
    refund_note = ""
    if type(cit_due) is int and cit_due < 0:
        refund_note = "本期企业所得税为多缴，未生成计提分录；请走退税或抵减"
    else:
        post("cit_expense", "cit_payable", cit_due, "企业所得税预缴计提")
    stamp = package.get("stamp_duty") or {}
    stamp_status = stamp.get("status") or "computed"
    stamp_notice = ""
    if stamp_status == "unprovided":
        # ``total_cents`` 为 None 表示没收到合同资料，不是“算出来是 0”。
        stamp_notice = stamp.get("notice") or "未提供合同资料，本次计提不含印花税"
    else:
        post("stamp_expense", "stamp_payable", stamp.get("total_cents"), "印花税计提")
    if stamp_status == "zero_confirmed":
        stamp_notice = stamp.get("notice") or "已确认本期无应税合同（零业务）"

    debit = sum(item["cents"] for item in entries if item["side"] == "debit")
    credit = sum(item["cents"] for item in entries if item["side"] == "credit")
    if debit != credit:  # pragma: no cover - guarded by construction
        raise AccountingError("计提分录借贷不平，拒绝生成草稿", 500)
    return {
        "period": package.get("period"),
        "source": source,
        "entries": entries,
        "debit_cents": debit,
        "credit_cents": credit,
        "refund_note": refund_note,
        "stamp_duty_status": stamp_status,
        "stamp_duty_notice": stamp_notice,
        "notices": [stamp_notice] if stamp_notice else [],
    }
