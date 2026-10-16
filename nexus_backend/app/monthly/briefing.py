"""锁期后的月度财税简报：只读聚合，不生成新会计事实，不暴露原始票据。"""

import json

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money
from ..accounting.service import now
from ..closing.queries import receipt_valid_sql


class BriefingService:
    def __init__(self, db, accounting):
        self.db, self.accounting = db, accounting
        self.access = accounting.access

    def briefing(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            firm_role = self.access.firm_role(conn, actor)
            company_role = self.access.company_role(conn, actor, book["company_id"])
            if not firm_role and company_role not in {"owner", "admin", "finance"}:
                raise AccountingError("月度财税简报只向事务所与企业管理者开放", 403)
            status = conn.execute(
                "SELECT status FROM periods WHERE book_id=?", (book_id,)
            ).fetchone()
            if not status or status["status"] != "closed":
                raise AccountingError("月结锁期后才能查看月度财税简报", 409)
            company = conn.execute(
                "SELECT name FROM companies WHERE id=?", (book["company_id"],)
            ).fetchone()
            flows = {"income": 0, "expense": 0}
            for row in conn.execute(
                "SELECT direction,COALESCE(SUM(amount_cents),0) AS total FROM sources "
                "WHERE book_id=? AND source_kind='bank' GROUP BY direction",
                (book_id,),
            ):
                flows[row["direction"]] = int(row["total"])
            top = [
                {
                    "account_code": row["account_code"],
                    "name": row["name"],
                    "amount": cents_to_money(abs(int(row["amount_cents"]))),
                }
                for row in conn.execute(
                    "SELECT l.account_code,a.name,SUM(l.debit_cents-l.credit_cents) AS amount_cents "
                    "FROM draft_lines l JOIN drafts d ON d.id=l.draft_id AND d.book_id=l.book_id "
                    "JOIN accounts a ON a.book_id=l.book_id AND a.code=l.account_code "
                    "WHERE l.book_id=? AND d.status IN ('approved','exported') AND a.category='expense' "
                    "GROUP BY l.account_code,a.name HAVING SUM(l.debit_cents-l.credit_cents)<>0 "
                    "ORDER BY ABS(SUM(l.debit_cents-l.credit_cents)) DESC,l.account_code LIMIT 5",
                    (book_id,),
                )
            ]
            baseline = conn.execute(
                "SELECT b.lines_json FROM monthly_baselines b "
                "JOIN monthly_baseline_confirmations cf ON cf.baseline_id=b.id "
                "WHERE b.book_id=? AND b.kind='closing' ORDER BY b.version DESC LIMIT 1",
                (book_id,),
            ).fetchone()
            receivable = payable = None
            if baseline:
                lines = json.loads(baseline["lines_json"])
                receivable = sum(
                    int(item.get("balance_cents", 0))
                    for item in lines
                    if str(item.get("account_code", "")).startswith(("1122", "1221"))
                )
                payable = sum(
                    int(item.get("balance_cents", 0))
                    for item in lines
                    if str(item.get("account_code", "")).startswith(("2202", "2203"))
                )
            tax = int(
                conn.execute(
                    "SELECT COALESCE(SUM(l.credit_cents-l.debit_cents),0) AS total "
                    "FROM draft_lines l JOIN drafts d ON d.id=l.draft_id AND d.book_id=l.book_id "
                    "JOIN accounts a ON a.book_id=l.book_id AND a.code=l.account_code "
                    "WHERE l.book_id=? AND d.status IN ('approved','exported') AND a.code LIKE '2221%'",
                    (book_id,),
                ).fetchone()["total"]
            )
            receipt = receipt_valid_sql(self.db.dialect)
            unverified = int(
                conn.execute(
                    "SELECT COUNT(*) AS total FROM exports x WHERE x.book_id=? AND NOT " + receipt,
                    (book_id,),
                ).fetchone()["total"]
            )
            open_cases = int(
                conn.execute(
                    "SELECT COUNT(*) AS total FROM case_tasks t WHERE t.state<>'closed' AND "
                    "(t.book_id=? OR t.event_id IN (SELECT m.event_id FROM business_mappings m WHERE m.book_id=?))",
                    (book_id, book_id),
                ).fetchone()["total"]
            )
            risks = []
            if unverified:
                risks.append(
                    {"kind": "delivery", "text": f"有 {unverified} 个导出批次尚未核验导入回执"}
                )
            if open_cases:
                risks.append(
                    {"kind": "cases", "text": f"仍有 {open_cases} 个未关闭的异常或补件任务"}
                )
            net = flows["income"] - flows["expense"]
            if net < 0:
                risks.append({"kind": "cash", "text": "本月银行净流出为负，请关注资金安排"})
            return {
                "book": {
                    "id": book["id"],
                    "name": book["name"],
                    "period": book["period"],
                    "company_id": book["company_id"],
                    "company_name": company["name"] if company else "",
                    "period_status": "closed",
                },
                "flows": {
                    "income": cents_to_money(flows["income"]),
                    "expense": cents_to_money(flows["expense"]),
                    "net": cents_to_money(net),
                    "basis": "按银行来源流水的收入 / 支出合计",
                },
                "top_expenses": top,
                "balances": {
                    "receivable": None if receivable is None else cents_to_money(receivable),
                    "payable": None if payable is None else cents_to_money(payable),
                    "basis": "人工确认的期末参考余额"
                    if baseline
                    else "本期未提供人工确认的期末参考余额",
                },
                "tax": {
                    "change": cents_to_money(tax),
                    "note": "按已审核凭证的应交税费类科目净变动汇总，最终以纳税申报为准。",
                },
                "risks": risks,
                "notes": [
                    "简报只聚合已锁期账套的银行流水、已审核凭证与人工确认的期末参考余额，不包含员工票据原件。",
                    "金额以分为单位精确计算；净额为正表示银行净流入。",
                    "本简报不是财务报表，也不能替代法定申报与客户外部账套过账。",
                ],
                "generated_at": now(),
            }
