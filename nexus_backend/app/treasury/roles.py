"""出纳与资金审批人的岗位授权。

付款岗位与账套读写权限是两条轴：给一个人 ``cashier`` 只让他能制单，不会让他能改
账；给 ``treasury_approver`` 只让他能放款，不会让他能复核凭证。授予与撤销都要求
事务所管理者或企业管理者身份，且撤销只改状态、不删记录——谁在什么时候拿过审批权
必须留痕（数据库侧另有禁删触发器兜底）。
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor
from ..accounting.database import Database

PAYMENT_ROLES = ("cashier", "treasury_approver")
ROLE_LABELS = {"cashier": "出纳", "treasury_approver": "资金审批人"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaymentRoleService:
    def __init__(self, db: Database, access: AccessControl | None = None):
        self.db = db
        self.access = access or AccessControl()

    def _validate_role(self, role: str) -> str:
        if role not in PAYMENT_ROLES:
            raise AccountingError(f"未知的付款岗位：{role}", 422)
        return role

    def list(self, actor: Actor, book_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT r.*,u.name AS user_name FROM book_payment_roles r "
                "JOIN users u ON u.id=r.user_id WHERE r.book_id=? "
                "ORDER BY r.role,r.user_id,r.granted_at",
                (book_id,),
            ).fetchall()
            can_grant = bool(book["permissions"].get("manage"))
        return {
            "book_id": book["id"],
            "roles": [
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "user_name": row["user_name"],
                    "role": row["role"],
                    "role_label": ROLE_LABELS.get(row["role"], row["role"]),
                    "state": row["state"],
                    "reason": row["reason"],
                    "granted_by": row["granted_by"],
                    "granted_at": row["granted_at"],
                    "revoked_by": row["revoked_by"],
                    "revoked_at": row["revoked_at"],
                }
                for row in rows
            ],
            "can_grant": can_grant,
            "roles_supported": [{"value": key, "label": ROLE_LABELS[key]} for key in PAYMENT_ROLES],
        }

    def grant(self, actor: Actor, book_id: str, user_id: str, *, role: str, reason: str) -> dict:
        role = self._validate_role(role)
        if not str(reason or "").strip():
            raise AccountingError("授予付款岗位必须写明理由", 422)
        company_id = self._company(actor, book_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            self._require_manager(conn, actor, book, company_id)
            target = conn.execute("SELECT id,active FROM users WHERE id=?", (user_id,)).fetchone()
            if not target or not int(target["active"]):
                raise AccountingError("被授权账号不存在或已停用", 404)
            # 岗位只加能力，不给账套访问权：被授权人必须先有一条账套授权，
            # 否则这条岗位记录既没有意义，也会让台账看起来像「授予了权限」。
            try:
                self.access.book(conn, Actor(user_id, actor.firm_id), book_id, "read")
            except AccountingError as denied:
                raise AccountingError(
                    "被授权人还没有这本账的访问授权，请先授予账套访问再加付款岗位",
                    409,
                ) from denied
            existing = conn.execute(
                "SELECT * FROM book_payment_roles WHERE book_id=? AND user_id=? AND role=?",
                (book_id, user_id, role),
            ).fetchone()
            if existing is not None and existing["state"] == "active":
                return self._one(conn, existing["id"], replayed=True)
            if existing is not None:
                # 同一 (账套, 人, 岗位) 只能有一条：撤销后再授权是「恢复同一条」，
                # 不是插入第二条，否则历史会出现两个互相矛盾的有效授权。
                conn.execute(
                    "UPDATE book_payment_roles SET state='active',reason=?,granted_by=?,"
                    "granted_at=?,revoked_by=NULL,revoked_at=NULL WHERE id=?",
                    (reason, actor.user_id, _now(), existing["id"]),
                )
                self._audit(conn, actor, book, "payment_role.grant", existing["id"], role, reason)
                return self._one(conn, existing["id"], replayed=False)
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO book_payment_roles(id,firm_id,company_id,book_id,user_id,role,state,"
                "reason,granted_by,granted_at) VALUES(?,?,?,?,?,?,'active',?,?,?)",
                (
                    identifier,
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    user_id,
                    role,
                    reason,
                    actor.user_id,
                    _now(),
                ),
            )
            self._audit(conn, actor, book, "payment_role.grant", identifier, role, reason)
            return self._one(conn, identifier, replayed=False)

    def revoke(self, actor: Actor, book_id: str, role_id: str, *, reason: str) -> dict:
        if not str(reason or "").strip():
            raise AccountingError("撤销付款岗位必须写明理由", 422)
        company_id = self._company(actor, book_id)
        with self.db.transaction(
            actor=actor, company_id=company_id, write=True, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            self._require_manager(conn, actor, book, company_id)
            row = conn.execute(
                "SELECT * FROM book_payment_roles WHERE id=? AND book_id=?", (role_id, book_id)
            ).fetchone()
            if row is None:
                raise AccountingError("付款岗位授权不存在或不属于当前账套", 404)
            if row["state"] != "active":
                return self._one(conn, role_id, replayed=True)
            conn.execute(
                "UPDATE book_payment_roles SET state='revoked',revoked_by=?,revoked_at=?,"
                "reason=? WHERE id=? AND state='active'",
                (actor.user_id, _now(), reason, role_id),
            )
            self._audit(conn, actor, book, "payment_role.revoke", role_id, row["role"], reason)
            return self._one(conn, role_id, replayed=False)

    # -- internals -------------------------------------------------------
    def _company(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            return self.access.book(conn, actor, book_id, "read")["company_id"]

    @staticmethod
    def _require_manager(conn, actor: Actor, book: dict, company_id: str) -> None:
        """只有事务所管理者或企业管理者能发/收付款岗位。"""
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM memberships WHERE firm_id=? AND user_id=? "
            "AND role IN ('owner','admin')) AS firm_manager,"
            "EXISTS(SELECT 1 FROM company_memberships WHERE company_id=? AND user_id=? "
            "AND role IN ('owner','admin')) AS company_manager",
            (book["firm_id"], actor.user_id, company_id, actor.user_id),
        ).fetchone()
        if not row or not (row["firm_manager"] or row["company_manager"]):
            raise AccountingError("需要事务所企业管理者或企业管理员权限才能授予或撤销付款岗位", 403)

    def _one(self, conn, role_id: str, *, replayed: bool) -> dict:
        row = conn.execute(
            "SELECT r.*,u.name AS user_name FROM book_payment_roles r "
            "JOIN users u ON u.id=r.user_id WHERE r.id=?",
            (role_id,),
        ).fetchone()
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "user_id": row["user_id"],
            "user_name": row["user_name"],
            "role": row["role"],
            "role_label": ROLE_LABELS.get(row["role"], row["role"]),
            "state": row["state"],
            "reason": row["reason"],
            "granted_by": row["granted_by"],
            "granted_at": row["granted_at"],
            "revoked_by": row["revoked_by"],
            "revoked_at": row["revoked_at"],
            "replayed": replayed,
        }

    @staticmethod
    def _audit(conn, actor: Actor, book: dict, action: str, object_id: str, role: str, reason: str):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book["id"],
                actor.user_id,
                action,
                object_id,
                json.dumps({"role": role, "reason": reason}, ensure_ascii=False),
                _now(),
            ),
        )
