"""OPT06-C: time-bounded review delegation.

A delegation is a separate authorization object, not a role grant: the delegate
gets read/review only for the named book, and only while the database clock is
inside the requested interval. It never grants edit, export or management.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pydantic import Field, StrictStr

from .access import AccessControl
from .domain import Actor, AccountingError
from .models import InputModel
from .service import now


class ReviewDelegationWrite(InputModel):
    to_user_id: StrictStr = Field(min_length=1, max_length=120)
    starts_at: StrictStr
    ends_at: StrictStr
    reason: StrictStr = Field(min_length=5, max_length=500)


class ReviewDelegationRevoke(InputModel):
    reason: StrictStr = Field(min_length=5, max_length=500)


def _parse(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingError("替补起止时间格式不正确", 422) from error
    if parsed.tzinfo is None:
        raise AccountingError("替补起止时间必须带时区", 422)
    return parsed.astimezone(timezone.utc)


class ReviewDelegationService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def _audit(self, conn, actor, book_id, action, object_id, details):
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

    def create(self, actor: Actor, book_id: str, request: ReviewDelegationWrite):
        starts, ends = _parse(request.starts_at), _parse(request.ends_at)
        current = datetime.now(timezone.utc)
        if ends <= starts:
            raise AccountingError("替补结束时间必须晚于开始时间", 422)
        if ends <= current:
            raise AccountingError("替补结束时间必须晚于当前时间", 422)
        if ends - starts > timedelta(days=31):
            raise AccountingError("替补有效期不能超过 31 天", 422)
        if request.to_user_id == actor.user_id:
            raise AccountingError("不能把复核职责替补给自己", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            target = conn.execute(
                "SELECT u.id,u.active,m.role AS firm_role,cm.role AS company_role "
                "FROM users u LEFT JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
                "LEFT JOIN company_memberships cm ON cm.company_id=? AND cm.user_id=u.id "
                "WHERE u.id=?",
                (actor.firm_id, book["company_id"], request.to_user_id),
            ).fetchone()
            if not target or not target["active"]:
                raise AccountingError("替补账号不存在或未激活", 422)
            if target["firm_role"] in {"owner", "admin"} or (
                target["firm_role"] is None and target["company_role"] in {"owner", "admin"}
            ):
                raise AccountingError("管理员不能作为复核替补", 422)
            if target["firm_role"] is None and target["company_role"] is None:
                raise AccountingError("替补账号不属于当前事务所或企业", 422)
            delegation_id = uuid4().hex
            try:
                conn.execute(
                    "INSERT INTO book_review_delegations("
                    "id,firm_id,company_id,book_id,duty,from_user_id,to_user_id,starts_at,ends_at,"
                    "status,reason,requested_by,created_at) VALUES(?,?,?,?,'reviewer',?,?,?,?,?,?,?,?)",
                    (
                        delegation_id,
                        actor.firm_id,
                        book["company_id"],
                        book_id,
                        actor.user_id,
                        request.to_user_id,
                        starts.isoformat(),
                        ends.isoformat(),
                        "pending",
                        request.reason,
                        actor.user_id,
                        now(),
                    ),
                )
            except Exception as error:
                if "UNIQUE" in str(error):
                    raise AccountingError("同一替补在该开始时间已有授权", 409) from error
                raise
            self._audit(
                conn,
                actor,
                book_id,
                "review.delegation.request",
                delegation_id,
                {
                    "to_user_id": request.to_user_id,
                    "starts_at": starts.isoformat(),
                    "ends_at": ends.isoformat(),
                    "reason": request.reason,
                },
            )
            return self._view(
                conn.execute(
                    "SELECT * FROM book_review_delegations WHERE id=?", (delegation_id,)
                ).fetchone()
            )

    def approve(self, actor: Actor, book_id: str, delegation_id: str):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM book_review_delegations WHERE id=? AND book_id=? AND firm_id=?",
                (delegation_id, book_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("替补授权不存在", 404)
            if row["requested_by"] == actor.user_id:
                raise AccountingError("发起人不能批准自己的替补授权", 403)
            if row["status"] != "pending":
                raise AccountingError("替补授权不在待批准状态", 409)
            if _parse(row["ends_at"]) <= datetime.now(timezone.utc):
                raise AccountingError("替补授权已过期", 409)
            conn.execute(
                "UPDATE book_review_delegations SET status='active',approved_by=?,approved_at=? WHERE id=?",
                (actor.user_id, now(), delegation_id),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "review.delegation.approve",
                delegation_id,
                {"requested_by": row["requested_by"], "to_user_id": row["to_user_id"]},
            )
            return self._view(
                conn.execute(
                    "SELECT * FROM book_review_delegations WHERE id=?", (delegation_id,)
                ).fetchone()
            )

    def revoke(
        self, actor: Actor, book_id: str, delegation_id: str, request: ReviewDelegationRevoke
    ):
        with self.db.transaction(write=True, actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM book_review_delegations WHERE id=? AND book_id=? AND firm_id=?",
                (delegation_id, book_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("替补授权不存在", 404)
            if row["requested_by"] != actor.user_id:
                self.access.require_manager(conn, actor)
            if row["status"] not in {"pending", "active"}:
                raise AccountingError("替补授权已结束，不能撤销", 409)
            conn.execute(
                "UPDATE book_review_delegations SET status='revoked',revoked_by=?,revoked_at=?,revoke_reason=? WHERE id=?",
                (actor.user_id, now(), request.reason, delegation_id),
            )
            self._audit(
                conn,
                actor,
                book_id,
                "review.delegation.revoke",
                delegation_id,
                {"reason": request.reason},
            )
            return self._view(
                conn.execute(
                    "SELECT * FROM book_review_delegations WHERE id=?", (delegation_id,)
                ).fetchone()
            )

    def expire_due(self, actor: Actor, *, at: str | None = None):
        """Mark due rows expired and leave one audit event per row."""
        stamp = at or now()
        with self.db.transaction(write=True, actor=actor) as conn:
            rows = conn.execute(
                "SELECT * FROM book_review_delegations WHERE status IN ('pending','active') AND ends_at<=?",
                (stamp,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE book_review_delegations SET status='expired' WHERE id=? AND status IN ('pending','active')",
                    (row["id"],),
                )
                self._audit(
                    conn,
                    actor,
                    row["book_id"],
                    "review.delegation.expire",
                    row["id"],
                    {"to_user_id": row["to_user_id"], "ends_at": row["ends_at"]},
                )
            return {"expired": len(rows)}

    def list(self, actor: Actor, book_id: str):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT * FROM book_review_delegations WHERE firm_id=? AND book_id=? ORDER BY created_at DESC",
                (actor.firm_id, book_id),
            ).fetchall()
            return {"items": [self._view(row) for row in rows]}

    @staticmethod
    def _view(row):
        return {
            key: row[key]
            for key in (
                "id",
                "book_id",
                "from_user_id",
                "to_user_id",
                "starts_at",
                "ends_at",
                "status",
                "reason",
                "requested_by",
                "approved_by",
                "approved_at",
                "revoked_by",
                "revoked_at",
                "revoke_reason",
            )
        }
