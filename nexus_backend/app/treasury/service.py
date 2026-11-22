"""付款指令服务：把三权分立与状态机落在数据库写路径上。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.database import Database
from ..observability import metrics
from ..accounting.domain import AccountingError
from ..accounting.domain import Actor
from .payments import PaymentRuleError, advance, check_separation, needs_escalation, same_payment

DEFAULT_SINGLE_LIMIT_CENTS = 2_000_000
DEFAULT_DAILY_LIMIT_CENTS = 10_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentService:
    def __init__(self, db: Database, access: AccessControl | None = None):
        self.db = db
        self.access = access or AccessControl()

    # -- helpers ---------------------------------------------------------
    def _book(self, conn, actor: Actor, book_id: str, *, write: bool) -> dict:
        # 制单/提交/推进走付钱能力（出纳或账套编辑者），不是账套编辑权本身。
        return self.access.book(conn, actor, book_id, "pay" if write else "read")

    def _order(self, conn, actor: Actor, order_id: str) -> dict:
        row = conn.execute("SELECT * FROM payment_orders WHERE id=?", (order_id,)).fetchone()
        if row is None:
            raise AccountingError("付款指令不存在或无权访问", status=404)
        order = dict(row)
        # 授权走访问层（SQLite/PG 同一条路径），不依赖 PG 专用函数。
        self.access.book(conn, actor, order["book_id"], "read")
        return order

    def _daily_used(self, conn, book_id: str) -> int:
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS total FROM payment_orders "
            "WHERE book_id=? AND substr(created_at,1,10)=? AND status NOT IN ('draft','cancelled','rejected')",
            (book_id, today),
        ).fetchone()
        return int(row["total"] or 0)

    def _event(
        self, conn, order: dict, *, before: str, after: str, actor: Actor, reason: str
    ) -> None:
        conn.execute(
            "INSERT INTO payment_events(id,order_id,book_id,from_status,to_status,reason,"
            "actor_user_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                f"pe-{uuid4().hex[:12]}",
                order["id"],
                order["book_id"],
                before,
                after,
                reason,
                actor.user_id,
                _now(),
            ),
        )

    # -- commands --------------------------------------------------------
    def _company(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id, write=False)["company_id"]

    def _company_for_order(self, actor, order_id):
        with self.db.transaction(actor=actor) as conn:
            return self._order(conn, actor, order_id)["company_id"]

    def create(
        self,
        actor: Actor,
        book_id: str,
        *,
        payee: str,
        payee_account: str,
        amount_cents: int,
        idempotency_key: str,
        period: str,
        single_limit_cents: int = DEFAULT_SINGLE_LIMIT_CENTS,
        daily_limit_cents: int = DEFAULT_DAILY_LIMIT_CENTS,
    ) -> dict:
        if amount_cents <= 0:
            raise AccountingError("付款金额必须大于零", status=422)
        if not idempotency_key.strip():
            raise PaymentRuleError("付款指令必须有幂等键")
        company_id = self._company(actor, book_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, write=True)
            existing = conn.execute(
                "SELECT * FROM payment_orders WHERE book_id=? AND idempotency_key=?",
                (book_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if not same_payment(idempotency_key, dict(existing)):
                    raise AccountingError("幂等键已用于另一笔付款", status=409)
                return dict(existing)
            escalated = needs_escalation(
                amount_cents,
                single_limit_cents=single_limit_cents,
                daily_used_cents=self._daily_used(conn, book_id),
                daily_limit_cents=daily_limit_cents,
            )
            order_id = f"po-{uuid4().hex[:12]}"
            now = _now()
            conn.execute(
                "INSERT INTO payment_orders(id,firm_id,company_id,book_id,period,payee,payee_account,"
                "amount_cents,idempotency_key,status,maker_user_id,escalated,reason,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?)",
                (
                    order_id,
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    period,
                    payee,
                    payee_account,
                    amount_cents,
                    idempotency_key,
                    actor.user_id,
                    int(escalated),
                    "",
                    now,
                    now,
                ),
            )
            order = dict(
                conn.execute("SELECT * FROM payment_orders WHERE id=?", (order_id,)).fetchone()
            )
            self._event(conn, order, before="", after="draft", actor=actor, reason="创建付款指令")
            return order

    def submit(self, actor: Actor, order_id: str) -> dict:
        return self._move(actor, order_id, "pending_approval", "")

    def approve(self, actor: Actor, order_id: str, *, voucher_reviewer: str = "") -> dict:
        company_id = self._company_for_order(actor, order_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            order = self._order(conn, actor, order_id)
            # 放款是独立岗位：只有资金审批人（或已有复核权的人）能批，
            # 而「谁能批」与「谁制单」由同一张表的 maker/approver 分开保证。
            self.access.book(conn, actor, order["book_id"], "approve_payment")
            try:
                check_separation(order["maker_user_id"], actor.user_id, voucher_reviewer)
            except PaymentRuleError as error:
                raise AccountingError(str(error), status=403) from error
            return self._apply(conn, actor, order, "approved", "")

    def advance_external(
        self, actor: Actor, order_id: str, target: str, *, reason: str = ""
    ) -> dict:
        return self._move(actor, order_id, target, reason)

    # -- internals -------------------------------------------------------
    def _move(self, actor: Actor, order_id: str, target: str, reason: str) -> dict:
        company_id = self._company_for_order(actor, order_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            order = self._order(conn, actor, order_id)
            # 推进状态同样是付钱动作（提交、送银行、记退回），必须走付钱能力；
            # 只校验 read 会让被撤销的出纳继续推进别人的付款。
            self.access.book(conn, actor, order["book_id"], "pay")
            view = self._apply(conn, actor, order, target, reason)
        metrics.payment_state(target)
        return view

    def _apply(self, conn, actor: Actor, order: dict, target: str, reason: str) -> dict:
        try:
            advance(order["status"], target, reason=reason)
        except PaymentRuleError as error:
            raise AccountingError(str(error), status=409) from error
        sets = ["status=?", "updated_at=?"]
        params = [target, _now()]
        if target == "approved":
            sets.append("approver_user_id=?")
            params.append(actor.user_id)
        if reason:
            sets.append("reason=?")
            params.append(reason)
        params.append(order["id"])
        conn.execute(f"UPDATE payment_orders SET {', '.join(sets)} WHERE id=?", params)
        self._event(conn, order, before=order["status"], after=target, actor=actor, reason=reason)
        return dict(
            conn.execute("SELECT * FROM payment_orders WHERE id=?", (order["id"],)).fetchone()
        )

    def list_orders(self, actor: Actor, book_id: str, *, status: str | None = None) -> list[dict]:
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, write=False)
            sql = "SELECT * FROM payment_orders WHERE book_id=?"
            params: list = [book_id]
            if status:
                sql += " AND status=?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT 200"
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def desk(self, actor: Actor, book_id: str) -> dict:
        """出纳台口径：本账套的付款岗位、我能不能制单/审批、以及指令清单。

        单独一个只读投影，而不是把 roles 塞进 list_orders 的返回——列表的契约
        已经是数组，改成对象会让既有调用方静默读错。
        """
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            roles = sorted(self.access.payment_roles(conn, actor, book_id))
            orders = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM payment_orders WHERE book_id=? ORDER BY created_at DESC LIMIT 200",
                    (book_id,),
                ).fetchall()
            ]
        return {
            "book_id": book["id"],
            "period": book["period"],
            "roles": roles,
            "permissions": {
                "pay": bool(book["permissions"].get("pay")),
                "approve_payment": bool(book["permissions"].get("approve_payment")),
            },
            "orders": orders,
            "separation": "制单人与资金审批人必须是不同的人；出纳与资金审批人都不能代替独立复核。",
        }
