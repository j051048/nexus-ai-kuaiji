"""Read-only continuous close radar.

The radar is a projection over existing service state. It never closes a
period, approves a draft, or sends a request.
"""

import hashlib
from datetime import datetime, timezone

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..posting.integrity import VoucherGapService


class ClosingRadarService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def radar(self, actor, period):
        if (
            len(period) != 7
            or period[4] != "-"
            or not period[:4].isdigit()
            or not period[5:].isdigit()
        ):
            raise AccountingError("期间格式应为 YYYY-MM", 422)
        if not 1 <= int(period[5:]) <= 12:
            raise AccountingError("期间月份无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            books = conn.execute(
                "SELECT b.id,b.company_id,c.name AS company_name,b.period,"
                "COALESCE(p.status,'open') AS period_status FROM books b "
                "JOIN companies c ON c.id=b.company_id "
                "LEFT JOIN periods p ON p.book_id=b.id AND p.period=? "
                "WHERE b.firm_id=? AND b.period=? ORDER BY c.name,b.id",
                (period, actor.firm_id, period),
            ).fetchall()
            items = []
            for book in books:
                drafts = conn.execute(
                    "SELECT count(*) FROM drafts WHERE book_id=? AND status='draft'",
                    (book["id"],),
                ).fetchone()[0]
                materials = conn.execute(
                    "SELECT count(*) FROM material_requests r WHERE r.book_id=? AND r.period=? "
                    "AND r.required=1 AND NOT EXISTS ("
                    "SELECT 1 FROM material_request_actions a WHERE a.request_id=r.id AND a.state='accepted')",
                    (book["id"], period),
                ).fetchone()[0]
                basis = conn.execute(
                    "SELECT count(*) FROM tax_basis_reviews WHERE book_id=? AND period=? AND status='draft'",
                    (book["id"], period),
                ).fetchone()[0]
                bank_accounts = conn.execute(
                    "SELECT count(*) FROM bank_accounts WHERE book_id=? AND active=TRUE",
                    (book["id"],),
                ).fetchone()[0]
                bank_missing = conn.execute(
                    "SELECT count(*) FROM bank_accounts a WHERE a.book_id=? AND a.active=TRUE "
                    "AND NOT EXISTS (SELECT 1 FROM bank_statement_coverages c WHERE c.account_id=a.id AND c.period=?)",
                    (book["id"], period),
                ).fetchone()[0]
                ledger = conn.execute(
                    "SELECT l.id FROM ledgers l JOIN ledger_periods lp ON lp.ledger_id=l.id "
                    "WHERE lp.book_id=? ORDER BY l.created_at DESC LIMIT 1",
                    (book["id"],),
                ).fetchone()
                subledger_pending = 0
                if ledger:
                    setting = conn.execute(
                        "SELECT status FROM subledger_control_settings WHERE ledger_id=?",
                        (ledger["id"],),
                    ).fetchone()
                    if not setting or setting["status"] == "pending":
                        subledger_pending = 1
                blockers = []
                if materials:
                    blockers.append(
                        {
                            "code": "materials_pending",
                            "label": "必需资料未验收",
                            "count": materials,
                            "owner_role": "client_finance",
                            "next_action": "打开月度资料清单，补齐并验收",
                        }
                    )
                if drafts:
                    blockers.append(
                        {
                            "code": "drafts_pending",
                            "label": "凭证待复核",
                            "count": drafts,
                            "owner_role": "reviewer",
                            "next_action": "打开待复核，按当前版本独立复核",
                        }
                    )
                if basis:
                    blockers.append(
                        {
                            "code": "tax_basis_pending",
                            "label": "税费核定待复核",
                            "count": basis,
                            "owner_role": "reviewer",
                            "next_action": "打开税务底稿，复核基数与人工依据",
                        }
                    )
                if not bank_accounts:
                    blockers.append(
                        {
                            "code": "bank_accounts_missing",
                            "label": "银行账户未登记",
                            "count": 1,
                            "owner_role": "client_finance",
                            "next_action": "打开账套设置登记银行账户与期间覆盖",
                        }
                    )
                elif bank_missing:
                    blockers.append(
                        {
                            "code": "bank_coverage_missing",
                            "label": "银行流水覆盖未确认",
                            "count": bank_missing,
                            "owner_role": "client_finance",
                            "next_action": "补齐本期银行账户覆盖并确认",
                        }
                    )
                if subledger_pending:
                    blockers.append(
                        {
                            "code": "subledger_control_pending",
                            "label": "子账控制未配置/未确认",
                            "count": 1,
                            "owner_role": "accountant",
                            "next_action": "打开子账控制设置确认适用性或控制科目",
                        }
                    )
                gaps = VoucherGapService(self.db, self.access).scan_in_transaction(conn, book["id"])
                if gaps["missing"]:
                    blockers.append(
                        {
                            "code": "voucher_number_gap",
                            "label": "凭证编号断号",
                            "count": len(gaps["missing"]),
                            "voucher_numbers": gaps["missing"],
                            "owner_role": "accountant",
                            "next_action": "核对凭证编号连续性并登记核查结论；不得重排已记账凭证",
                        }
                    )
                authoritative = not blockers
                state = (
                    "locked"
                    if book["period_status"] == "closed"
                    else ("blocked" if blockers else "precheck_passed")
                )
                fingerprint = hashlib.sha256(
                    "|".join(
                        [book["id"], period, state]
                        + [f"{b['code']}:{b['count']}" for b in blockers]
                    ).encode()
                ).hexdigest()
                items.append(
                    {
                        "book_id": book["id"],
                        "company_id": book["company_id"],
                        "company_name": book["company_name"],
                        "period": period,
                        "period_status": book["period_status"],
                        "precheck": {
                            "state": state,
                            "check_version": 1,
                            "checked_at": datetime.now(timezone.utc).isoformat(),
                            "fingerprint": fingerprint,
                            "authoritative": authoritative,
                        },
                        "closable": state == "precheck_passed",
                        "blockers": blockers,
                    }
                )
        return {
            "period": period,
            "books": items,
            "counts": {
                "books": len(items),
                "blocked": sum(1 for item in items if item["blockers"]),
                "closable": sum(1 for item in items if item["closable"]),
                "precheck_passed": sum(
                    1 for item in items if item["precheck"]["state"] == "precheck_passed"
                ),
                "not_checked": sum(
                    1 for item in items if item["precheck"]["state"] == "not_checked"
                ),
                "closed": sum(1 for item in items if item["period_status"] == "closed"),
            },
            "note": "只读雷达：不自动关账、不自动复核、不替代项目经理判断。",
        }
