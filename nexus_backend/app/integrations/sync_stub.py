"""H6 signed, local-only synchronization stand-in.

The adapter deliberately has no outbound HTTP client. It proves the local
contract that a real connector must satisfy: signed timestamped payloads,
monotonic cursors, idempotent delivery, late/duplicate isolation, and explicit
unknown/correction handling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from uuid import uuid4

from pydantic import Field, StrictInt, StrictStr, field_validator

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.models import InputModel

SECRET_ENV = "ACCOUNTING_SYNC_STUB_SECRET"
MAX_SKEW_SECONDS = 300
EVENT_STATUS = {"applied", "unknown", "correction", "cancelled"}


def _payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _signature(secret: str, timestamp: int, external_id: str, version: int, digest: str) -> str:
    message = f"{timestamp}.{external_id}.{version}.{digest}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class SyncEventWrite(InputModel):
    source_id: StrictStr = Field(min_length=1, max_length=80)
    external_id: StrictStr = Field(min_length=1, max_length=120)
    event_type: StrictStr = Field(min_length=3, max_length=40)
    version: StrictInt = Field(ge=1)
    occurred_at: StrictStr = Field(min_length=10, max_length=40)
    timestamp: StrictInt
    signature: StrictStr = Field(min_length=64, max_length=64)
    payload: dict = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def known(cls, value: str) -> str:
        if value not in EVENT_STATUS:
            raise ValueError("事件类型必须是 applied、unknown、correction 或 cancelled")
        return value


class SyncStubService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def _secret(self) -> str:
        secret = os.getenv(SECRET_ENV, "").strip()
        if len(secret) < 16:
            raise AccountingError("本地同步替身未配置签名密钥", 503)
        return secret

    def push(self, actor, book_id: str, request: SyncEventWrite):
        secret = self._secret()
        digest = _payload_hash(request.payload)
        now = int(time.time())
        if abs(now - int(request.timestamp)) > MAX_SKEW_SECONDS:
            raise AccountingError("同步事件时间戳超出允许窗口", 409)
        expected = _signature(
            secret, request.timestamp, request.external_id, request.version, digest
        )
        if not hmac.compare_digest(expected, request.signature):
            raise AccountingError("同步事件签名无效", 403)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            current = conn.execute(
                "SELECT * FROM sync_stub_events WHERE firm_id=? AND source_id=? AND external_id=? "
                "ORDER BY version DESC LIMIT 1",
                (actor.firm_id, request.source_id, request.external_id),
            ).fetchone()
            if current and int(current["version"]) == request.version:
                if (
                    current["payload_sha256"] != digest
                    or current["event_type"] != request.event_type
                ):
                    raise AccountingError("同一同步事件版本不能改变内容", 409)
                return self._view(current, duplicate=True)
            if current and int(current["version"]) > request.version:
                return {
                    "accepted": False,
                    "duplicate": False,
                    "reason": "stale_version",
                    "current_version": int(current["version"]),
                }
            cursor = conn.execute(
                "SELECT COALESCE(MAX(cursor),0)+1 AS next FROM sync_stub_events "
                "WHERE firm_id=? AND source_id=?",
                (actor.firm_id, request.source_id),
            ).fetchone()["next"]
            event_id = uuid4().hex
            conn.execute(
                "INSERT INTO sync_stub_events(id,firm_id,company_id,book_id,source_id,external_id,"
                "event_type,version,payload_json,payload_sha256,occurred_at,received_at,"
                "signature_verified,status,cursor) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,TRUE,?,?)",
                (
                    event_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    request.source_id,
                    request.external_id,
                    request.event_type,
                    request.version,
                    json.dumps(request.payload, ensure_ascii=False, sort_keys=True),
                    digest,
                    request.occurred_at,
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                    "applied" if request.event_type != "unknown" else "unknown",
                    cursor,
                ),
            )
            if current:
                conn.execute(
                    "UPDATE sync_stub_events SET status='superseded' WHERE id=?",
                    (current["id"],),
                )
            row = conn.execute("SELECT * FROM sync_stub_events WHERE id=?", (event_id,)).fetchone()
            return self._view(row, duplicate=False)

    def list_events(self, actor, book_id: str, *, source_id: str, after: int = 0, limit: int = 100):
        if after < 0 or not 1 <= limit <= 500:
            raise AccountingError("同步游标或分页参数无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT * FROM sync_stub_events WHERE firm_id=? AND source_id=? AND book_id=? "
                "AND cursor>? ORDER BY cursor LIMIT ?",
                (actor.firm_id, source_id, book_id, after, limit),
            ).fetchall()
            return {
                "items": [self._view(row, duplicate=False) for row in rows],
                "next_cursor": int(rows[-1]["cursor"]) if rows else after,
                "has_more": len(rows) == limit,
            }

    @staticmethod
    def _view(row, *, duplicate: bool):
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "external_id": row["external_id"],
            "event_type": row["event_type"],
            "version": int(row["version"]),
            "status": row["status"],
            "cursor": int(row["cursor"]),
            "payload_sha256": row["payload_sha256"],
            "accepted": not duplicate,
            "duplicate": duplicate,
            "external_posting": False,
        }
