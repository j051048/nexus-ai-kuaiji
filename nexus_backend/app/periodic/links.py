"""FSC17: stable vehicle association for periodic assets.

The vehicle identity comes from transport master data; a plate/alias is never
promoted to the asset key. Links are additive evidence, not a second asset
ledger and not an authorization grant.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from pydantic import Field, StrictStr

from ..accounting.domain import AccountingError
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now


def _fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AssetVehicleLinkWrite(InputModel):
    vehicle_external_id: StrictStr = Field(min_length=1, max_length=120)


class AssetVehicleLinkRevoke(InputModel):
    reason: StrictStr = Field(min_length=3, max_length=500)


class AssetVehicleLinkService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _asset(self, conn, actor, asset_id: str, capability: str = "read"):
        asset = conn.execute("SELECT * FROM periodic_assets WHERE id=?", (asset_id,)).fetchone()
        if not asset:
            raise AccountingError("资产不存在或不可访问", 404)
        book = self.accounting.access.book(conn, actor, asset["book_id"], capability)
        return asset, book

    @staticmethod
    def _subject_fingerprint(row) -> str:
        return _fingerprint(
            {
                "id": row["id"],
                "external_id": row["external_id"],
                "display_name": row["display_name"],
                "version": int(row["version"]),
                "active": bool(row["active"]),
            }
        )

    def _subject(self, conn, book, vehicle_external_id: str):
        row = conn.execute(
            "SELECT * FROM transport_subjects WHERE firm_id=? AND company_id=? "
            "AND kind='vehicle' AND external_id=?",
            (book["firm_id"], book["company_id"], vehicle_external_id),
        ).fetchone()
        if not row:
            raise AccountingError("车辆不在本企业运输主数据中", 404)
        if not bool(row["active"]):
            raise AccountingError("车辆已停用，不能建立新关联", 409)
        return row

    def _current(self, conn, row):
        subject = conn.execute(
            "SELECT * FROM transport_subjects WHERE firm_id=? AND company_id=? "
            "AND kind='vehicle' AND external_id=?",
            (row["firm_id"], row["company_id"], row["vehicle_external_id"]),
        ).fetchone()
        if not subject or not bool(subject["active"]):
            return None, "车辆已停用或不在运输主数据中"
        if int(subject["version"]) != int(row["source_version"]):
            return subject, "车辆版本已变化，旧关联只作历史证据"
        if self._subject_fingerprint(subject) != row["source_fingerprint"]:
            return subject, "车辆名称或主数据指纹已变化，旧关联只作历史证据"
        return subject, None

    def _view(self, conn, row):
        asset = conn.execute(
            "SELECT id,name,kind,status,cost_cents FROM periodic_assets WHERE id=?",
            (row["asset_id"],),
        ).fetchone()
        subject, stale = self._current(conn, row)
        return {
            "id": row["id"],
            "asset_id": row["asset_id"],
            "asset_name": asset["name"] if asset else None,
            "asset_status": asset["status"] if asset else None,
            "vehicle_external_id": row["vehicle_external_id"],
            "vehicle_name": subject["display_name"] if subject else None,
            "source_kind": row["source_kind"],
            "source_version": int(row["source_version"]),
            "source_fingerprint": row["source_fingerprint"],
            "status": row["status"],
            "linked_by": row["linked_by"],
            "linked_at": row["linked_at"],
            "revoked_by": row["revoked_by"],
            "revoked_at": row["revoked_at"],
            "revoke_reason": row["revoke_reason"],
            "stale_reason": stale,
        }

    def link(self, actor, asset_id: str, request: AssetVehicleLinkWrite):
        vehicle = request.vehicle_external_id.strip()
        if not vehicle:
            raise AccountingError("车辆外部编号不能为空", 422)
        with self.db.transaction(actor=actor) as conn:
            _asset, prebook = self._asset(conn, actor, asset_id, "edit")
            company_id = prebook["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            asset, book = self._asset(conn, actor, asset_id, "edit")
            if asset["status"] != "active":
                raise AccountingError("已处置资产不能建立车辆关联", 409)
            subject = self._subject(conn, book, vehicle)
            existing = conn.execute(
                "SELECT * FROM periodic_asset_vehicle_links WHERE asset_id=? AND status='active'",
                (asset_id,),
            ).fetchone()
            if existing:
                if existing["vehicle_external_id"] == vehicle:
                    return self._view(conn, existing)
                raise AccountingError("资产已关联其他车辆，请先撤销原关联", 409)
            link_id = uuid4().hex
            stamp = now()
            conn.execute(
                "INSERT INTO periodic_asset_vehicle_links("
                "id,firm_id,company_id,book_id,asset_id,vehicle_external_id,source_kind,"
                "source_version,source_fingerprint,status,linked_by,linked_at) "
                "VALUES(?,?,?,?,?,?,'transport_subject',?,?,'active',?,?)",
                (
                    link_id,
                    book["firm_id"],
                    book["company_id"],
                    asset["book_id"],
                    asset_id,
                    vehicle,
                    int(subject["version"]),
                    self._subject_fingerprint(subject),
                    actor.user_id,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,company_id,user_id,action,object_id,"
                "details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    book["firm_id"],
                    asset["book_id"],
                    book["company_id"],
                    actor.user_id,
                    "periodic.asset.vehicle.link",
                    asset_id,
                    json.dumps(
                        {
                            "object_kind": "periodic_asset",
                            "vehicle_external_id": vehicle,
                            "source_version": int(subject["version"]),
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM periodic_asset_vehicle_links WHERE id=?", (link_id,)
            ).fetchone()
            return self._view(conn, row)

    def revoke(self, actor, asset_id: str, request: AssetVehicleLinkRevoke):
        with self.db.transaction(actor=actor) as conn:
            _asset, prebook = self._asset(conn, actor, asset_id, "edit")
            company_id = prebook["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            asset, book = self._asset(conn, actor, asset_id, "edit")
            row = conn.execute(
                "SELECT * FROM periodic_asset_vehicle_links WHERE asset_id=? AND status='active'",
                (asset_id,),
            ).fetchone()
            if not row:
                raise AccountingError("资产没有生效中的车辆关联", 404)
            stamp = now()
            conn.execute(
                "UPDATE periodic_asset_vehicle_links SET status='revoked',revoked_by=?,"
                "revoked_at=?,revoke_reason=? WHERE id=? AND status='active'",
                (actor.user_id, stamp, request.reason, row["id"]),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,company_id,user_id,action,object_id,"
                "details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    book["firm_id"],
                    asset["book_id"],
                    book["company_id"],
                    actor.user_id,
                    "periodic.asset.vehicle.revoke",
                    asset_id,
                    json.dumps(
                        {
                            "object_kind": "periodic_asset",
                            "reason": request.reason,
                            "vehicle_external_id": row["vehicle_external_id"],
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM periodic_asset_vehicle_links WHERE id=?", (row["id"],)
            ).fetchone()
            return self._view(conn, updated)

    def summary(self, actor, asset_id: str):
        with self.db.transaction(actor=actor) as conn:
            self._asset(conn, actor, asset_id)
            row = conn.execute(
                "SELECT * FROM periodic_asset_vehicle_links WHERE asset_id=? "
                "ORDER BY linked_at DESC LIMIT 1",
                (asset_id,),
            ).fetchone()
            return self._view(conn, row) if row else None

    def for_vehicle(self, actor, book_id: str, vehicle_external_id: str):
        with self.db.transaction(actor=actor) as conn:
            book = self.accounting.access.book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT l.* FROM periodic_asset_vehicle_links l "
                "JOIN periodic_assets a ON a.id=l.asset_id "
                "WHERE l.company_id=? AND l.vehicle_external_id=? AND l.status='active' "
                "AND a.status='active' ORDER BY a.name",
                (book["company_id"], vehicle_external_id),
            ).fetchall()
            return {"items": [self._view(conn, row) for row in rows]}
