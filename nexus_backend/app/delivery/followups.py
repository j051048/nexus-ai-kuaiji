"""Delivery configuration and reminder tasks for already-locked monthly periods.

Three axes stay separate on purpose:

* **event** — a frozen package exists for that book and period;
* **notification** — a task was queued and, at most, written to the local sink;
* **signature** — the customer's acknowledgement row.

Nothing here sends mail, SMS or WeChat messages: the only adapters are a
disabled channel and a local, auditable sink. Nothing here unlocks a period,
rewrites a package or moves an existing signature.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Protocol
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor
from ..accounting.invariants import validate_period
from ..accounting.service import now
from .followup_models import DeliveryGrantRequest, DeliveryRevokeRequest, DeliveryTaskRequest
from .notification_dispatch import NotificationDispatcher, assert_not_held, restore_epoch

KINDS = ("signature_reminder", "missing_contact_followup", "signature_overdue")
PENDING = ("pending", "failed")
TERMINAL = ("sink_written", "cancelled", "unknown")
MAX_ATTEMPTS = 5


class ChannelDisabled(RuntimeError):
    """The outbound channel is intentionally off in this environment."""


class DeliveryNotSent(RuntimeError):
    """Adapter certifies no request was sent; retry remains bounded and delayed.

    Do not use for read timeouts, connection loss after writes, or an ambiguous
    supplier response. Those outcomes must remain unknown.
    """


class DeliveryChannel(Protocol):
    name: str

    def deliver(self, payload: dict[str, Any]) -> str:  # pragma: no cover - protocol
        ...


class DisabledChannel:
    """Default adapter. Recording a task is allowed; sending is not."""

    name = "disabled"

    def deliver(self, payload: dict[str, Any]) -> str:
        raise ChannelDisabled("本环境未启用任何外发通道")


class LocalSinkChannel:
    """Writes into the same row set, so the evidence stays queryable and bounded."""

    name = "local_sink"

    def deliver(self, payload: dict[str, Any]) -> str:
        return "local-sink:" + str(payload["notification_id"])


def channel_from_env(value: str | None) -> DeliveryChannel:
    choice = (value or "").strip()
    if choice == "local_sink":
        return LocalSinkChannel()
    if choice == "smtp":
        from .smtp_channel import SmtpChannel

        return SmtpChannel(
            os.environ.get("DELIVERY_SMTP_HOST", "127.0.0.1"),
            int(os.environ.get("DELIVERY_SMTP_PORT", "1025")),
            os.environ.get("DELIVERY_SMTP_SENDER", "noreply@example.invalid"),
            os.environ.get("DELIVERY_SMTP_BASE_URL", "http://127.0.0.1:5173"),
        )
    if choice == "smtp_tls":
        from .smtp_channel import SecureSmtpChannel

        return SecureSmtpChannel(
            os.environ.get("DELIVERY_SMTP_HOST", ""),
            int(os.environ.get("DELIVERY_SMTP_PORT", "465")),
            os.environ.get("DELIVERY_SMTP_SENDER", ""),
            os.environ.get("DELIVERY_SMTP_BASE_URL", ""),
            username=os.environ.get("DELIVERY_SMTP_USERNAME", ""),
            password=os.environ.get("DELIVERY_SMTP_PASSWORD", ""),
        )
    return DisabledChannel()


class DeliveryFollowupService(NotificationDispatcher):
    def __init__(self, db, packages=None, plans=None):
        self.db, self.access = db, AccessControl()
        self.packages, self.plans = packages, plans

    # ------------------------------------------------------------------ reads
    @staticmethod
    def _period_row(conn, book_id: str, period: str):
        return conn.execute(
            "SELECT p.period,p.status,EXISTS(SELECT 1 FROM internal_period_locks l "
            "WHERE l.book_id=p.book_id AND l.period=p.period) AS locked "
            "FROM periods p WHERE p.book_id=? AND p.period=?",
            (book_id, period),
        ).fetchone()

    @staticmethod
    def _locked(row) -> bool:
        return bool(row and (row["status"] == "closed" or row["locked"]))

    @staticmethod
    def _grant(conn, book_id: str, period: str):
        return conn.execute(
            "SELECT * FROM monthly_delivery_grants WHERE book_id=? AND period=? AND state='active'",
            (book_id, period),
        ).fetchone()

    @staticmethod
    def _latest_grant(conn, book_id: str, period: str):
        return conn.execute(
            "SELECT * FROM monthly_delivery_grants WHERE book_id=? AND period=? "
            "ORDER BY version DESC LIMIT 1",
            (book_id, period),
        ).fetchone()

    @staticmethod
    def _package(conn, book_id: str, period: str):
        return conn.execute(
            "SELECT id,version,period,contact_user_id,manifest_sha256,created_at,created_by "
            "FROM monthly_delivery_packages WHERE book_id=? AND period=? "
            "ORDER BY version DESC LIMIT 1",
            (book_id, period),
        ).fetchone()

    @staticmethod
    def _ack(conn, package_id: str):
        return conn.execute(
            "SELECT created_by,created_at FROM monthly_delivery_acknowledgements WHERE package_id=?",
            (package_id,),
        ).fetchone()

    def _company_member(self, conn, company_id: str, book_id: str, user_id: str) -> bool:
        # A reviewer may manage this book without permission to list the
        # client's staff. Keep membership RLS private and ask only the bounded
        # current-actor boolean instead of treating a hidden row as a non-member.
        if self.db.is_postgres:
            return bool(
                conn.execute(
                    "SELECT accounting.delivery_recipient_member(?,?)", (book_id, user_id)
                ).fetchone()[0]
            )
        return bool(
            conn.execute(
                "SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id "
                "WHERE cm.company_id=? AND cm.user_id=? AND u.active=1",
                (company_id, user_id),
            ).fetchone()
        )

    def _book(self, conn, actor, book_id: str):
        return self.access.book(conn, actor, book_id, capability="review")

    def _recipient_eligible(self, conn, firm_id, company_id, book_id, package, recipient):
        """Notify only the person who can currently open and sign this exact package.

        A grant cannot repair an obsolete frozen contact or replace book access.
        Worker-side PG checks use the same contract in their controlled functions.
        """
        if (
            not recipient
            or package["contact_user_id"] != recipient
            or package["created_by"] == recipient
            or not self._company_member(conn, company_id, book_id, recipient)
        ):
            return False
        if self.db.is_postgres:
            return bool(
                conn.execute(
                    "SELECT accounting.monthly_delivery_contact_eligible(?,?)", (book_id, recipient)
                ).fetchone()[0]
            )
        plan = conn.execute(
            "SELECT contact_user_id FROM service_plan_versions WHERE book_id=? ORDER BY version DESC LIMIT 1",
            (book_id,),
        ).fetchone()
        if not plan or plan["contact_user_id"] != recipient:
            return False
        return bool(
            conn.execute(
                "SELECT 1 FROM monthly_delivery_eligible_contacts WHERE book_id=? AND user_id=?",
                (book_id, recipient),
            ).fetchone()
        )

    def _plan_contact(self, conn, book_id: str):
        """Current service-plan contact, which is what a *new* package would freeze."""
        if not self.plans:
            return None
        try:
            plan = self.plans._plan(conn, book_id)
        except AccountingError:
            return None
        return plan["contact_user_id"] if plan else None

    # ----------------------------------------------------------------- status
    def status(self, actor, book_id: str, period: str) -> dict:
        validate_period(period)
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            row = self._period_row(conn, book_id, period)
            if not row:
                raise AccountingError("该账套没有这个会计期间", 404)
            grant = self._grant(conn, book_id, period)
            package = self._package(conn, book_id, period)
            ack = self._ack(conn, package["id"]) if package else None
            planned = self._plan_contact(conn, book_id)
            tasks = conn.execute(
                "SELECT state,kind,COUNT(*) AS total FROM delivery_notifications "
                "WHERE book_id=? AND period=? GROUP BY state,kind",
                (book_id, period),
            ).fetchall()
            counts = {
                key: 0
                for key in (
                    "pending",
                    "claimed",
                    "sending",
                    "unknown",
                    "sink_written",
                    "failed",
                    "cancelled",
                )
            }
            for item in tasks:
                counts[item["state"]] = counts.get(item["state"], 0) + int(item["total"])
            manual_review = [
                dict(item)
                for item in conn.execute(
                    "SELECT id AS notification_id,package_id,package_version,manifest_sha256,"
                    "recipient_user_id,state,attempts,last_error,send_started_at,updated_at "
                    "FROM delivery_notifications WHERE book_id=? AND period=? AND state='unknown' "
                    "ORDER BY created_at,id LIMIT 100",
                    (book_id, period),
                ).fetchall()
            ]
            reasons = []
            locked = self._locked(row)
            if not locked:
                reasons.append("period_not_closed")
            if not package:
                reasons.append("package_missing")
            elif not ack:
                reasons.append("signature_missing")
            if counts["unknown"]:
                reasons.append("notification_unknown")
            target = grant["recipient_user_id"] if grant else planned
            recipient_valid = bool(target) and (
                self._recipient_eligible(
                    conn, actor.firm_id, book["company_id"], book_id, package, target
                )
                if package
                else self._company_member(conn, book["company_id"], book_id, target)
            )
            latest_grant = self._latest_grant(conn, book_id, period)
            if latest_grant and not grant:
                recipient_valid = False
            if not target:
                reasons.append("recipient_missing")
            elif not recipient_valid:
                reasons.append("recipient_invalid")
            return {
                "book_id": book_id,
                "period": period,
                "period_status": "closed" if locked else row["status"],
                "event": {
                    "state": "published" if package else "missing",
                    "package_id": package["id"] if package else None,
                    "package_version": package["version"] if package else None,
                    "package_contact_user_id": package["contact_user_id"] if package else None,
                },
                "recipient": {
                    "state": ("valid" if recipient_valid else "invalid" if target else "missing"),
                    "user_id": target,
                    "source": "grant" if grant else "service_plan" if target else None,
                    "grant_id": grant["id"] if grant else None,
                    "grant_version": grant["version"] if grant else None,
                },
                "notification": {**counts, "channel": "disabled", "manual_review": manual_review},
                "signature": {
                    "state": "signed" if ack else "awaiting",
                    "signed_by": ack["created_by"] if ack else None,
                    "signed_at": ack["created_at"] if ack else None,
                },
                "followup": {"needed": bool(reasons), "reasons": reasons},
            }

    @staticmethod
    def _next_step(reasons: list) -> tuple:
        """Who has to move next, derived from the first blocking reason."""
        for reason, step, owner in (
            ("notification_unknown", "reconcile_notification", "firm_manager"),
            ("period_not_closed", "close_period", "accountant"),
            ("package_missing", "publish_package", "accountant"),
            ("recipient_invalid", "fix_recipient", "firm_manager"),
            ("recipient_missing", "configure_recipient", "firm_manager"),
            ("signature_missing", "await_customer_signature", "customer"),
        ):
            if reason in reasons:
                return step, owner
        return "review", "accountant"

    def followups(self, actor, limit: int = 100) -> dict:
        """Bounded feed for the firm inbox: only periods that need a human step."""
        limit = max(1, min(int(limit), 200))
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            rows = conn.execute(
                "SELECT b.id AS book_id,b.company_id,b.name AS book_name,p.period,p.status,"
                "EXISTS(SELECT 1 FROM internal_period_locks l WHERE l.book_id=p.book_id "
                "AND l.period=p.period) AS locked "
                "FROM books b JOIN periods p ON p.book_id=b.id "
                "WHERE b.firm_id=? ORDER BY p.period DESC,b.name LIMIT ?",
                (actor.firm_id, limit * 4),
            ).fetchall()
            items = []
            for row in rows:
                try:
                    self._book(conn, actor, row["book_id"])
                except AccountingError:
                    continue
                package = self._package(conn, row["book_id"], row["period"])
                grant = self._grant(conn, row["book_id"], row["period"])
                ack = self._ack(conn, package["id"]) if package else None
                reasons = []
                if conn.execute(
                    "SELECT 1 FROM delivery_notifications WHERE book_id=? AND period=? "
                    "AND state='unknown' LIMIT 1",
                    (row["book_id"], row["period"]),
                ).fetchone():
                    reasons.append("notification_unknown")
                if not self._locked(row):
                    reasons.append("period_not_closed")
                if not package:
                    reasons.append("package_missing")
                elif not ack:
                    reasons.append("signature_missing")
                # Who signs only matters while a signature is still owed; once the
                # customer has signed, an unconfigured recipient is not an open item.
                target = (
                    grant["recipient_user_id"]
                    if grant
                    else self._plan_contact(conn, row["book_id"])
                )
                if package and not ack:
                    if not target:
                        reasons.append("recipient_missing")
                    elif (
                        not grant and self._latest_grant(conn, row["book_id"], row["period"])
                    ) or not self._recipient_eligible(
                        conn, actor.firm_id, row["company_id"], row["book_id"], package, target
                    ):
                        reasons.append("recipient_invalid")
                if reasons:
                    next_step, owner = self._next_step(reasons)
                    items.append(
                        {
                            "book_id": row["book_id"],
                            "book_name": row["book_name"],
                            "period": row["period"],
                            "reasons": reasons,
                            "next_step": next_step,
                            "owner": owner,
                            "package_id": package["id"] if package else None,
                            "recipient_user_id": target,
                        }
                    )
                if len(items) >= limit:
                    break
            return {"items": items, "count": len(items)}

    # --------------------------------------------------- notification states
    SEMANTICS = {
        "pending": "queued",
        "claimed": "sending",
        "sending": "sending",
        "sink_written": "accepted_unconfirmed",
        "failed": "failed",
        "unknown": "unknown",
        "cancelled": "cancelled",
    }
    NOTIFICATION_READERS = {"owner", "admin", "reviewer"}

    @classmethod
    def semantic(cls, state, opened, acknowledged):
        """Accepted by a server is not receipt; only a customer action is."""
        if acknowledged:
            return "signed"
        if opened:
            return "viewed"
        return cls.SEMANTICS.get(state, "unknown")

    def notifications(self, actor, book_id: str, period: str) -> dict:
        validate_period(period)
        with self.db.transaction(actor=actor) as conn:
            if self.access.firm_role(conn, actor) not in self.NOTIFICATION_READERS:
                raise AccountingError("仅事务所人员可查看提醒发送状态", 403)
            self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT n.id,n.kind,n.state,n.attempts,n.package_id,n.package_version,n.sink_ref,"
                "n.last_error,n.created_at,n.updated_at,n.dispatched_at,n.recipient_user_id,n.manifest_sha256,"
                "u.name AS recipient_name,a.created_at AS acknowledged_at,"
                "(SELECT MAX(o.opened_at) FROM monthly_delivery_opens o WHERE o.package_id=n.package_id) AS opened_at "
                "FROM delivery_notifications n LEFT JOIN users u ON u.id=n.recipient_user_id "
                "LEFT JOIN monthly_delivery_acknowledgements a ON a.package_id=n.package_id "
                "WHERE n.book_id=? AND n.period=? ORDER BY n.created_at DESC, n.id LIMIT 50",
                (book_id, period),
            ).fetchall()
            can_investigate = self.access.firm_role(conn, actor) in {"owner", "admin"}
            history = conn.execute(
                "SELECT * FROM notification_investigations WHERE book_id=? AND period=? ORDER BY revision",
                (book_id, period),
            ).fetchall()
            latest = {r["notification_id"]: dict(r) for r in history}
        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "state": row["state"],
                    "semantic": self.semantic(
                        row["state"], row["opened_at"], row["acknowledged_at"]
                    ),
                    "attempts": row["attempts"],
                    "recipient": {
                        "user_id": row["recipient_user_id"],
                        "name": row["recipient_name"],
                    },
                    "package": {
                        "id": row["package_id"],
                        "version": row["package_version"],
                        "manifest_sha256": row["manifest_sha256"],
                    },
                    "investigation": latest.get(row["id"]),
                    "can_investigate": can_investigate and row["state"] == "unknown",
                    "channel": "smtp"
                    if (row["sink_ref"] or "").startswith("smtp:")
                    else "local_sink",
                    "last_error": row["last_error"] or "",
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "dispatched_at": row["dispatched_at"] or "",
                    "opened_at": row["opened_at"] or "",
                    "acknowledged_at": row["acknowledged_at"] or "",
                }
            )
        return {"book_id": book_id, "period": period, "items": items}

    # -------------------------------------------------------------- commands
    def configure(self, actor, book_id: str, request: DeliveryGrantRequest) -> dict:
        validate_period(request.period)
        stamp = now()
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            if self.access.firm_role(conn, actor) not in {"owner", "admin", "reviewer"}:
                raise AccountingError("只有复核或管理角色可以配置交付收件人", 403)
            if not self._period_row(conn, book_id, request.period):
                raise AccountingError("该账套没有这个会计期间", 404)
            if not self._company_member(
                conn, book["company_id"], book_id, request.recipient_user_id
            ):
                raise AccountingError("收件人不是该客户企业的当前成员", 422)
            current = self._grant(conn, book_id, request.period)
            version = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM monthly_delivery_grants "
                    "WHERE book_id=? AND period=?",
                    (book_id, request.period),
                ).fetchone()[0]
            )
            if current:
                conn.execute(
                    "UPDATE monthly_delivery_grants SET state='revoked',revoked_by=?,revoked_at=?,"
                    "reason=reason||' / 被新版本取代' WHERE id=?",
                    (actor.user_id, stamp, current["id"]),
                )
            conn.execute(
                "INSERT INTO monthly_delivery_grants(id,firm_id,company_id,book_id,period,"
                "recipient_user_id,version,state,granted_by,granted_at,reason) "
                "VALUES(?,?,?,?,?,?,?,'active',?,?,?)",
                (
                    uuid4().hex,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    request.period,
                    request.recipient_user_id,
                    version,
                    actor.user_id,
                    stamp,
                    request.reason,
                ),
            )
            grant = self._grant(conn, book_id, request.period)
            # The previous recipient's queued tasks are not re-addressed silently.
            conn.execute(
                "UPDATE delivery_notifications SET state='cancelled',updated_at=?,"
                "last_error='收件人已变更' WHERE book_id=? AND period=? AND state IN('pending','failed','claimed') "
                "AND (grant_id IS NULL OR grant_id<>?)",
                (stamp, book_id, request.period, grant["id"]),
            )
            self._audit(
                conn,
                actor,
                "delivery.recipient.configure",
                {
                    "book_id": book_id,
                    "period": request.period,
                    "version": version,
                    "recipient_user_id": request.recipient_user_id,
                },
                stamp,
            )
            return {
                "grant_id": grant["id"],
                "version": version,
                "recipient_user_id": request.recipient_user_id,
                "previous_recipient_user_id": current["recipient_user_id"] if current else None,
            }

    def revoke(self, actor, book_id: str, period: str, request: DeliveryRevokeRequest) -> dict:
        validate_period(period)
        stamp = now()
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id)
            if self.access.firm_role(conn, actor) not in {"owner", "admin", "reviewer"}:
                raise AccountingError("只有复核或管理角色可以撤回交付收件人", 403)
            grant = self._grant(conn, book_id, period)
            if not grant:
                raise AccountingError("该期间没有生效中的交付收件人", 404)
            conn.execute(
                "UPDATE monthly_delivery_grants SET state='revoked',revoked_by=?,revoked_at=? WHERE id=?",
                (actor.user_id, stamp, grant["id"]),
            )
            cancelled = conn.execute(
                "UPDATE delivery_notifications SET state='cancelled',updated_at=?,"
                "last_error='交付收件人已撤回' WHERE book_id=? AND period=? "
                "AND state IN('pending','failed','claimed')",
                (stamp, book_id, period),
            ).rowcount
            self._audit(
                conn,
                actor,
                "delivery.recipient.revoke",
                {
                    "book_id": book_id,
                    "period": period,
                    "grant_id": grant["id"],
                    "cancelled_notifications": cancelled,
                },
                stamp,
            )
            return {"revoked": True, "cancelled_notifications": int(cancelled or 0)}

    def queue(self, actor, book_id: str, request: DeliveryTaskRequest) -> dict:
        validate_period(request.period)
        if request.kind not in KINDS:
            raise AccountingError("不支持的交付任务类型", 422)
        stamp = now()
        with self.db.transaction(write=True, actor=actor) as conn:
            assert_not_held(conn, self.db)
            book = self._book(conn, actor, book_id)
            if not self._period_row(conn, book_id, request.period):
                raise AccountingError("该账套没有这个会计期间", 404)
            package = self._package(conn, book_id, request.period)
            grant = self._grant(conn, book_id, request.period)
            latest = self._latest_grant(conn, book_id, request.period)
            epoch = restore_epoch(conn)
            recipient = None
            if request.kind in ("signature_reminder", "signature_overdue"):
                if latest and not grant:
                    raise AccountingError("交付授权已撤回，请重新配置收件人后再安排提醒", 409)
                if not package:
                    raise AccountingError("该期间还没有已冻结的交付包", 409)
                if self._ack(conn, package["id"]):
                    raise AccountingError("客户已签收，不再安排提醒", 409)
                recipient = package["contact_user_id"]
                if grant and grant["recipient_user_id"] != recipient:
                    raise AccountingError(
                        "当前交付收件人与该包版本冻结的联系人不一致；请重新生成交付版本，"
                        "或按冻结联系人重新配置交付收件人",
                        409,
                    )
                if not self._recipient_eligible(
                    conn, actor.firm_id, book["company_id"], book_id, package, recipient
                ):
                    raise AccountingError(
                        "收件人当前不能查看并签收该冻结包；请核对账套权限和服务单联系人，必要时重新生成交付版本",
                        409,
                    )
                # Retry never creates a second identity for an unknown/cancelled send.
                dedupe = f"{request.kind}:{package['id']}:v{package['version']}:g{grant['version'] if grant else 0}:r{epoch}"
            else:
                # This is an internal human task, not a message to an absent customer.
                package, grant = None, None
                dedupe = (
                    f"{request.kind}:{book_id}:{request.period}:{date.today().isoformat()}:r{epoch}"
                )
            existing = conn.execute(
                "SELECT id,state FROM delivery_notifications WHERE dedupe_key=?", (dedupe,)
            ).fetchone()
            if existing:
                return {
                    "notification_id": existing["id"],
                    "deduplicated": True,
                    "state": existing["state"],
                }
            if (
                package
                and conn.execute(
                    "SELECT 1 FROM delivery_notifications WHERE package_id=? AND recipient_user_id=? "
                    "AND state IN('unknown','claimed','sending') LIMIT 1",
                    (package["id"], recipient),
                ).fetchone()
            ):
                raise AccountingError(
                    "该交付包存在发送中或结果未知的提醒，请先人工核对；不得换类型或授权重发", 409
                )
            notification_id = uuid4().hex
            payload = {
                "notification_id": notification_id,
                "firm_id": actor.firm_id,
                "company_id": book["company_id"],
                "book_id": book_id,
                "period": request.period,
                "package_id": package["id"] if package else None,
                "package_version": package["version"] if package else None,
                "manifest_sha256": package["manifest_sha256"] if package else None,
                "recipient_user_id": recipient,
                "grant_id": grant["id"] if grant else None,
                "grant_version": grant["version"] if grant else 0,
                "restore_epoch": epoch,
                "kind": request.kind,
                "reason": request.reason,
            }
            conn.execute(
                "INSERT INTO delivery_notifications(id,firm_id,company_id,book_id,period,package_id,"
                "package_version,manifest_sha256,kind,recipient_user_id,grant_id,grant_version,restore_epoch,"
                "dedupe_key,state,payload_json,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                (
                    notification_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    request.period,
                    payload["package_id"],
                    payload["package_version"],
                    payload["manifest_sha256"],
                    request.kind,
                    recipient,
                    payload["grant_id"],
                    payload["grant_version"],
                    epoch,
                    dedupe,
                    json.dumps(payload, ensure_ascii=False),
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            self._audit(conn, actor, "delivery.notification.queue", payload, stamp)
            return {"notification_id": notification_id, "deduplicated": False, "state": "pending"}

    def dispatch(self, channel: DeliveryChannel | None = None, limit: int = 20) -> dict:
        """Claim, authorize send, call outside transactions, then fenced settlement.

        A crash after begin() is deliberately conservative: the expired sending
        row is unknown, even if the process died before the adapter wrote bytes.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise AccountingError("派发数量须为 1–100 的整数", 422)
        channel = channel or DisabledChannel()
        result = {
            "sent": 0,
            "failed": 0,
            "cancelled": 0,
            "blocked": 0,
            "unknown": 0,
            "channel": channel.name,
            "at": now(),
        }
        if isinstance(channel, DisabledChannel):
            result["blocked"] = min(limit, self.pending_count())
            return result
        worker_id = "delivery-" + uuid4().hex
        for _ in range(limit):
            claimed = self.claim(worker_id)
            if not claimed:
                break
            if claimed["state"] == "cancelled":
                result["cancelled"] += 1
                continue
            sending = self.begin(claimed["id"], claimed["lease_token"])
            if not sending:
                result["cancelled"] += 1
                continue
            outcome, reference, error = "unknown", "", ""
            envelope = json.loads(sending["payload_json"])
            try:
                envelope["recipient_email"] = sending["recipient_email"]
                reference = channel.deliver(envelope)
                if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
                    reference, error = "", "invalid_receipt"
                else:
                    outcome = "sink_written"
            except (DeliveryNotSent, ChannelDisabled) as failure:
                outcome, error = "failed", type(failure).__name__
            except Exception as failure:
                # An arbitrary transport exception says nothing about delivery.
                outcome, error = "unknown", type(failure).__name__
            accepted = self.finish(sending["id"], sending["lease_token"], outcome, reference, error)
            if not accepted:
                self.recover()
                result["unknown"] += 1
            else:
                result[
                    {"sink_written": "sent", "failed": "failed", "unknown": "unknown"}[outcome]
                ] += 1
        return result

    # ---------------------------------------------------------------- audit
    @staticmethod
    def _audit(conn, actor: Actor, action: str, details: dict, stamp: str) -> None:
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                details.get("book_id"),
                actor.user_id,
                action,
                details.get("notification_id")
                or details.get("package_id")
                or details.get("grant_id")
                or "",
                json.dumps(details, ensure_ascii=False),
                stamp,
            ),
        )
