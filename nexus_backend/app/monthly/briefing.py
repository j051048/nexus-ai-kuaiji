"""锁期后的月度财税简报：只读聚合，不生成新会计事实，不暴露原始票据。"""

import json

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money, signed_cents_to_money
from ..accounting.service import now
from ..closing.queries import active_source_sql, receipt_valid_sql
from .briefing_source import resolve_source


class BriefingService:
    def __init__(self, db, accounting):
        self.db, self.accounting = db, accounting
        self.access = accounting.access

    def _book_block(self, book, company, close):
        return {
            "id": book["id"],
            "name": book["name"],
            "period": book["period"],
            "company_id": book["company_id"],
            "company_name": company["name"] if company else "",
            "period_status": close,
        }

    def _pending(self, source, book, company):
        """没有已发布冻结报表时只给状态，不给任何金额。"""
        reason = source["reason"]
        label = (
            "待会计复核 / 发布"
            if reason in {"no_published_report", "close_not_locked"}
            else "成果版本不一致，需要会计处理"
        )
        return {
            "book": self._book_block(book, company, "closed"),
            "source": source,
            "client_visible": False,
            "pending_label": label,
            "flows": None,
            "top_expenses": [],
            "balances": {
                "receivable": None,
                "payable": None,
                "basis": "正式报表尚未发布，不展示草稿或旧口径金额",
            },
            "tax": {"change": None, "note": "正式报表尚未发布，税额以待发布报表为准。"},
            "risks": [],
            "notes": [
                "客户可见口径只认已发布且与锁期一致的冻结报表；未发布时故意留空。",
                "草稿、旧期间关闭与人工余额都不能替代正式报表金额。",
            ],
            "generated_at": now(),
        }

    def _published(self, source, book, company):
        amounts = source["amounts"]
        flows = amounts["flows"]
        net = flows["net"]
        report = source["report"]
        expenses = sorted(
            (
                {
                    "account_code": item.get("code", ""),
                    "name": item.get("name", ""),
                    "amount": signed_cents_to_money(int(item.get("amount_cents", 0))),
                    "cents": int(item.get("amount_cents", 0)),
                }
                for item in amounts["expenses"]
                if int(item.get("amount_cents", 0)) != 0
            ),
            key=lambda row: abs(row["cents"]),
            reverse=True,
        )[:5]
        expenses = [
            {"account_code": row["account_code"], "name": row["name"], "amount": row["amount"]}
            for row in expenses
        ]
        risks = []
        if net < 0:
            risks.append({"kind": "profit", "text": "本期利润表为亏损，请关注经营安排"})
        return {
            "book": self._book_block(book, company, "closed"),
            "source": source,
            "client_visible": True,
            "flows": {
                "income": signed_cents_to_money(flows["income"]),
                "expense": signed_cents_to_money(flows["expense"]),
                "net": signed_cents_to_money(net),
                "basis": "取自已发布冻结报表的本期利润表合计（营业收入 / 营业成本与费用 / 净利润）",
            },
            "top_expenses": expenses,
            "balances": {
                "receivable": signed_cents_to_money(amounts["receivable"]),
                "payable": signed_cents_to_money(amounts["payable"]),
                "basis": f"取自已发布冻结报表资产负债表 V{report['version']}",
            },
            "tax": {
                "change": signed_cents_to_money(amounts["tax"]),
                "note": "取自已发布冻结报表中应交税费科目余额，最终以纳税申报为准。",
            },
            "risks": risks,
            "notes": [
                f"金额来自已发布冻结报表 V{report['version']}（内容指纹 {report['content_hash'][:12]}）。",
                "内部锁期、外部回执、税务申报、通知送达与客户签收是五条独立轴线，任一完成不能替代其他。",
                "本简报不是财务报表本身，法定报表以冻结版本为准。",
            ],
            "generated_at": now(),
        }

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
            source = resolve_source(conn, book, book["period"], self.db.dialect)
            if source["state"] in {"pending_report", "inconsistent"}:
                return self._pending(source, book, company)
            if source["state"] == "published":
                return self._published(source, book, company)
            active = active_source_sql(self.db.dialect, "s")
            flows = {"income": 0, "expense": 0}
            for row in conn.execute(
                "SELECT direction,COALESCE(SUM(amount_cents),0) AS total FROM sources s "
                "WHERE s.book_id=? AND s.source_kind='bank' AND " + active + " GROUP BY direction",
                (book_id,),
            ):
                flows[row["direction"]] = int(row["total"])
            top = [
                {
                    "account_code": row["account_code"],
                    "name": row["name"],
                    "amount": signed_cents_to_money(int(row["amount_cents"])),
                }
                for row in conn.execute(
                    "SELECT l.account_code,a.name,SUM(l.debit_cents-l.credit_cents) AS amount_cents "
                    "FROM draft_lines l JOIN drafts d ON d.id=l.draft_id AND d.book_id=l.book_id "
                    "JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id "
                    "JOIN accounts a ON a.book_id=l.book_id AND a.code=l.account_code "
                    "WHERE l.book_id=? AND d.status IN ('approved','exported') AND a.category='expense' "
                    "AND " + active + " "
                    "GROUP BY l.account_code,a.name HAVING SUM(l.debit_cents-l.credit_cents)<>0 "
                    "ORDER BY SUM(l.debit_cents-l.credit_cents) DESC,l.account_code LIMIT 5",
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
                "source": source,
                "client_visible": False,
                "pending_label": "历史账期口径：待正式报表发布",
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
                    "net": signed_cents_to_money(net),
                    "basis": "按有效银行来源流水的收入 / 支出合计（已排除已隔离重复与替代前来源）",
                },
                "top_expenses": top,
                "balances": {
                    "receivable": None if receivable is None else signed_cents_to_money(receivable),
                    "payable": None if payable is None else signed_cents_to_money(payable),
                    "basis": "人工确认的期末参考余额"
                    if baseline
                    else "本期未提供人工确认的期末参考余额",
                },
                "tax": {
                    "change": signed_cents_to_money(tax),
                    "note": "按已审核凭证的应交税费类科目净变动汇总，最终以纳税申报为准。",
                },
                "risks": risks,
                "notes": [
                    "历史账期口径：本账套没有已发布冻结报表，以下金额仅供内部核对，不对客户展示。",
                    "简报只聚合已锁期账套的银行流水、已审核凭证与人工确认的期末参考余额，不包含员工票据原件。",
                    "本简报不是财务报表，也不能替代法定申报与客户外部账套过账。",
                ],
                "generated_at": now(),
            }
