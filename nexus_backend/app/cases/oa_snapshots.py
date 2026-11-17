"""OA 审批快照：只登记已批准来源，不重放审批，也不直接生成补件。"""

import hashlib
import json
from datetime import date, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError, Actor
from app.accounting.service import now


class OALine(BaseModel):
    account_code: str = Field(min_length=1, max_length=32)
    amount_cents: int = Field(gt=0)
    description: str = Field(default="", max_length=200)


class OASnapshotWrite(BaseModel):
    oa_event_id: str = Field(min_length=1, max_length=120)
    oa_version: int = Field(gt=0)
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    title: str = Field(min_length=3, max_length=200)
    approved_by: str = Field(min_length=1, max_length=120)
    approved_at: str = Field(min_length=10, max_length=40)
    lines: list[OALine] = Field(min_length=1, max_length=200)


class OAMaterialRequestWrite(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=3, max_length=2000)


class OASnapshotService:
    def __init__(self, database):
        self.db = database
        self.access = AccessControl()

    @staticmethod
    def _canonical(row: OASnapshotWrite) -> str:
        return json.dumps(
            {
                "event": row.oa_event_id,
                "version": row.oa_version,
                "period": row.period,
                "title": row.title,
                "approved_by": row.approved_by,
                "approved_at": row.approved_at,
                "lines": [line.model_dump() for line in row.lines],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def register(self, actor: Actor, book_id: str, request: OASnapshotWrite) -> dict:
        canonical = self._canonical(request)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        codes = [line.account_code for line in request.lines]
        if len(codes) != len(set(codes)):
            raise AccountingError("同一 OA 快照中科目不能重复", 422)
        try:
            datetime.fromisoformat(request.approved_at.replace("Z", "+00:00"))
            date.fromisoformat(request.approved_at[:10])
        except ValueError as exc:
            raise AccountingError("OA 审批时间必须为 ISO 日期时间", 422) from exc
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, capability="manage")
            if request.period != book["period"]:
                raise AccountingError("OA 核定期间必须与账套期间一致", 409)
            existing = conn.execute(
                "SELECT * FROM oa_approval_snapshots WHERE firm_id=? AND company_id=? AND book_id=? AND oa_event_id=? AND oa_version=?",
                (
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    request.oa_event_id,
                    request.oa_version,
                ),
            ).fetchone()
            if existing:
                if existing["fingerprint"] == digest:
                    return self._view(existing)
                raise AccountingError("同一 OA 事件版本的内容不一致，必须新版本更正", 409)
            snapshot_id = uuid4().hex
            created = now()
            conn.execute(
                "INSERT INTO oa_approval_snapshots(id,firm_id,company_id,book_id,oa_event_id,oa_version,period,title,approved_by,approved_at,lines_json,source_sha256,fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    request.oa_event_id,
                    request.oa_version,
                    request.period,
                    request.title,
                    request.approved_by,
                    request.approved_at,
                    json.dumps([line.model_dump() for line in request.lines], ensure_ascii=False),
                    digest,
                    digest,
                    actor.user_id,
                    created,
                ),
            )
            row = conn.execute(
                "SELECT * FROM oa_approval_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
            return self._view(row)

    def list(self, actor: Actor, book_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, capability="read")
            rows = conn.execute(
                "SELECT * FROM oa_approval_snapshots WHERE book_id=? ORDER BY oa_event_id,oa_version DESC",
                (book_id,),
            ).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def create_material_request(
        self, actor: Actor, book_id: str, request: OAMaterialRequestWrite
    ) -> dict:
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, capability="manage")
            snapshot = conn.execute(
                "SELECT * FROM oa_approval_snapshots WHERE id=? AND book_id=?",
                (request.snapshot_id, book_id),
            ).fetchone()
            if not snapshot:
                raise AccountingError("OA 审批快照不属于当前账套", 404)
            existing = conn.execute(
                "SELECT * FROM oa_material_requests WHERE book_id=? AND snapshot_id=?",
                (book_id, request.snapshot_id),
            ).fetchone()
            if existing:
                return self._request_view(existing)
            request_id = uuid4().hex
            conn.execute(
                "INSERT INTO oa_material_requests(id,firm_id,company_id,book_id,snapshot_id,title,reason,status,version,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    snapshot["id"],
                    request.title,
                    request.reason,
                    "open",
                    1,
                    actor.user_id,
                    now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM oa_material_requests WHERE id=?", (request_id,)
            ).fetchone()
            return self._request_view(row)

    def list_material_requests(self, actor: Actor, book_id: str) -> dict:
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, capability="read")
            rows = conn.execute(
                "SELECT * FROM oa_material_requests WHERE book_id=? ORDER BY created_at,id",
                (book_id,),
            ).fetchall()
            return {"items": [self._request_view(row) for row in rows]}

    def list_accessible_requests(self, actor: Actor) -> dict:
        with self.db.transaction(actor=actor) as conn:
            rows = conn.execute(
                """
                SELECT r.*, b.company_id, c.name AS company_name, b.name AS book_name
                FROM oa_material_requests r
                JOIN books b ON b.id = r.book_id
                JOIN companies c ON c.id = b.company_id
                ORDER BY r.created_at, r.id
                """
            ).fetchall()
            allowed: dict[str, bool] = {}
            items = []
            for row in rows:
                company_id = row["company_id"]
                if company_id not in allowed:
                    try:
                        self.access.company(conn, actor, company_id)
                        allowed[company_id] = True
                    except AccountingError:
                        allowed[company_id] = False
                if allowed[company_id]:
                    item = self._request_view(row)
                    item.update(
                        {
                            "company_id": company_id,
                            "company_name": row["company_name"],
                            "book_id": row["book_id"],
                            "book_name": row["book_name"],
                        }
                    )
                    items.append(item)
            return {"items": items}

    @staticmethod
    def _request_view(row) -> dict:
        return {
            "id": row["id"],
            "snapshot_id": row["snapshot_id"],
            "title": row["title"],
            "reason": row["reason"],
            "status": row["status"],
            "version": row["version"],
        }

    @staticmethod
    def _view(row) -> dict:
        return {
            "id": row["id"],
            "oa_event_id": row["oa_event_id"],
            "oa_version": row["oa_version"],
            "period": row["period"],
            "title": row["title"],
            "approved_by": row["approved_by"],
            "approved_at": row["approved_at"],
            "lines": json.loads(row["lines_json"])
            if isinstance(row["lines_json"], str)
            else row["lines_json"],
            "source_sha256": row["source_sha256"],
            "fingerprint": row["fingerprint"],
        }
