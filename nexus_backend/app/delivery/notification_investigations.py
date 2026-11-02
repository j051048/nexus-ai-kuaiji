"""Append-only human assertions about unknown transport; never a delivery receipt."""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import now
from .followup_models import Identifier, Reason
from .notification_dispatch import assert_not_held


class InvestigationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    expected_attempts: Annotated[int, Field(strict=True, ge=0)]
    expected_package_version: Annotated[int, Field(strict=True, gt=0)]
    expected_manifest_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    expected_recipient_user_id: Identifier
    conclusion: Literal["accepted_by_channel", "not_sent", "unresolved"]
    evidence_ref: Reason
    reason: Reason
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=120)]


class NotificationInvestigations:
    def __init__(self, db):
        self.db, self.access = db, AccessControl()

    def _source(self, conn, actor, book_id, notification_id):
        self.access.book(conn, actor, book_id, "read")
        if self.access.firm_role(conn, actor) not in {"owner", "admin", "reviewer"}:
            raise AccountingError("仅事务所人员可查看通知核查记录", 403)
        row = conn.execute(
            "SELECT * FROM delivery_notifications WHERE id=? AND book_id=? AND firm_id=?",
            (notification_id, book_id, actor.firm_id),
        ).fetchone()
        if row is None:
            raise AccountingError("通知不存在或无权访问", 404)
        return row

    def history(self, actor, book_id, notification_id):
        with self.db.transaction(actor=actor) as conn:
            self._source(conn, actor, book_id, notification_id)
            rows = conn.execute(
                "SELECT * FROM notification_investigations WHERE notification_id=? ORDER BY revision",
                (notification_id,),
            ).fetchall()
            return {"notification_id": notification_id, "items": [dict(row) for row in rows]}

    def create(self, actor, book_id, notification_id, request: InvestigationCreate):
        with self.db.transaction(actor=actor) as conn:
            source = self._source(conn, actor, book_id, notification_id)
            company_id = source["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            source = self._source(conn, actor, book_id, notification_id)
            self.access.require_manager(conn, actor)
            assert_not_held(conn, self.db)
            prior = conn.execute(
                "SELECT * FROM notification_investigations WHERE notification_id=? AND idempotency_key=?",
                (notification_id, request.idempotency_key),
            ).fetchone()
            expected = {
                "attempts": request.expected_attempts,
                "package_version": request.expected_package_version,
                "manifest_sha256": request.expected_manifest_sha256,
                "recipient_user_id": request.expected_recipient_user_id,
            }
            if any(source[key] != value for key, value in expected.items()):
                raise AccountingError("通知来源已变化，请刷新后重新核查", 409)
            if prior:
                if (
                    prior["created_by"] != actor.user_id
                    or prior["revision"] != request.expected_revision + 1
                    or any(
                        prior[key] != getattr(request, key)
                        for key in ("conclusion", "reason", "evidence_ref")
                    )
                ):
                    raise AccountingError("核查幂等键已用于不同请求", 409)
                return dict(prior)
            revision = conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM notification_investigations WHERE notification_id=?",
                (notification_id,),
            ).fetchone()[0]
            if source["state"] != "unknown" or request.expected_revision != revision:
                raise AccountingError("通知状态或核查版本已变化，请刷新后重新确认", 409)
            row = {
                "id": uuid4().hex,
                "notification_id": notification_id,
                "firm_id": actor.firm_id,
                "company_id": company_id,
                "book_id": book_id,
                "period": source["period"],
                "package_id": source["package_id"],
                **expected,
                "revision": revision + 1,
                "conclusion": request.conclusion,
                "evidence_ref": request.evidence_ref,
                "reason": request.reason,
                "idempotency_key": request.idempotency_key,
                "created_by": actor.user_id,
                "created_at": now(),
            }
            conn.execute(
                "INSERT INTO notification_investigations("
                + ",".join(row)
                + ") VALUES("
                + ",".join("?" for _ in row)
                + ")",
                tuple(row.values()),
            )
            return dict(
                conn.execute(
                    "SELECT * FROM notification_investigations WHERE id=?", (row["id"],)
                ).fetchone()
            )
