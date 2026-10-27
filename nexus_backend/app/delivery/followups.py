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
from datetime import date, datetime
from typing import Any, Protocol
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError, Actor
from ..accounting.invariants import validate_period
from ..accounting.service import now
from ..jobs.restore_hold import restore_hold_active
from .followup_models import DeliveryGrantRequest, DeliveryRevokeRequest, DeliveryTaskRequest

KINDS = ("signature_reminder", "missing_contact_followup", "signature_overdue")
PENDING = ("pending", "failed")
TERMINAL = ("sink_written", "cancelled")
MAX_ATTEMPTS = 5


class ChannelDisabled(RuntimeError):
    """The outbound channel is intentionally off in this environment."""


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
    return LocalSinkChannel() if (value or "").strip() == "local_sink" else DisabledChannel()


class DeliveryFollowupService:
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
            "SELECT id,version,period,contact_user_id,manifest_sha256,created_at "
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

    @staticmethod
    def _company_member(conn, company_id: str, user_id: str) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id "
                "WHERE cm.company_id=? AND cm.user_id=? AND u.active=1",
                (company_id, user_id),
            ).fetchone()
        )

    def _book(self, conn, actor, book_id: str):
        return self.access.book(conn, actor, book_id, capability="review")

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
            counts = {"pending": 0, "sink_written": 0, "failed": 0, "cancelled": 0}
            for item in tasks:
                counts[item["state"]] = counts.get(item["state"], 0) + int(item["total"])
            reasons = []
            locked = self._locked(row)
            if not locked:
                reasons.append("period_not_closed")
            if not package:
                reasons.append("package_missing")
            elif not ack:
                reasons.append("signature_missing")
            target = grant["recipient_user_id"] if grant else planned
            if not target:
                reasons.append("recipient_missing")
            elif not self._company_member(conn, book["company_id"], target):
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
                    "state": (
                        "valid"
                        if target and self._company_member(conn, book["company_id"], target)
                        else "invalid"
                        if target
                        else "missing"
                    ),
                    "user_id": target,
                    "source": "grant" if grant else "service_plan" if target else None,
                    "grant_id": grant["id"] if grant else None,
                    "grant_version": grant["version"] if grant else None,
                },
                "notification": {**counts, "channel": "disabled"},
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
                package = self._package(conn, row["book_id"], row["period"])
                grant = self._grant(conn, row["book_id"], row["period"])
                ack = self._ack(conn, package["id"]) if package else None
                reasons = []
                if not self._locked(row):
                    reasons.append("period_not_closed")
                if not package:
                    reasons.append("package_missing")
                elif not ack:
                    reasons.append("signature_missing")
                # Who signs only matters while a signature is still owed; once the
                # customer has signed, an unconfigured recipient is not an open item.
                if package and not ack:
                    if not grant:
                        reasons.append("recipient_missing")
                    elif not self._company_member(
                        conn, row["company_id"], grant["recipient_user_id"]
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
                            "recipient_user_id": grant["recipient_user_id"] if grant else None,
                        }
                    )
                if len(items) >= limit:
                    break
            return {"items": items, "count": len(items)}

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
            if not self._company_member(conn, book["company_id"], request.recipient_user_id):
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
                "last_error='收件人已变更' WHERE book_id=? AND period=? AND state IN('pending','failed') "
                "AND grant_id<>?",
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
                "last_error='交付收件人已撤回' WHERE grant_id=? AND state IN('pending','failed')",
                (stamp, grant["id"]),
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
            book = self._book(conn, actor, book_id)
            if not self._period_row(conn, book_id, request.period):
                raise AccountingError("该账套没有这个会计期间", 404)
            package = self._package(conn, book_id, request.period)
            grant = self._grant(conn, book_id, request.period)
            latest = self._latest_grant(conn, book_id, request.period)
            ack = self._ack(conn, package["id"]) if package else None
            if request.kind in ("signature_reminder", "signature_overdue") and latest and not grant:
                raise AccountingError("交付授权已撤回，请重新配置收件人后再安排提醒", 409)
            if request.kind == "signature_reminder":
                if not package:
                    raise AccountingError("该期间还没有已冻结的交付包", 409)
                if ack:
                    raise AccountingError("客户已签收，不再安排提醒", 409)
                recipient = grant["recipient_user_id"] if grant else package["contact_user_id"]
                if not recipient or not self._company_member(conn, book["company_id"], recipient):
                    raise AccountingError("缺少有效收件人，请先配置交付收件人", 409)
                dedupe = (
                    f"signature:{package['id']}:v{package['version']}"
                    f":g{grant['version'] if grant else 0}"
                )
            else:
                dedupe = f"{request.kind}:{book_id}:{request.period}:{date.today().isoformat()}"
                recipient = grant["recipient_user_id"] if grant else None
            existing = conn.execute(
                "SELECT id,state FROM delivery_notifications WHERE dedupe_key=?", (dedupe,)
            ).fetchone()
            if existing and existing["state"] != "cancelled":
                return {
                    "notification_id": existing["id"],
                    "deduplicated": True,
                    "state": existing["state"],
                }
            payload = {
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": request.period,
                "package_id": package["id"] if package else None,
                "package_version": package["version"] if package else None,
                "kind": request.kind,
                "recipient_user_id": recipient,
                "reason": request.reason,
            }
            notification_id = uuid4().hex
            payload["notification_id"] = notification_id
            conn.execute(
                "INSERT INTO delivery_notifications(id,firm_id,company_id,book_id,period,package_id,"
                "kind,recipient_user_id,grant_id,dedupe_key,state,payload_json,created_by,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                (
                    notification_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    request.period,
                    package["id"] if package else None,
                    request.kind,
                    recipient,
                    grant["id"] if grant else None,
                    dedupe,
                    json.dumps(payload, ensure_ascii=False),
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            self._audit(conn, actor, "delivery.notification.queue", payload, stamp)
            return {"notification_id": notification_id, "deduplicated": False, "state": "pending"}

    # ------------------------------------------------------------- dispatch
    def dispatch(self, channel: DeliveryChannel | None = None, limit: int = 20) -> dict:
        """Worker-side. Leaves rows pending while the channel is disabled."""
        channel = channel or DisabledChannel()
        if restore_hold_active():
            raise AccountingError("恢复隔离期间禁止外发交付通知", 409)
        stamp = now()
        sent, failed, cancelled, skipped = 0, 0, 0, 0
        with self.db.transaction(write=True) as conn:
            rows = conn.execute(
                "SELECT * FROM delivery_notifications WHERE state IN('pending','failed') "
                "AND attempts<? ORDER BY created_at LIMIT ?",
                (MAX_ATTEMPTS, max(1, min(int(limit), 100))),
            ).fetchall()
            for row in rows:
                grant = (
                    conn.execute(
                        "SELECT * FROM monthly_delivery_grants WHERE id=?", (row["grant_id"],)
                    ).fetchone()
                    if row["grant_id"]
                    else None
                )
                package = conn.execute(
                    "SELECT id,contact_user_id FROM monthly_delivery_packages WHERE id=?",
                    (row["package_id"],),
                ).fetchone()
                stale = (
                    (row["grant_id"] and (not grant or grant["state"] != "active"))
                    or (
                        row["recipient_user_id"]
                        and not self._company_member(
                            conn, row["company_id"], row["recipient_user_id"]
                        )
                    )
                    or (
                        package
                        and conn.execute(
                            "SELECT 1 FROM monthly_delivery_acknowledgements WHERE package_id=?",
                            (package["id"],),
                        ).fetchone()
                    )
                )
                if stale:
                    conn.execute(
                        "UPDATE delivery_notifications SET state='cancelled',updated_at=?,"
                        "last_error='授权或状态已变化' WHERE id=?",
                        (stamp, row["id"]),
                    )
                    cancelled += 1
                    continue
                payload = json.loads(row["payload_json"])
                try:
                    reference = channel.deliver(payload)
                except ChannelDisabled as error:
                    conn.execute(
                        "UPDATE delivery_notifications SET attempts=attempts+1,updated_at=?,"
                        "last_error=? WHERE id=?",
                        (stamp, str(error), row["id"]),
                    )
                    skipped += 1
                    continue
                except Exception as error:  # bounded: one bad task must not stop the batch
                    conn.execute(
                        "UPDATE delivery_notifications SET state='failed',attempts=attempts+1,"
                        "updated_at=?,last_error=? WHERE id=?",
                        (stamp, type(error).__name__, row["id"]),
                    )
                    failed += 1
                    continue
                conn.execute(
                    "UPDATE delivery_notifications SET state='sink_written',attempts=attempts+1,"
                    "updated_at=?,dispatched_at=?,sink_ref=?,last_error='' WHERE id=?",
                    (stamp, stamp, reference, row["id"]),
                )
                sent += 1
        return {
            "sent": sent,
            "failed": failed,
            "cancelled": cancelled,
            "blocked": skipped,
            "channel": channel.name,
            "at": stamp,
        }

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
                details.get("package_id") or details.get("grant_id") or "",
                json.dumps(details, ensure_ascii=False),
                stamp,
            ),
        )
