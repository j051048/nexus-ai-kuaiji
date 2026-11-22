"""Turn four-tax accrual lines into a manual-voucher review request.

The product already owns one compliant path for a human-entered voucher:
``ManualReviewService.prepare`` → ``approve`` → ``post`` (mounted at
``/api/posting/manual-requests``). This module only translates an accrual into
that request; it does not create a second posting path, a second table, or a
second approval rule.

Two things are load-bearing:

* the ``command_key`` is derived from the *workpaper identity* (id, version,
  source digest), so clicking twice produces the same key and the existing
  idempotency check replays instead of posting a second voucher;
* every line carries its account version, because a posting against a stale
  account version must fail rather than land on a silently changed account.
"""

import hashlib

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money

#: Debit/credit side → the field name the manual-voucher contract expects.
SIDE_FIELD = {"debit": "debit", "credit": "credit"}


def normalize_source(source):
    """Accept both the package spelling and the normalized spelling.

    ``four_tax.package`` names the source fields ``id``/``version``/``digest``;
    the command key needs explicit names. Tolerating both here is deliberate: if
    only one spelling were understood, the same workpaper could mint two keys —
    and two keys mean two vouchers for one accrual.
    """
    source = source if isinstance(source, dict) else {}
    version = source.get("workpaper_version")
    if version is None:
        version = source.get("version")
    digest = source.get("source_digest")
    if digest is None:
        digest = source.get("digest")
    return {
        "workpaper_id": str(source.get("workpaper_id") or source.get("id") or ""),
        "workpaper_version": "" if version is None else str(version),
        "source_digest": str(digest or ""),
        "status": str(source.get("status") or ""),
    }


def command_key_for(source):
    """Deterministic key from the reviewed workpaper identity."""
    normalized = normalize_source(source)
    parts = [
        normalized["workpaper_id"],
        normalized["workpaper_version"],
        normalized["source_digest"],
    ]
    if not all(parts):
        raise AccountingError("计提入账需要底稿 id、版本与来源摘要，当前信息不完整", 409)
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return f"tax-accrual:{digest[:40]}"


def _money(cents):
    # 整数分 → "123.45"，不经过浮点：0.1+0.2 那一类误差在凭证上是不能出现的。
    return cents_to_money(cents)


def manual_voucher(package, entries, accounts, *, book_id, voucher_date, description="", reason=""):
    """Build a ``ManualVoucher`` payload.

    ``accounts`` maps account code → ``{"id": ..., "version": n}``; it must cover
    every code the entries reference. Amounts stay integers here and are formatted
    with two decimals only at the contract boundary.
    """
    if not entries:
        raise AccountingError("本期没有可计提的税金，不需要生成凭证", 409)
    period = str(package.get("period") or "")
    if period and str(voucher_date)[:7] != period:
        raise AccountingError("计提凭证日期必须落在计提期间内", 422)
    # ManualVoucher rejects a repeated (account_id, auxiliary_values) pair, and
    # surtax + stamp both debit 6403. Merge same account + same side first; the
    # same account on both sides is ambiguous, so refuse instead of netting.
    merged = {}
    for entry in entries:
        side = entry.get("side")
        if side not in SIDE_FIELD:
            raise AccountingError(f"未知的借贷方向：{side}", 422)
        key = (str(entry.get("account") or ""), side)
        merged[key] = merged.get(key, 0) + int(entry.get("cents") or 0)
    accounts_touched = {code for code, _ in merged}
    clash = [
        code
        for code in accounts_touched
        if (code, "debit") in merged and (code, "credit") in merged
    ]
    if clash:
        raise AccountingError(
            f"科目 {', '.join(sorted(clash))} 同时出现在借贷两方，请确认计提方向后再生成凭证", 409
        )
    entries = [
        {"account": code, "side": side, "cents": cents} for (code, side), cents in merged.items()
    ]

    lines = []
    debit_total = credit_total = 0
    for entry in entries:
        code = str(entry.get("account") or "")
        account = (accounts or {}).get(code)
        if not account:
            raise AccountingError(f"计提科目 {code} 不在当前科目表中，不能生成凭证", 409)
        side = entry.get("side")
        if side not in SIDE_FIELD:
            raise AccountingError(f"未知的借贷方向：{side}", 422)
        cents = entry.get("cents")
        if type(cents) is not int or cents <= 0:
            raise AccountingError("计提金额必须是正整数分", 422)
        if side == "debit":
            debit_total += cents
        else:
            credit_total += cents
        lines.append(
            {
                "account_id": account["id"],
                "account_version": int(account["version"]),
                "debit": _money(cents) if side == "debit" else "0.00",
                "credit": _money(cents) if side == "credit" else "0.00",
                "source_ref": normalize_source(package.get("source"))["workpaper_id"],
            }
        )
    if debit_total != credit_total:
        raise AccountingError("计提分录不平衡，拒绝生成凭证", 409)
    return {
        "command_key": command_key_for(package.get("source") or {}),
        "book_id": book_id,
        "voucher_date": voucher_date,
        "voucher_type": "记",
        "description": description or f"四税计提（{package.get('period', '')}）",
        "lines": lines,
        "reason": reason or "四税底稿计提入账",
    }
