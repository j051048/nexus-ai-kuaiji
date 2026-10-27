"""Deterministic periodic drafts. AI cannot write formal vouchers here."""

import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money, money_to_cents, validate_period
from ..accounting.service import AccountingService, now
from ..posting.models import ManualLine, ManualVoucher
from ..posting.service import PostingService
from .models import (
    AccrualRule,
    ClosingGenerate,
    DepreciableAsset,
    DraftCommand,
    DraftPost,
)


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _month_index(period):
    year, month = map(int, period.split("-"))
    return year * 12 + month - 1


class PeriodicService:
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

    def _account(self, conn, ledger_id, account_id, version):
        row = conn.execute(
            "SELECT a.current_version,v.* FROM ledger_accounts a JOIN ledger_account_versions v "
            "ON v.account_id=a.id AND v.version=? WHERE a.id=? AND a.ledger_id=?",
            (version, account_id, ledger_id),
        ).fetchone()
        if not row or int(row["current_version"]) != int(version) or not row["is_leaf"]:
            raise AccountingError("科目不存在、版本过期或不是末级", 409)
        if row["status"] != "active":
            raise AccountingError("科目已停用", 409)
        return row

    def create_accrual_rule(self, actor, request: AccrualRule):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            for account_id, version in (
                (request.debit_account_id, request.debit_account_version),
                (request.credit_account_id, request.credit_account_version),
            ):
                self._account(conn, ledger_id, account_id, version)
            rule_id = uuid4().hex
            conn.execute(
                "INSERT INTO periodic_rules(id,firm_id,company_id,ledger_id,book_id,kind,name,"
                "config_json,active,version,created_by,created_at) "
                "VALUES(?,?,?,?,?,'accrual',?,?,1,1,?,?)",
                (
                    rule_id,
                    book["firm_id"],
                    book["company_id"],
                    ledger_id,
                    book["id"],
                    request.name,
                    json.dumps(request.model_dump(), ensure_ascii=False),
                    actor.user_id,
                    now(),
                ),
            )
            return {"id": rule_id, **request.model_dump()}

    def create_asset(self, actor, request: DepreciableAsset):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            self._account(
                conn, ledger_id, request.expense_account_id, request.expense_account_version
            )
            self._account(conn, ledger_id, request.accum_account_id, request.accum_account_version)
            if money_to_cents(request.residual) >= money_to_cents(request.cost):
                raise AccountingError("残值必须小于原值", 422)
            asset_id = uuid4().hex
            conn.execute(
                "INSERT INTO periodic_assets(id,book_id,kind,name,cost_cents,residual_cents,"
                "life_months,start_period,accumulated_cents,expense_account_id,"
                "expense_account_version,accum_account_id,accum_account_version,status,"
                "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,'active',?,?)",
                (
                    asset_id,
                    book["id"],
                    request.kind,
                    request.name,
                    money_to_cents(request.cost),
                    money_to_cents(request.residual),
                    request.life_months,
                    request.start_period,
                    request.expense_account_id,
                    request.expense_account_version,
                    request.accum_account_id,
                    request.accum_account_version,
                    actor.user_id,
                    now(),
                ),
            )
            return {"id": asset_id, **request.model_dump()}

    def _run(self, conn, book_id, run_key):
        return conn.execute(
            "SELECT * FROM periodic_runs WHERE book_id=? AND run_key=?", (book_id, run_key)
        ).fetchone()

    def _insert_draft(
        self, conn, actor, book, *, kind, run_key, asset_id, description, lines, fingerprint
    ):
        run_id, draft_id = uuid4().hex, uuid4().hex
        stamp = now()
        conn.execute(
            "INSERT INTO periodic_runs(id,book_id,period,kind,run_key,asset_id,input_fingerprint,"
            "status,draft_id,created_by,created_at) VALUES(?,?,?,?,?,?,?,'generated',?,?,?)",
            (
                run_id,
                book["id"],
                book["period"],
                kind,
                run_key,
                asset_id,
                fingerprint,
                draft_id,
                actor.user_id,
                stamp,
            ),
        )
        conn.execute(
            "INSERT INTO periodic_drafts(id,run_id,book_id,period,kind,description,status,version,"
            "created_by,created_at) VALUES(?,?,?,?,?,?,'draft',1,?,?)",
            (draft_id, run_id, book["id"], book["period"], kind, description, actor.user_id, stamp),
        )
        for index, line in enumerate(lines, start=1):
            conn.execute(
                "INSERT INTO periodic_draft_lines(id,draft_id,line_no,account_id,account_version,"
                "debit_cents,credit_cents,aux_json,source_ref) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    draft_id,
                    index,
                    line["account_id"],
                    line["account_version"],
                    line["debit_cents"],
                    line["credit_cents"],
                    json.dumps(line.get("aux", {}), ensure_ascii=False),
                    line.get("source_ref", ""),
                ),
            )
        self._audit(
            conn,
            actor,
            book["id"],
            "periodic.generate",
            draft_id,
            {"kind": kind, "run_key": run_key, "lines": len(lines)},
        )
        return self._draft_view(conn, draft_id)

    def generate_accrual(self, actor, rule_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            rule = conn.execute(
                "SELECT * FROM periodic_rules WHERE id=? AND active=1", (rule_id,)
            ).fetchone()
            if not rule:
                raise AccountingError("计提规则不存在或已停用", 404)
            book, _ = self._book(conn, actor, rule["book_id"], write=True)
            key = f"accrual:{rule_id}:{book['period']}"
            prior = self._run(conn, book["id"], key)
            if prior:
                return self._draft_view(conn, prior["draft_id"], replayed=True)
            config = json.loads(rule["config_json"])
            amount = money_to_cents(config["amount"])
            lines = [
                {
                    "account_id": config["debit_account_id"],
                    "account_version": config["debit_account_version"],
                    "debit_cents": amount,
                    "credit_cents": 0,
                },
                {
                    "account_id": config["credit_account_id"],
                    "account_version": config["credit_account_version"],
                    "debit_cents": 0,
                    "credit_cents": amount,
                },
            ]
            return self._insert_draft(
                conn,
                actor,
                book,
                kind="accrual",
                run_key=key,
                asset_id=None,
                description="计提：" + rule["name"],
                lines=lines,
                fingerprint=_fingerprint(config),
            )

    def _asset_prior_posted(self, conn, asset_id, period):
        row = conn.execute(
            "SELECT COALESCE(SUM(l.debit_cents),0) FROM periodic_runs r "
            "JOIN periodic_drafts d ON d.run_id=r.id JOIN periodic_draft_lines l ON l.draft_id=d.id "
            "WHERE r.asset_id=? AND r.status='posted' AND r.period<?",
            (asset_id, period),
        ).fetchone()
        return int(row[0])

    def generate_asset(self, actor, asset_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            asset = conn.execute("SELECT * FROM periodic_assets WHERE id=?", (asset_id,)).fetchone()
            if not asset or asset["status"] != "active":
                raise AccountingError("资产不存在或已处置", 404)
            book = conn.execute("SELECT * FROM books WHERE id=?", (asset["book_id"],)).fetchone()
            self._book(conn, actor, book["id"], write=True)
            key = f"{asset['kind']}:{asset_id}:{book['period']}"
            prior = self._run(conn, book["id"], key)
            if prior:
                return self._draft_view(conn, prior["draft_id"], replayed=True)
            start, current = _month_index(asset["start_period"]), _month_index(book["period"])
            if current < start or (
                asset["disposed_period"] and book["period"] > asset["disposed_period"]
            ):
                raise AccountingError("资产尚未开始或已停止计提", 409)
            elapsed = current - start + 1
            depreciable = int(asset["cost_cents"]) - int(asset["residual_cents"])
            monthly = depreciable // int(asset["life_months"])
            total = min(depreciable, monthly * elapsed)
            prior_posted = self._asset_prior_posted(conn, asset_id, book["period"])
            amount = total - prior_posted
            if amount <= 0:
                raise AccountingError("本期无可计提金额", 409)
            if amount > depreciable - prior_posted:
                amount = depreciable - prior_posted
            lines = [
                {
                    "account_id": asset["expense_account_id"],
                    "account_version": asset["expense_account_version"],
                    "debit_cents": amount,
                    "credit_cents": 0,
                },
                {
                    "account_id": asset["accum_account_id"],
                    "account_version": asset["accum_account_version"],
                    "debit_cents": 0,
                    "credit_cents": amount,
                },
            ]
            return self._insert_draft(
                conn,
                actor,
                book,
                kind=asset["kind"],
                run_key=key,
                asset_id=asset_id,
                description=("折旧：" if asset["kind"] == "fixed" else "摊销：") + asset["name"],
                lines=lines,
                fingerprint=_fingerprint({"asset": dict(asset), "period": book["period"]}),
            )

    def _balances_without_closing(self, conn, book):
        binding = conn.execute(
            "SELECT * FROM opening_bindings WHERE book_id=? AND status='active'", (book["id"],)
        ).fetchone()
        if not binding:
            raise AccountingError("未绑定期初，不能结转", 409)
        closing_vouchers = [
            row[0]
            for row in conn.execute(
                "SELECT voucher_id FROM periodic_runs WHERE book_id=? AND kind='closing' "
                "AND status='posted' AND voucher_id IS NOT NULL",
                (book["id"],),
            )
        ]
        rows = conn.execute(
            "SELECT l.account_id,SUM(l.debit_cents) AS debit,SUM(l.credit_cents) AS credit "
            "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=? "
            + (
                "AND v.id NOT IN (" + ",".join("?" * len(closing_vouchers)) + ") "
                if closing_vouchers
                else ""
            )
            + "GROUP BY l.account_id",
            (book["id"], *closing_vouchers),
        ).fetchall()
        opening = {
            row[0]: int(row[1])
            for row in conn.execute(
                "SELECT account_id,SUM(opening_cents) FROM opening_lines WHERE package_version_id=? "
                "GROUP BY account_id",
                (binding["package_version_id"],),
            )
        }
        balances = {account_id: opening.get(account_id, 0) for account_id in opening}
        for row in rows:
            balances[row["account_id"]] = (
                balances.get(row["account_id"], 0) + int(row["debit"]) - int(row["credit"])
            )
        return balances

    def generate_closing(self, actor, request: ClosingGenerate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._book(conn, actor, request.book_id, write=True)
            if request.period != book["period"]:
                raise AccountingError("结转期间与账套期间不一致", 422)
            profit = self._account(
                conn, ledger_id, request.profit_account_id, request.profit_account_version
            )
            if profit["category"] != "equity":
                raise AccountingError("本年利润目标必须是权益类科目", 422)
            key = f"closing:{book['id']}:{book['period']}"
            prior = self._run(conn, book["id"], key)
            if prior:
                return self._draft_view(conn, prior["draft_id"], replayed=True)
            balances = self._balances_without_closing(conn, book)
            categories = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT v.account_id,v.category FROM ledger_account_versions v "
                    "JOIN ledger_accounts a ON a.id=v.account_id AND a.current_version=v.version "
                    "WHERE a.ledger_id=?",
                    (ledger_id,),
                )
            }
            lines, profit_cents = [], 0
            for account_id, balance in balances.items():
                category = categories.get(account_id)
                if category == "income" and balance < 0:
                    amount = -balance
                    lines.append(
                        {
                            "account_id": account_id,
                            "account_version": 1,
                            "debit_cents": amount,
                            "credit_cents": 0,
                        }
                    )
                    profit_cents += amount
                elif category == "expense" and balance > 0:
                    amount = balance
                    lines.append(
                        {
                            "account_id": account_id,
                            "account_version": 1,
                            "debit_cents": 0,
                            "credit_cents": amount,
                        }
                    )
                    profit_cents -= amount
            if not lines:
                raise AccountingError("没有可结转的损益余额", 409)
            if profit_cents > 0:
                lines.append(
                    {
                        "account_id": profit["account_id"],
                        "account_version": profit["version"],
                        "debit_cents": 0,
                        "credit_cents": profit_cents,
                    }
                )
            elif profit_cents < 0:
                lines.append(
                    {
                        "account_id": profit["account_id"],
                        "account_version": profit["version"],
                        "debit_cents": -profit_cents,
                        "credit_cents": 0,
                    }
                )
            # Closing lines must use the account version actually used by the balance.
            for line in lines:
                if line["account_id"] != profit["account_id"]:
                    row = conn.execute(
                        "SELECT current_version FROM ledger_accounts WHERE id=?",
                        (line["account_id"],),
                    ).fetchone()
                    line["account_version"] = row["current_version"]
            return self._insert_draft(
                conn,
                actor,
                book,
                kind="closing",
                run_key=key,
                asset_id=None,
                description="期末损益结转",
                lines=lines,
                fingerprint=_fingerprint(
                    {"book": book["id"], "period": book["period"], "lines": lines}
                ),
            )

    def _draft(self, conn, draft_id):
        row = conn.execute("SELECT * FROM periodic_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            raise AccountingError("期末草稿不存在", 404)
        return row

    def approve(self, actor, draft_id, request: DraftCommand):
        with self.db.transaction(write=True, actor=actor) as conn:
            draft = self._draft(conn, draft_id)
            book, _ = self._book(conn, actor, draft["book_id"], write=True)
            if draft["status"] != "draft":
                raise AccountingError("只有草稿状态可以复核", 409)
            if int(draft["version"]) != request.expected_version:
                raise AccountingError("期末草稿版本已变化", 409)
            if draft["created_by"] == actor.user_id:
                raise AccountingError("期末草稿编制人与复核人必须分离", 403)
            conn.execute(
                "UPDATE periodic_drafts SET status='approved',version=version+1,approved_by=?,"
                "approved_at=? WHERE id=?",
                (actor.user_id, now(), draft_id),
            )
            conn.execute(
                "UPDATE periodic_runs SET status='approved' WHERE id=?", (draft["run_id"],)
            )
            self._audit(conn, actor, book["id"], "periodic.approve", draft_id, {})
            return self._draft_view(conn, draft_id)

    def post(self, actor, draft_id, request: DraftPost):
        # One transaction covers the draft/run state, the formal voucher and the
        # command receipt. A crash cannot leave a posted voucher with an approved
        # draft, and a second business key cannot create a second voucher.
        with self.db.transaction(write=True, actor=actor) as conn:
            draft = self._draft(conn, draft_id)
            self._book(conn, actor, draft["book_id"], write=True)
            if draft["status"] == "posted":
                return self._draft_view(conn, draft_id, replayed=True)
            if draft["status"] != "approved":
                raise AccountingError("期末草稿尚未复核", 409)
            if int(draft["version"]) != request.expected_version:
                raise AccountingError("期末草稿版本已变化", 409)
            lines = conn.execute(
                "SELECT * FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no", (draft_id,)
            ).fetchall()
            manual = ManualVoucher(
                command_key=request.command_key,
                book_id=draft["book_id"],
                voucher_date=request.voucher_date,
                voucher_type="转",
                description=draft["description"],
                reason=request.reason,
                lines=[
                    ManualLine(
                        account_id=row["account_id"],
                        account_version=row["account_version"],
                        debit=cents_to_money(row["debit_cents"]),
                        credit=cents_to_money(row["credit_cents"]),
                        auxiliary_values=json.loads(row["aux_json"] or "{}"),
                        source_ref=row["source_ref"],
                    )
                    for row in lines
                ],
            )
            view = self.posting.post_manual_in_transaction(
                conn, actor, manual, periodic_review_id=draft_id
            )
            voucher_id = view["voucher"]["id"]
            changed = conn.execute(
                "UPDATE periodic_drafts SET status='posted',posted_voucher_id=? "
                "WHERE id=? AND status='approved'",
                (voucher_id, draft_id),
            )
            if changed.rowcount != 1:
                raise AccountingError("期末草稿状态已变化，未重复记账", 409)
            conn.execute(
                "UPDATE periodic_runs SET status='posted',voucher_id=? WHERE id=?",
                (voucher_id, draft["run_id"]),
            )
            self._audit(
                conn, actor, draft["book_id"], "periodic.post", draft_id, {"voucher_id": voucher_id}
            )
            return self._draft_view(conn, draft_id)

    def _draft_view(self, conn, draft_id, replayed=False):
        draft = self._draft(conn, draft_id)
        lines = conn.execute(
            "SELECT * FROM periodic_draft_lines WHERE draft_id=? ORDER BY line_no", (draft_id,)
        ).fetchall()
        return {"draft": dict(draft), "lines": [dict(row) for row in lines], "replayed": replayed}

    def list_drafts(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM periodic_drafts WHERE book_id=? ORDER BY created_at,id",
                (book["id"],),
            ).fetchall()
            return [dict(row) for row in rows]

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
