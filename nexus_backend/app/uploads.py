"""Bounded two-phase uploads for large originals (single file ≤ 10 MB).

The serverless gateway keeps its small request limits; the browser uploads a
large original straight to private storage under an authenticated, expiring
intent, then the backend verifies size and SHA-256 before any attachment row
may reference the stored bytes. The intent row is owned by one active actor.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from fastapi import APIRouter, Depends, Request

from .accounting.access import AccessControl
from .accounting.domain import AccountingError
from .accounting.service import now
from .business.storage import MAX_FILE_BYTES, validate_attachment

Purpose = Literal[
    "intake_attachment",
    "event_attachment",
    "case_attachment",
    "material_attachment",
    "bank_import",
    "invoice_import",
]
INTENT_TTL_SECONDS = 900
PURPOSES: tuple[str, ...] = (
    "intake_attachment",
    "event_attachment",
    "case_attachment",
    "material_attachment",
    "bank_import",
    "invoice_import",
)
Filename = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]


def normalize_filename(value: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", (value or "").replace("\\", "/").split("/")[-1]).strip()
    if not name or len(name) > 150:
        raise AccountingError("附件名称须为 1–150 个字符", 422)
    return name


class UploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Purpose
    filename: Filename
    media_type: str = Field(default="", max_length=120)
    size_bytes: int = Field(ge=1, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("filename")
    @classmethod
    def clean_name(cls, value):
        return normalize_filename(value)

    @field_validator("media_type")
    @classmethod
    def clean_type(cls, value):
        return (value or "").split(";", 1)[0].strip().lower()[:120]


class UploadIntentService:
    def __init__(self, db, store):
        self.db, self.store = db, store
        self.access = AccessControl()

    @staticmethod
    def _validator(purpose):
        if purpose == "material_attachment":
            from .service_plans.attachments import validate_material_original

            return validate_material_original
        if purpose == "invoice_import":
            from .invoices.parser import validate_invoice_export

            return validate_invoice_export
        if purpose == "bank_import":
            from .import_wizard.parser import validate_import_original

            return validate_import_original
        return validate_attachment

    def create(self, actor, request: UploadCreate):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
        upload_id = uuid4().hex
        storage_key = f"{upload_id[:2]}/{upload_id}"
        signed = None
        if hasattr(self.store.backend, "sign_upload"):
            signed = self.store.backend.sign_upload(storage_key, INTENT_TTL_SECONDS)
            url = signed["url"]
        else:
            url = f"/api/uploads/{upload_id}/content"
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=INTENT_TTL_SECONDS)
        ).isoformat()
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            conn.execute(
                "INSERT INTO upload_intents(id,firm_id,user_id,purpose,filename,media_type,size_bytes,"
                "sha256,storage_key,state,created_at,expires_at,consumed_ref) "
                "VALUES(?,?,?,?,?,?,?,?,?, 'pending',?,?,NULL)",
                (
                    upload_id,
                    actor.firm_id,
                    actor.user_id,
                    request.purpose,
                    request.filename,
                    request.media_type,
                    request.size_bytes,
                    request.sha256,
                    storage_key,
                    now(),
                    expires_at,
                ),
            )
        return {
            "id": upload_id,
            "method": "PUT",
            "url": url,
            "headers": {"Content-Type": request.media_type or "application/octet-stream"},
            "expires_at": expires_at,
            "max_bytes": MAX_FILE_BYTES,
        }

    def _owned(self, conn, actor, upload_id):
        if not isinstance(upload_id, str) or not re.fullmatch(r"[0-9a-f]{32}", upload_id):
            raise AccountingError("上传凭证不存在或已失效", 404)
        row = conn.execute(
            "SELECT * FROM upload_intents WHERE id=? AND firm_id=? AND user_id=?",
            (upload_id, actor.firm_id, actor.user_id),
        ).fetchone()
        if not row:
            raise AccountingError("上传凭证不存在或已失效", 404)
        return row

    @staticmethod
    def _expired(row):
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            return True
        return expires < datetime.now(timezone.utc)

    def _transfer_snapshot(self, actor, upload_id):
        """Short read: authorize and snapshot the intent, never hold it during IO."""
        with self.db.transaction(actor=actor) as conn:
            row = self._owned(conn, actor, upload_id)
            if row["state"] == "stored":
                return dict(row), True
            if row["state"] != "pending":
                raise AccountingError("该上传凭证已被使用或失效", 409)
            if self._expired(row):
                raise AccountingError("上传凭证已过期，请重新发起上传", 410)
            return dict(row), False

    def _commit_stored(self, actor, upload_id, name, detected=None):
        """Short write: re-authorize/version-check before publishing the object."""
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._owned(conn, actor, upload_id)
            if row["state"] == "stored":
                return {
                    "id": upload_id,
                    "state": "stored",
                    "filename": row["filename"],
                    **({"media_type": detected} if detected else {}),
                }
            if row["state"] != "pending":
                raise AccountingError("上传凭证状态已变化，请重新发起上传", 409)
            if self._expired(row):
                raise AccountingError("上传凭证已过期，请重新发起上传", 410)
            changed = conn.execute(
                "UPDATE upload_intents SET state='stored' WHERE id=? AND state='pending'",
                (upload_id,),
            )
            if changed.rowcount != 1:
                current = self._owned(conn, actor, upload_id)
                if current["state"] == "stored":
                    return {
                        "id": upload_id,
                        "state": "stored",
                        "filename": current["filename"],
                        **({"media_type": detected} if detected else {}),
                    }
                raise AccountingError("上传凭证状态已变化，请重新发起上传", 409)
            return {
                "id": upload_id,
                "state": "stored",
                "filename": name,
                **({"media_type": detected} if detected else {}),
            }

    def store_content(self, actor, upload_id, data):
        """Local single-request path: verify bytes outside the write lock, then commit."""
        if not isinstance(data, bytes):
            raise AccountingError("上传内容无效", 422)
        row, already = self._transfer_snapshot(actor, upload_id)
        if already:
            return {"id": upload_id, "state": "stored", "filename": row["filename"]}
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise AccountingError("上传内容与声明的大小或哈希不一致", 409)
        validator = self._validator(row["purpose"])
        name, detected = validator(row["filename"], row["media_type"], data)
        # Object IO may be slow/networked; it runs without a database write lock.
        self.store.backend.put(row["storage_key"], data, detected)
        return self._commit_stored(actor, upload_id, name, detected)

    def complete(self, actor, upload_id):
        """Verify a signed-URL object outside the write lock, then commit once."""
        row, already = self._transfer_snapshot(actor, upload_id)
        if already:
            return {"id": upload_id, "state": "stored", "filename": row["filename"]}
        try:
            data = self.store.backend.read(row["storage_key"], row["size_bytes"])
        except AccountingError:
            raise AccountingError("尚未收到上传内容，请先完成文件传输再校验", 409) from None
        mismatch = (
            len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]
        )
        if mismatch:
            self._mark_discarded(actor, upload_id, row["storage_key"])
            raise AccountingError("上传内容与声明的大小或哈希不一致，已丢弃该对象", 409)
        try:
            validator = self._validator(row["purpose"])
            name, detected = validator(row["filename"], row["media_type"], data)
        except AccountingError:
            self._mark_discarded(actor, upload_id, row["storage_key"])
            raise
        return self._commit_stored(actor, upload_id, name, detected)

    def take(self, actor, upload_id, purpose):
        """Claim a stored intent for the caller's attachment transaction."""
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._owned(conn, actor, upload_id)
            if row["purpose"] != purpose:
                raise AccountingError("上传凭证的用途与本次附件不一致", 409)
            if row["state"] == "consuming":
                raise AccountingError("该上传凭证正在被处理，请稍后核对结果", 409)
            if row["state"] != "stored":
                raise AccountingError("上传尚未完成校验，请先完成上传校验", 409)
            if self._expired(row):
                raise AccountingError("上传凭证已过期，请重新上传", 410)
            changed = conn.execute(
                "UPDATE upload_intents SET state='consuming' WHERE id=? AND state='stored'",
                (upload_id,),
            )
            if changed.rowcount != 1:
                raise AccountingError("该上传凭证正在被处理，请稍后核对结果", 409)
            return {
                "id": row["storage_key"].split("/")[1],
                "storage_key": row["storage_key"],
                "filename": row["filename"],
                "media_type": row["media_type"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
            }

    def peek(self, actor, upload_id, purpose):
        """Read a stored intent without claiming it, for deterministic previews."""
        with self.db.transaction(actor=actor) as conn:
            row = self._owned(conn, actor, upload_id)
            if row["purpose"] != purpose:
                raise AccountingError("上传凭证的用途与本次操作不一致", 409)
            if row["state"] == "consuming":
                raise AccountingError("该上传凭证正在被处理，请稍后核对结果", 409)
            if row["state"] != "stored":
                raise AccountingError("上传尚未完成校验，请先完成上传校验", 409)
            if self._expired(row):
                raise AccountingError("上传凭证已过期，请重新上传", 410)
            return {
                "id": row["storage_key"].split("/")[1],
                "storage_key": row["storage_key"],
                "filename": row["filename"],
                "media_type": row["media_type"],
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
            }

    def confirm(self, actor, upload_id, ref="attachment"):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            conn.execute(
                "UPDATE upload_intents SET state='consumed',consumed_ref=? "
                "WHERE id=? AND firm_id=? AND user_id=? AND state='consuming'",
                (ref, upload_id, actor.firm_id, actor.user_id),
            )

    def release(self, actor, upload_id):
        """Return a claimed intent to stored when the attachment write failed."""
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            conn.execute(
                "UPDATE upload_intents SET state='stored' WHERE id=? AND firm_id=? AND user_id=? "
                "AND state='consuming'",
                (upload_id, actor.firm_id, actor.user_id),
            )

    def _discard_object(self, row):
        try:
            self.store.backend.discard(row["storage_key"])
        except AccountingError:
            pass

    def _mark_discarded(self, actor, upload_id, storage_key):
        """Claim the discard before deleting bytes, so a concurrent winner is safe."""
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            changed = conn.execute(
                "UPDATE upload_intents SET state='discarded' WHERE id=? AND firm_id=? "
                "AND user_id=? AND state='pending'",
                (upload_id, actor.firm_id, actor.user_id),
            )
            claimed = changed.rowcount == 1
        if claimed:
            self._discard_object({"storage_key": storage_key})


def build_uploads_router(actor_dependency):
    router = APIRouter(prefix="/api/uploads", tags=["大文件上传"])

    def service(request: Request):
        return request.app.state.uploads

    @router.post("", status_code=201)
    def create(
        payload: UploadCreate,
        actor=Depends(actor_dependency),
        uploads=Depends(service),
    ):
        return uploads.create(actor, payload)

    @router.put("/{upload_id}/content")
    async def content(
        upload_id: str,
        request: Request,
        actor=Depends(actor_dependency),
        uploads=Depends(service),
    ):
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise AccountingError("单个文件最多 10 MB", 413)
            chunks.append(chunk)
        return uploads.store_content(actor, upload_id, b"".join(chunks))

    @router.post("/{upload_id}/complete")
    def complete(
        upload_id: str,
        actor=Depends(actor_dependency),
        uploads=Depends(service),
    ):
        return uploads.complete(actor, upload_id)

    return router
