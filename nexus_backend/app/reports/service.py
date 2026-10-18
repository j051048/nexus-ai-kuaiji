"""Versioned financial report snapshots over formal posted vouchers only."""

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import AccountingService, now
from ..posting.service import PostingService
from .models import ReportCommand, ReportNote


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ReportService:
    TEMPLATE_ID = "small_enterprise_basic"
    TEMPLATE_VERSION = 1

    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)
        self.posting = PostingService(db)

    def _book(self, conn, actor, book_id, write=False):
        book = self.accounting.access.book(conn, actor, book_id, "review" if write else "read")
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()
        if not ledger:
            raise AccountingError("账套未关联长期账套", 409)
        return book, ledger["ledger_id"]

    def _ensure_template(self, conn):
        row = conn.execute(
            "SELECT * FROM report_templates WHERE id=? AND version=?",
            (self.TEMPLATE_ID, self.TEMPLATE_VERSION),
        ).fetchone()
        if row:
            return row
        definition = {
            "kind": "small_enterprise_basic",
            "currency": "CNY",
            "balance_sheet": "category_and_code_mapping_v1",
            "profit_statement": "category_activity_v1",
            "cash_flow": "single_cash_line_counterparty_category_v1",
        }
        stamp = now()
        conn.execute(
            "INSERT INTO report_templates(id,name,standard,version,effective_from,definition_json,active,created_at) "
            "VALUES(?,?,?,?,?,?,1,?)",
            (
                self.TEMPLATE_ID,
                "小企业基础报表",
                "small_enterprise",
                self.TEMPLATE_VERSION,
                "2026-01",
                json.dumps(definition, ensure_ascii=False),
                stamp,
            ),
        )
        return conn.execute(
            "SELECT * FROM report_templates WHERE id=? AND version=?",
            (self.TEMPLATE_ID, self.TEMPLATE_VERSION),
        ).fetchone()

    def _activity(self, conn, book):
        closing_vouchers = [
            row[0]
            for row in conn.execute(
                "SELECT voucher_id FROM periodic_runs WHERE book_id=? AND kind='closing' "
                "AND status='posted' AND voucher_id IS NOT NULL",
                (book["id"],),
            )
        ]
        sql = (
            "SELECT l.account_id,SUM(l.debit_cents) AS debit,SUM(l.credit_cents) AS credit "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? AND v.voucher_date LIKE ? "
        )
        params = [book["id"], book["period"] + "%"]
        if closing_vouchers:
            sql += "AND v.id NOT IN (" + ",".join("?" * len(closing_vouchers)) + ") "
            params.extend(closing_vouchers)
        sql += "GROUP BY l.account_id"
        return {
            row["account_id"]: (int(row["debit"]), int(row["credit"]))
            for row in conn.execute(sql, params)
        }, closing_vouchers

    def _cash_flow(self, conn, book, cash_account_ids):
        closing_vouchers = [
            row[0]
            for row in conn.execute(
                "SELECT voucher_id FROM periodic_runs WHERE book_id=? AND kind='closing' "
                "AND status='posted' AND voucher_id IS NOT NULL",
                (book["id"],),
            )
        ]
        sql = (
            "SELECT v.id AS voucher_id,v.voucher_no,v.voucher_date,l.account_id,l.debit_cents,"
            "l.credit_cents FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? AND v.voucher_date LIKE ? "
        )
        params = [book["id"], book["period"] + "%"]
        if closing_vouchers:
            sql += "AND v.id NOT IN (" + ",".join("?" * len(closing_vouchers)) + ") "
            params.extend(closing_vouchers)
        sql += "ORDER BY v.voucher_date,v.voucher_no,l.line_no"
        rows = conn.execute(sql, params).fetchall()
        by_voucher = {}
        for row in rows:
            by_voucher.setdefault(row["voucher_id"], []).append(row)
        operating = investing = financing = unclassified = 0
        for lines in by_voucher.values():
            cash = [line for line in lines if line["account_id"] in cash_account_ids]
            if not cash:
                continue
            noncash = [line for line in lines if line["account_id"] not in cash_account_ids]
            if len(cash) > 1 and not noncash:
                continue
            if len(cash) != 1 or not noncash:
                unclassified += sum(
                    int(line["debit_cents"]) - int(line["credit_cents"]) for line in cash
                )
                continue
            categories = {self._category(conn, line["account_id"]) for line in noncash}
            codes = {self._code(conn, line["account_id"]) for line in noncash}
            amount = int(cash[0]["debit_cents"]) - int(cash[0]["credit_cents"])
            if any(
                code.startswith(("1122", "2202", "2203", "2204")) for code in codes
            ) or categories <= {"income", "expense"}:
                operating += amount
            elif categories <= {"asset", "cost"}:
                investing += amount
            elif categories <= {"liability", "equity"}:
                financing += amount
            else:
                unclassified += amount
        return {
            "operating_cents": operating,
            "investing_cents": investing,
            "financing_cents": financing,
            "unclassified_cents": unclassified,
        }

    @staticmethod
    def _category(conn, account_id):
        row = conn.execute(
            "SELECT v.category FROM ledger_accounts a JOIN ledger_account_versions v "
            "ON v.account_id=a.id AND v.version=a.current_version WHERE a.id=?",
            (account_id,),
        ).fetchone()
        return row[0] if row else "other"

    @staticmethod
    def _code(conn, account_id):
        row = conn.execute(
            "SELECT v.code FROM ledger_accounts a JOIN ledger_account_versions v "
            "ON v.account_id=a.id AND v.version=a.current_version WHERE a.id=?",
            (account_id,),
        ).fetchone()
        return row[0] if row else ""

    def _build(self, actor, book):
        trial = self.posting.trial_balance(actor, book["id"])
        binding = None
        with self.db.transaction(actor=actor) as conn:
            binding = conn.execute(
                "SELECT * FROM opening_bindings WHERE book_id=? AND status='active'", (book["id"],)
            ).fetchone()
            if not binding:
                raise AccountingError("未绑定期初，不能生成正式报表", 409)
            activity, closing_vouchers = self._activity(conn, book)
            opening = {
                row["account_id"]: int(row["total"])
                for row in conn.execute(
                    "SELECT account_id,SUM(opening_cents) AS total FROM opening_lines "
                    "WHERE package_version_id=? GROUP BY account_id",
                    (binding["package_version_id"],),
                )
            }
            cash_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT a.id FROM ledger_accounts a JOIN ledger_account_versions v "
                    "ON v.account_id=a.id AND v.version=a.current_version WHERE a.ledger_id=? "
                    "AND (v.code LIKE '1001%' OR v.code LIKE '1002%')",
                    (binding["ledger_id"],),
                )
            }
            cash_flow = self._cash_flow(conn, book, cash_ids)
        rows = trial["rows"]
        leaves = [row for row in rows if row["is_leaf"]]
        assets = [row for row in leaves if row["category"] == "asset"]
        liabilities = [row for row in leaves if row["category"] == "liability"]
        equity = [row for row in leaves if row["category"] == "equity"]
        income = [row for row in leaves if row["category"] == "income"]
        expense = [row for row in leaves if row["category"] == "expense"]

        def signed(row):
            return int(row["closing_cents"])

        def period_net(row):
            debit, credit = activity.get(row["account_id"], (0, 0))
            if row["category"] == "income":
                return credit - debit
            return debit - credit

        total_assets = sum(signed(row) for row in assets)
        total_liabilities = -sum(signed(row) for row in liabilities)
        total_equity = -sum(signed(row) for row in equity)
        current_profit = sum(period_net(row) for row in income) - sum(
            period_net(row) for row in expense
        )
        closing_posted = bool(closing_vouchers)
        profit_in_equity = 0 if closing_posted else current_profit
        balance_ok = total_assets == total_liabilities + total_equity + profit_in_equity

        revenue = sum(period_net(row) for row in income)
        expenses = sum(period_net(row) for row in expense)
        profit = revenue - expenses
        cash_opening = sum(opening.get(account_id, 0) for account_id in cash_ids)
        cash_closing = sum(signed(row) for row in leaves if row["account_id"] in cash_ids)
        cash_net = cash_closing - cash_opening
        classified = (
            cash_flow["operating_cents"]
            + cash_flow["investing_cents"]
            + cash_flow["financing_cents"]
        )
        cash_ok = classified + cash_flow["unclassified_cents"] == cash_net

        content = {
            "balance_sheet": {
                "assets": total_assets,
                "liabilities": total_liabilities,
                "equity": total_equity,
                "unclosed_profit": profit_in_equity,
                "items": [
                    {
                        "section": "asset",
                        "account_id": row["account_id"],
                        "code": row["code"],
                        "name": row["name"],
                        "amount_cents": signed(row),
                    }
                    for row in assets
                ]
                + [
                    {
                        "section": "liability",
                        "account_id": row["account_id"],
                        "code": row["code"],
                        "name": row["name"],
                        "amount_cents": -signed(row),
                    }
                    for row in liabilities
                ]
                + [
                    {
                        "section": "equity",
                        "account_id": row["account_id"],
                        "code": row["code"],
                        "name": row["name"],
                        "amount_cents": -signed(row),
                    }
                    for row in equity
                ],
            },
            "profit_statement": {
                "revenue_cents": revenue,
                "expense_cents": expenses,
                "profit_cents": profit,
                "items": [
                    {
                        "account_id": row["account_id"],
                        "code": row["code"],
                        "name": row["name"],
                        "amount_cents": period_net(row),
                    }
                    for row in income + expense
                ],
            },
            "cash_flow": {
                **cash_flow,
                "opening_cash_cents": cash_opening,
                "closing_cash_cents": cash_closing,
                "net_change_cents": cash_net,
            },
            "checks": {
                "balance_sheet_passed": balance_ok,
                "profit_statement_passed": revenue - expenses == profit,
                "cash_flow_passed": cash_ok,
                "unclassified_cash_cents": cash_flow["unclassified_cents"],
                "closing_posted": closing_posted,
            },
        }
        fingerprint = _fingerprint(
            {
                "book": book["id"],
                "period": book["period"],
                "trial": trial["rows"],
                "activity": activity,
                "opening": opening,
                "cash_flow": cash_flow,
                "closing_vouchers": closing_vouchers,
            }
        )
        return content, fingerprint

    def create(self, actor, book_id, period):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            if period != book["period"]:
                raise AccountingError("报表期间与账套期间不一致", 422)
        content, fingerprint = self._build(actor, book)
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            template = self._ensure_template(conn)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM report_runs WHERE book_id=? AND period=? AND template_id=?",
                (book["id"], period, self.TEMPLATE_ID),
            ).fetchone()[0]
            report_id = uuid4().hex
            content_json = json.dumps(content, ensure_ascii=False, sort_keys=True)
            conn.execute(
                "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
                "inputs_fingerprint,content_json,content_hash,created_by,created_at) "
                "VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?)",
                (
                    report_id,
                    book["id"],
                    period,
                    self.TEMPLATE_ID,
                    template["version"],
                    version,
                    fingerprint,
                    content_json,
                    _fingerprint(content),
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(conn, actor, book["id"], "report.create", report_id, {"version": version})
            return self._view(conn, report_id)

    def _report(self, conn, actor, report_id, write=False):
        row = conn.execute("SELECT * FROM report_runs WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise AccountingError("报表不存在", 404)
        self._book(conn, actor, row["book_id"], write=write)
        return row

    def validate(self, actor, report_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._report(conn, actor, report_id, write=True)
            if row["status"] not in {"draft", "validated"}:
                raise AccountingError("当前报表状态不能核验", 409)
            content = json.loads(row["content_json"])
            if not all(
                content["checks"].get(key)
                for key in ("balance_sheet_passed", "profit_statement_passed", "cash_flow_passed")
            ):
                raise AccountingError("报表勾稽未通过，不能标记核验", 409)
            if content["checks"].get("unclassified_cash_cents"):
                raise AccountingError("存在未分类现金流量，不能标记核验", 409)
            conn.execute("UPDATE report_runs SET status='validated' WHERE id=?", (report_id,))
            return self._view(conn, report_id)

    def _current(self, actor, report_id):
        with self.db.transaction(actor=actor) as conn:
            row = self._report(conn, actor, report_id)
            book = self.accounting.access.book(conn, actor, row["book_id"], "read")
        _, fingerprint = self._build(actor, book)
        if fingerprint != row["inputs_fingerprint"]:
            raise AccountingError("报表输入已变化，请重新生成报表", 409)
        return row

    def review(self, actor, report_id, request: ReportCommand):
        row = self._current(actor, report_id)
        if row["status"] != "validated":
            raise AccountingError("只有核验通过的报表才能复核", 409)
        if int(row["version"]) != request.expected_version:
            raise AccountingError("报表版本已变化", 409)
        if row["created_by"] == actor.user_id:
            raise AccountingError("报表编制人与复核人必须分离", 403)
        with self.db.transaction(write=True, actor=actor) as conn:
            conn.execute(
                "UPDATE report_runs SET status='reviewed',reviewed_by=?,reviewed_at=? WHERE id=?",
                (actor.user_id, now(), report_id),
            )
            return self._view(conn, report_id)

    def publish(self, actor, report_id, request: ReportCommand):
        row = self._current(actor, report_id)
        if row["status"] == "published":
            with self.db.transaction(actor=actor) as conn:
                return self._view(conn, report_id)
        if row["status"] != "reviewed":
            raise AccountingError("只有已复核的报表才能发布", 409)
        if int(row["version"]) != request.expected_version:
            raise AccountingError("报表版本已变化", 409)
        with self.db.transaction(write=True, actor=actor) as conn:
            lock = conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
                (row["book_id"], row["period"]),
            ).fetchone()
            if not lock:
                raise AccountingError("期间未内部锁期，不能发布正式报表", 409)
            conn.execute(
                "UPDATE report_runs SET status='published',published_by=?,published_at=? WHERE id=?",
                (actor.user_id, now(), report_id),
            )
            return self._view(conn, report_id)

    def add_note(self, actor, report_id, request: ReportNote):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._report(conn, actor, report_id, write=True)
            if row["status"] == "published":
                raise AccountingError("已发布报表不能追加未版本化附注", 409)
            conn.execute(
                "INSERT INTO report_notes(id,report_id,note_key,text,created_by,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (uuid4().hex, report_id, request.note_key, request.text, actor.user_id, now()),
            )
            return {"report_id": report_id, "note_key": request.note_key}

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM report_runs WHERE book_id=? ORDER BY period DESC,version DESC",
                (book["id"],),
            ).fetchall()
            return [dict(row) for row in rows]

    def _view(self, conn, report_id):
        row = conn.execute("SELECT * FROM report_runs WHERE id=?", (report_id,)).fetchone()
        return {"report": dict(row), "content": json.loads(row["content_json"])}

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )
