"""Versioned financial report snapshots over formal posted vouchers only."""

import hashlib
import json
from uuid import uuid4

from ..accounting.close_dependencies import close_dependencies
from ..accounting.domain import AccountingError
from ..accounting.service import AccountingService, now
from ..posting.service import PostingService
from .mapping import mapping_fingerprint
from .models import ReportCommand, ReportNote
from .reconciliation import canonical_line, compare as compare_reference


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ReportService:
    TEMPLATE_ID = "small_enterprise_basic"
    TEMPLATE_VERSION = 1
    STANDARD_UNASSESSED = "unassessed"

    TEMPLATES = {
        "enterprise": {
            "id": "enterprise_general",
            "name": "企业会计准则一般企业报表（合成模板，未经客户与会计师确认）",
            "version": 1,
            "definition": {
                "kind": "enterprise_general",
                "standard": "enterprise",
                "currency": "CNY",
                "sections": {
                    "balance_sheet": [
                        {"key": "asset", "label": "资产", "category": "asset", "sign": 1},
                        {"key": "liability", "label": "负债", "category": "liability", "sign": -1},
                        {
                            "key": "equity",
                            "label": "所有者权益（或股东权益）",
                            "category": "equity",
                            "sign": -1,
                        },
                    ],
                    "profit_statement": [
                        {"key": "income", "label": "营业收入", "category": "income", "sign": 1},
                        {
                            "key": "expense",
                            "label": "营业成本及费用",
                            "category": "expense",
                            "sign": 1,
                        },
                    ],
                },
            },
        },
        "small_enterprise": {
            "id": "small_enterprise_basic",
            "name": "小企业基础报表",
            "version": 1,
            "definition": {
                "kind": "small_enterprise_basic",
                "standard": "small_enterprise",
                "currency": "CNY",
                "sections": {
                    "balance_sheet": [
                        {"key": "asset", "label": "资产", "category": "asset", "sign": 1},
                        {"key": "liability", "label": "负债", "category": "liability", "sign": -1},
                        {"key": "equity", "label": "所有者权益", "category": "equity", "sign": -1},
                    ],
                    "profit_statement": [
                        {"key": "income", "label": "收入", "category": "income", "sign": 1},
                        {"key": "expense", "label": "费用", "category": "expense", "sign": 1},
                    ],
                },
            },
        },
    }

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

    def _active_policy(self, conn, ledger_id):
        return conn.execute(
            "SELECT * FROM accounting_policies WHERE ledger_id=? AND status='active' "
            "ORDER BY version DESC LIMIT 1",
            (ledger_id,),
        ).fetchone()

    def _standard_kind(self, conn, ledger_id):
        row = self._active_policy(conn, ledger_id)
        return row["standard_kind"] if row else self.STANDARD_UNASSESSED

    def _validate_definition(self, definition):
        if not isinstance(definition, dict):
            raise AccountingError("报表模板定义格式无效", 422)
        if definition.get("currency") != "CNY":
            raise AccountingError("报表模板币种仅支持 CNY", 422)
        sections = definition.get("sections")
        if not isinstance(sections, dict):
            raise AccountingError("报表模板缺少行定义", 422)
        allowed = {"asset", "liability", "equity", "income", "expense"}
        for name in ("balance_sheet", "profit_statement"):
            rows = sections.get(name)
            if not isinstance(rows, list) or not rows:
                raise AccountingError("报表模板行定义不完整", 422)
            for row in rows:
                if not isinstance(row, dict) or row.get("key") not in allowed:
                    raise AccountingError("报表模板行定义含未知科目分组", 422)
                if not isinstance(row.get("label"), str) or not row["label"].strip():
                    raise AccountingError("报表模板行缺少名称", 422)
        return definition

    def _template_for_standard(self, conn, standard):
        spec = self.TEMPLATES.get(standard)
        if not spec:
            raise AccountingError(f"账套适用的报表模板未配置：{standard}", 409)
        self._validate_definition(spec["definition"])
        row = conn.execute(
            "SELECT * FROM report_templates WHERE id=? AND version=? AND active=1",
            (spec["id"], spec["version"]),
        ).fetchone()
        if row:
            self._validate_definition(json.loads(row["definition_json"]))
            return row
        inactive = conn.execute(
            "SELECT 1 FROM report_templates WHERE id=? AND version=?",
            (spec["id"], spec["version"]),
        ).fetchone()
        if inactive:
            raise AccountingError(f"账套适用的报表模板未启用：{standard}", 409)
        stamp = now()
        conn.execute(
            "INSERT INTO report_templates(id,name,standard,version,effective_from,definition_json,active,created_at) "
            "VALUES(?,?,?,?,?,?,1,?)",
            (
                spec["id"],
                spec["name"],
                standard,
                spec["version"],
                "2026-01",
                json.dumps(spec["definition"], ensure_ascii=False),
                stamp,
            ),
        )
        return conn.execute(
            "SELECT * FROM report_templates WHERE id=? AND version=?",
            (spec["id"], spec["version"]),
        ).fetchone()

    def _template_for_book(self, conn, book):
        ledger = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()
        if not ledger:
            raise AccountingError("账套未关联长期账套", 409)
        standard = self._standard_kind(conn, ledger["ledger_id"])
        if standard == self.STANDARD_UNASSESSED:
            raise AccountingError(
                "核算政策未核定，不能生成报表；请先批准当前期间适用的核算政策", 409
            )
        return self._template_for_standard(conn, standard)

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

    def _cash_flow(self, conn, book, cash_account_ids, accounts):
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
        unclassified_count = 0
        unclassified_absolute = 0
        for lines in by_voucher.values():
            cash = [line for line in lines if line["account_id"] in cash_account_ids]
            if not cash:
                continue
            noncash = [line for line in lines if line["account_id"] not in cash_account_ids]
            if len(cash) > 1 and not noncash:
                continue
            if len(cash) != 1 or not noncash:
                amount = sum(int(line["debit_cents"]) - int(line["credit_cents"]) for line in cash)
                unclassified += amount
                unclassified_count += 1
                unclassified_absolute += abs(amount)
                continue
            categories = {
                accounts.get(line["account_id"], {}).get("category", "other") for line in noncash
            }
            codes = {accounts.get(line["account_id"], {}).get("code", "") for line in noncash}
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
                unclassified_count += 1
                unclassified_absolute += abs(amount)
        return {
            "operating_cents": operating,
            "investing_cents": investing,
            "financing_cents": financing,
            "unclassified_cents": unclassified,
            "unclassified_count": unclassified_count,
            "unclassified_absolute_cents": unclassified_absolute,
        }

    def _build(self, actor, book):
        with self.db.transaction(actor=actor) as conn:
            return self._build_in_transaction(conn, actor, book)

    def _build_in_transaction(self, conn, actor, book, trial=None, template=None):
        template = template or self._template_for_book(conn, book)
        definition = self._validate_definition(json.loads(template["definition_json"]))
        trial = trial or self.posting.trial_balance_in_transaction(conn, actor, book["id"])
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
        # Use the same period-resolved identities as the formal trial balance.
        # A future version must neither rewrite history for managers nor vanish
        # behind a book reader's narrower current-period RLS projection.
        accounts = self.posting._accounts(conn, binding["ledger_id"], book["period"])
        cash_ids = {
            account_id
            for account_id, account in accounts.items()
            if account["code"].startswith(("1001", "1002"))
        }
        cash_flow = self._cash_flow(conn, book, cash_ids, accounts)
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
        cash_ok = (
            classified + cash_flow["unclassified_cents"] == cash_net
            and cash_flow["unclassified_count"] == 0
            and cash_flow["unclassified_absolute_cents"] == 0
        )

        by_section = {
            "asset": assets,
            "liability": liabilities,
            "equity": equity,
            "income": income,
            "expense": expense,
        }
        balance_items = []
        for section in definition["sections"]["balance_sheet"]:
            for row in by_section[section["key"]]:
                amount = signed(row)
                if section["sign"] < 0:
                    amount = -amount
                balance_items.append(
                    {
                        "section": section["key"],
                        "account_id": row["account_id"],
                        "code": row["code"],
                        "name": row["name"],
                        "amount_cents": amount,
                    }
                )
        profit_items = []
        for section in definition["sections"]["profit_statement"]:
            for row in by_section[section["key"]]:
                amount = period_net(row)
                if section["sign"] < 0:
                    amount = -amount
                profit_items.append(
                    {
                        "section": section["key"],
                        "account_id": row["account_id"],
                        "code": row["code"],
                        "name": row["name"],
                        "amount_cents": amount,
                    }
                )
        content = {
            "balance_sheet": {
                "label": "资产负债表",
                "assets": total_assets,
                "liabilities": total_liabilities,
                "equity": total_equity,
                "unclosed_profit": profit_in_equity,
                "items": balance_items,
            },
            "profit_statement": {
                "label": "利润表",
                "revenue_cents": revenue,
                "expense_cents": expenses,
                "profit_cents": profit,
                "items": profit_items,
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
                "unclassified_cash_count": cash_flow["unclassified_count"],
                "unclassified_cash_absolute_cents": cash_flow["unclassified_absolute_cents"],
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
                "template": {
                    "id": template["id"],
                    "version": int(template["version"]),
                    "definition_fingerprint": _fingerprint(definition),
                },
                "policy_and_schema_revisions": {
                    key: value
                    for key, value in close_dependencies(conn, book["id"], template["id"]).items()
                    if key != "book"
                },
            }
        )
        return content, fingerprint

    def _formal_reasons(self, conn, book, policy, template, content):
        reasons = []
        if not policy:
            reasons.append("缺少生效的核算政策")
        else:
            if not policy["approved_by"]:
                reasons.append("核算政策尚未批准")
            if bool(policy["is_synthetic"]):
                reasons.append("核算政策为合成演练，不可正式出表")
            if policy["formal_book_system"] == "unassessed":
                reasons.append("正式账所在系统未核定")
            effective_to = policy["effective_to"]
            if book["period"] < policy["effective_from"] or (
                effective_to and book["period"] > effective_to
            ):
                reasons.append("当前期间不在核算政策生效期内")
            current_mapping = mapping_fingerprint(conn, policy["firm_id"])
            if (
                not policy["mapping_fingerprint"]
                or policy["mapping_fingerprint"] != current_mapping
            ):
                reasons.append("科目到报表行次的映射在政策批准后发生变化")
        checks = content["checks"]
        if not all(
            checks.get(key)
            for key in ("balance_sheet_passed", "profit_statement_passed", "cash_flow_passed")
        ):
            reasons.append("报表勾稽未通过")
        if (
            checks.get("unclassified_cash_cents")
            or checks.get("unclassified_cash_count")
            or checks.get("unclassified_cash_absolute_cents")
        ):
            reasons.append("存在未分类现金流量")
        return reasons

    def create(self, actor, book_id, period):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, book_id)
            if period != book["period"]:
                raise AccountingError("报表期间与账套期间不一致", 422)
            self._book(conn, actor, book_id, write=True)
            policy = self._active_policy(conn, ledger_id)
            reasons = []
            standard = policy["standard_kind"] if policy else "small_enterprise"
            try:
                template = self._template_for_standard(conn, standard)
            except AccountingError as exc:
                reasons.append(str(exc))
                template = self._template_for_standard(conn, "small_enterprise")
            content, fingerprint = self._build_in_transaction(conn, actor, book, template=template)
            reasons.extend(self._formal_reasons(conn, book, policy, template, content))
            mode = "formal" if not reasons else "trial"
            mapping = mapping_fingerprint(conn, actor.firm_id)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM report_runs WHERE book_id=? AND period=? AND template_id=?",
                (book["id"], period, template["id"]),
            ).fetchone()[0]
            report_id = uuid4().hex
            content_json = json.dumps(content, ensure_ascii=False, sort_keys=True)
            conn.execute(
                "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
                "inputs_fingerprint,content_json,content_hash,created_by,created_at,mode,standard_kind,"
                "formal_book_system,formal_book_system_name,policy_version,policy_approver,"
                "mapping_fingerprint,trial_reasons_json) "
                "VALUES(?,?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    book["id"],
                    period,
                    template["id"],
                    template["version"],
                    version,
                    fingerprint,
                    content_json,
                    _fingerprint(content),
                    actor.user_id,
                    now(),
                    mode,
                    standard,
                    policy["formal_book_system"] if policy else "unassessed",
                    policy["formal_book_system_name"] if policy else "",
                    int(policy["version"]) if policy else None,
                    policy["approved_by"] if policy else None,
                    mapping,
                    json.dumps(reasons, ensure_ascii=False),
                ),
            )
            self._audit(
                conn,
                actor,
                book["id"],
                "report.create",
                report_id,
                {"version": version, "mode": mode, "trial_reasons": reasons},
            )
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
            self._assert_mapping_current(conn, row)
            if row["status"] not in {"draft", "validated"}:
                raise AccountingError("当前报表状态不能核验", 409)
            content = json.loads(row["content_json"])
            if not all(
                content["checks"].get(key)
                for key in ("balance_sheet_passed", "profit_statement_passed", "cash_flow_passed")
            ):
                raise AccountingError("报表勾稽未通过，不能标记核验", 409)
            if (
                content["checks"].get("unclassified_cash_cents")
                or content["checks"].get("unclassified_cash_count")
                or content["checks"].get("unclassified_cash_absolute_cents")
            ):
                raise AccountingError("存在未分类现金流量，不能标记核验", 409)
            conn.execute("UPDATE report_runs SET status='validated' WHERE id=?", (report_id,))
            return self._view(conn, report_id)

    def _current(self, actor, report_id):
        with self.db.transaction(actor=actor) as conn:
            return self._current_in_transaction(conn, actor, report_id)

    def _assert_mapping_current(self, conn, row):
        if row["mode"] != "formal" or not row["mapping_fingerprint"]:
            return
        book = conn.execute("SELECT firm_id FROM books WHERE id=?", (row["book_id"],)).fetchone()
        if book and mapping_fingerprint(conn, book["firm_id"]) != row["mapping_fingerprint"]:
            raise AccountingError("科目到报表行次的映射已变化，请重新生成报表", 409)

    def _current_in_transaction(self, conn, actor, report_id, trial=None):
        row = self._report(conn, actor, report_id)
        self._assert_mapping_current(conn, row)
        book = self.accounting.access.book(conn, actor, row["book_id"], "read")
        template = conn.execute(
            "SELECT * FROM report_templates WHERE id=? AND version=? AND active=1",
            (row["template_id"], row["template_version"]),
        ).fetchone()
        if not template:
            raise AccountingError("报表模板版本已变化，请重新生成报表", 409)
        content, fingerprint = self._build_in_transaction(
            conn, actor, book, trial=trial, template=template
        )
        if (
            fingerprint != row["inputs_fingerprint"]
            or _fingerprint(json.loads(row["content_json"])) != row["content_hash"]
            or _fingerprint(content) != row["content_hash"]
        ):
            raise AccountingError("报表输入已变化，请重新生成报表", 409)
        return row

    def review(self, actor, report_id, request: ReportCommand):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._report(conn, actor, report_id, write=True)
            if row["mode"] != "formal":
                raise AccountingError("试算报表不能进入复核", 409)
            self._current_in_transaction(conn, actor, report_id)
            if row["status"] != "validated":
                raise AccountingError("只有核验通过的报表才能复核", 409)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("报表版本已变化", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("报表编制人与复核人必须分离", 403)
            conn.execute(
                "UPDATE report_runs SET status='reviewed',reviewed_by=?,reviewed_at=? WHERE id=?",
                (actor.user_id, now(), report_id),
            )
            return self._view(conn, report_id)

    def publish(self, actor, report_id, request: ReportCommand):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._report(conn, actor, report_id, write=True)
            if row["mode"] != "formal":
                reasons = json.loads(row["trial_reasons_json"] or "[]")
                raise AccountingError("试算报表不能发布：" + "；".join(reasons), 409)
            self._current_in_transaction(conn, actor, report_id)
            if row["status"] == "published":
                return self._view(conn, report_id)
            if row["status"] != "reviewed":
                raise AccountingError("只有已复核的报表才能发布", 409)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("报表版本已变化", 409)
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

    def reconcile(self, actor, report_id, request):
        with self.db.transaction(actor=actor) as conn:
            row = self._report(conn, actor, report_id)
            self._assert_mapping_current(conn, row)
            content = json.loads(row["content_json"])
            result = compare_reference(request.csv_text, content)
            result["report_id"] = report_id
            result["report_mode"] = row["mode"]
            result["reference_name"] = request.reference_name
            return result

    def explain_difference(self, actor, report_id, request):
        with self.db.transaction(actor=actor) as conn:
            row = self._report(conn, actor, report_id)
            book, _ = self._book(conn, actor, row["book_id"])
            company_id = book["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._report(conn, actor, report_id, write=True)
            self._assert_mapping_current(conn, row)
            line = canonical_line(request.line_key)
            note_id = uuid4().hex
            conn.execute(
                "INSERT INTO report_reconciliation_notes(id,report_id,book_id,firm_id,"
                "reference_sha256,line_key,explanation_type,note,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    note_id,
                    report_id,
                    row["book_id"],
                    actor.firm_id,
                    request.reference_sha256,
                    request.line_key,
                    request.explanation_type,
                    request.note,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                row["book_id"],
                "report.reconciliation.explain",
                note_id,
                {
                    "report_id": report_id,
                    "reference_sha256": request.reference_sha256,
                    "line_key": request.line_key,
                    "explanation_type": request.explanation_type,
                    "reason": request.reason,
                },
            )
            return {
                "id": note_id,
                "report_id": report_id,
                "reference_sha256": request.reference_sha256,
                "line_key": request.line_key,
                "line_name": line["line_name"],
                "explanation_type": request.explanation_type,
                "note": request.note,
                "created_by": actor.user_id,
                "created_at": now(),
            }

    def reconciliation_notes(self, actor, report_id, reference_sha256):
        with self.db.transaction(actor=actor) as conn:
            self._report(conn, actor, report_id)
            rows = conn.execute(
                "SELECT * FROM report_reconciliation_notes "
                "WHERE report_id=? AND reference_sha256=? ORDER BY created_at,id",
                (report_id, reference_sha256),
            ).fetchall()
            return {"notes": [dict(row) for row in rows]}

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM report_runs WHERE book_id=? ORDER BY period DESC,version DESC",
                (book["id"],),
            ).fetchall()
            reports = []
            for row in rows:
                report = dict(row)
                report["trial_reasons"] = json.loads(report.get("trial_reasons_json") or "[]")
                report["mode_label"] = "试算，不可对外" if report.get("mode") == "trial" else "正式"
                reports.append(report)
            return reports

    def _view(self, conn, report_id):
        row = conn.execute("SELECT * FROM report_runs WHERE id=?", (report_id,)).fetchone()
        report = dict(row)
        report["trial_reasons"] = json.loads(report.get("trial_reasons_json") or "[]")
        report["mode_label"] = "试算，不可对外" if report.get("mode") == "trial" else "正式"
        return {"report": report, "content": json.loads(row["content_json"])}

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
