"""Read-only preparation metadata; it neither chooses tax roles nor approves facts.

This projection is intentionally limited to what the existing workpaper service
can already authorize and validate. Generate/review/export remain authoritative.
"""

from ..accounting.domain import AccountingError
from .workpaper import _period, _snapshot


def tax_context(tax, actor, book_id, period=None):
    with tax.db.transaction(actor=actor) as conn:
        book = tax.access.book(conn, actor, book_id, "read")
        period = _period(period or book["period"])
        if period != book["period"]:
            raise AccountingError("底稿期间必须与当前账套月份一致，跨期处理请交人工核验", 409)
        profile = tax._profile(conn, book, period)
        reasons = []
        try:
            tax._guard_period(conn, book, period)
        except AccountingError as failure:
            reasons.append(failure.detail)

        # Each posted account/version is required even when its debit and credit
        # cancel out. Use frozen labels, not current account versions or code guesses.
        rows = conn.execute(
            "SELECT l.account_id,l.account_version,MIN(l.code_snapshot) AS code,"
            "MIN(l.name_snapshot) AS name FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? AND v.period=? AND v.status='posted' "
            "GROUP BY l.account_id,l.account_version ORDER BY code,l.account_version,l.account_id LIMIT 1001",
            (book_id, period),
        ).fetchall()
        if len(rows) > 1000:
            raise AccountingError("本期间发生科目版本超过底稿上限，请交会计分清范围后人工核验", 409)
        accounts = [
            {
                "account_id": row["account_id"],
                "account_version": int(row["account_version"]),
                "code": row["code"],
                "name": row["name"],
            }
            for row in rows
        ]
        if not profile:
            reasons.append(f"缺少{period}生效的纳税人档案，请先登记税务档案")
        else:
            try:
                # Exclusion here is a private validation probe, never a user
                # default or a saved tax declaration. No amount/result/digest is
                # returned: actual roles must be selected by the user at generate.
                snapshot = _snapshot(
                    conn,
                    book,
                    period,
                    profile,
                    [
                        {
                            "account_id": row["account_id"],
                            "account_version": row["account_version"],
                            "role": "excluded",
                        }
                        for row in accounts
                    ],
                )
                if not snapshot["rows"]:
                    reasons.append(f"{period}没有可用的发票导入行，请先导入发票来源")
            except AccountingError as failure:
                reasons.append(failure.detail)
        return {
            "scope": {
                "firm_id": book["firm_id"],
                "company_id": book["company_id"],
                "book_id": book["id"],
                "period": period,
                "book_name": book["name"],
            },
            "permissions": {
                key: bool(book["permissions"][key])
                for key in ("edit", "review", "export", "manage")
            },
            "profile": profile,
            "accounts": accounts,
            "blocking_reasons": list(dict.fromkeys(reasons)),
        }
