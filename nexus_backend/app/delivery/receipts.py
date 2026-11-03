"""Signed provider receipts: evidence after acceptance, never a retry trigger.

The channel adapter only proves that the provider accepted a request. Delivery,
open and bounce events arrive later over a signed callback. Those events are
append-only; they never change ``delivery_notifications.state``, never queue a
new send and never create a customer signature. A customer signature exists
only in ``monthly_delivery_acknowledgements``.
"""

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import now
from .notification_dispatch import NotificationDispatcher

EVENT_TYPES = ("accepted", "delivered", "viewed", "bounced")
MAX_BODY = 65536
WINDOW_SECONDS = 300
HEADER_TIMESTAMP = "x-delivery-timestamp"
HEADER_SIGNATURE = "x-delivery-signature"
HEADER_EVENT_ID = "x-delivery-event-id"


def callback_secret():
    value = os.environ.get("DELIVERY_CALLBACK_SECRET", "")
    if len(value) < 32:
        raise AccountingError("交付回调签名密钥未配置或长度不足", 503)
    return value.encode("utf-8")


def sign(raw: bytes, timestamp: str, secret: bytes | None = None) -> str:
    """HMAC over ``timestamp.body``; the timestamp is inside the signed bytes."""
    key = secret if secret is not None else callback_secret()
    return hmac.new(key, timestamp.encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountingError("回调事件时间格式不正确", 422) from error
    if parsed.tzinfo is None:
        raise AccountingError("回调事件时间必须包含时区", 422)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON number")


class ProviderReceiptService(NotificationDispatcher):
    def __init__(self, db):
        self.db, self.access = db, AccessControl()

    # --------------------------------------------------------------- callback
    def receive(self, raw: bytes, timestamp: str, signature: str, event_id: str):
        if not isinstance(raw, (bytes, bytearray)) or not 1 <= len(raw) <= MAX_BODY:
            raise AccountingError("回调内容大小不合法", 413)
        raw = bytes(raw)
        try:
            stamp = int(str(timestamp).strip())
        except ValueError as error:
            raise AccountingError("回调时间戳不合法", 401) from error
        current = int(datetime.now(timezone.utc).timestamp())
        if abs(current - stamp) > WINDOW_SECONDS:
            raise AccountingError("回调已过期或时间偏差过大", 401)
        supplied = str(signature or "").strip()
        if supplied.startswith("sha256="):
            supplied = supplied[7:]
        if not re.fullmatch(r"[0-9a-f]{64}", supplied):
            raise AccountingError("回调签名无效", 401)
        expected = sign(raw, str(stamp))
        if not hmac.compare_digest(supplied, expected):
            raise AccountingError("回调签名无效", 401)
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise AccountingError("回调内容不是有效 JSON", 422) from None
        if not isinstance(payload, dict):
            raise AccountingError("回调内容必须是对象", 422)
        notification_id = str(payload.get("notification_id", "")).strip()
        provider_event_id = str(event_id or "").strip()
        if provider_event_id != str(payload.get("provider_event_id", "")).strip():
            raise AccountingError("回调事件编号与请求头不一致", 422)
        if not notification_id or len(notification_id) > 120:
            raise AccountingError("回调缺少通知编号", 422)
        if not provider_event_id or len(provider_event_id) > 200:
            raise AccountingError("回调缺少事件编号", 422)
        event_type = str(payload.get("event_type", "")).strip()
        if event_type not in EVENT_TYPES:
            raise AccountingError("回调事件类型不受支持", 422)
        occurred_at = _stamp(payload.get("occurred_at"))
        digest = hashlib.sha256(raw).hexdigest()
        received_at = now()
        with self.worker_transaction() as conn:
            source = conn.execute(
                "SELECT id,firm_id,company_id,book_id FROM delivery_notifications WHERE id=?",
                (notification_id,),
            ).fetchone()
            if source is None:
                raise AccountingError("通知不存在或无权访问", 404)
            prior = conn.execute(
                "SELECT * FROM delivery_provider_events WHERE provider_event_id=?",
                (provider_event_id,),
            ).fetchone()
            if prior is not None:
                if prior["payload_sha256"] != digest:
                    raise AccountingError("同一回调事件编号携带了不同内容", 409)
                return {"duplicate": True, "event": dict(prior)}
            row = {
                "id": uuid4().hex,
                "notification_id": source["id"],
                "firm_id": source["firm_id"],
                "company_id": source["company_id"],
                "book_id": source["book_id"],
                "provider_event_id": provider_event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "received_at": received_at,
                "payload_sha256": digest,
                "signature_timestamp": stamp,
                "raw_size": len(raw),
            }
            inserted = conn.execute(
                "INSERT INTO delivery_provider_events("
                + ",".join(row)
                + ") VALUES("
                + ",".join("?" for _ in row)
                + ") ON CONFLICT(provider_event_id) DO NOTHING RETURNING id",
                tuple(row.values()),
            ).fetchone()
            stored = conn.execute(
                "SELECT * FROM delivery_provider_events WHERE provider_event_id=?",
                (provider_event_id,),
            ).fetchone()
            if stored is None or stored["payload_sha256"] != digest:
                raise AccountingError("同一回调事件编号携带了不同内容", 409)
        return {"duplicate": inserted is None, "event": dict(stored)}

    # ------------------------------------------------------------------- read
    def timeline(self, actor, book_id, notification_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            source = conn.execute(
                "SELECT * FROM delivery_notifications WHERE id=? AND book_id=? AND firm_id=?",
                (notification_id, book_id, actor.firm_id),
            ).fetchone()
            if source is None:
                raise AccountingError("通知不存在或无权访问", 404)
            events = [
                dict(row)
                for row in conn.execute(
                    "SELECT event_type,occurred_at,received_at,provider_event_id,payload_sha256 "
                    "FROM delivery_provider_events WHERE notification_id=? "
                    "ORDER BY occurred_at,received_at",
                    (notification_id,),
                ).fetchall()
            ]
            signed = bool(
                source["package_id"]
                and conn.execute(
                    "SELECT 1 FROM monthly_delivery_acknowledgements WHERE package_id=?",
                    (source["package_id"],),
                ).fetchone()
            )
            return {
                "notification_id": notification_id,
                "state": source["state"],
                "events": events,
                "accepted": any(item["event_type"] == "accepted" for item in events),
                "delivered": any(item["event_type"] == "delivered" for item in events),
                "viewed": any(item["event_type"] == "viewed" for item in events),
                "bounced": any(item["event_type"] == "bounced" for item in events),
                "signed": signed,
            }
