"""Shared, non-executable import envelopes for industry domains."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = {"sql", "formula", "script", "command", "eval", "exec"}
ROW_STATUS = {"adopted", "excluded", "needs_review"}


def _digest(value) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _forbidden(value, path="") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().lower() in FORBIDDEN:
                raise AccountingError(f"导入契约不允许可执行配置：{path}{key}", 422)
            _forbidden(item, f"{path}{key}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _forbidden(item, f"{path}[{index}].")


class ImportEnvelopeWrite(BaseModel):
    source_system: str
    source_object: str
    external_id: str
    source_version: int
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    mapping_version: str
    rule_version: str
    unit: str
    currency: str
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ImportConfirm(BaseModel):
    expected_version: int


class FscImportService:
    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    def import_envelope(self, actor, book_id: str, request: ImportEnvelopeWrite):
        if not isinstance(request.source_version, int) or request.source_version < 1:
            raise AccountingError("来源版本必须为正整数", 422)
        for field_name in (
            "source_system",
            "source_object",
            "external_id",
            "filename",
            "media_type",
            "mapping_version",
            "rule_version",
            "unit",
            "currency",
        ):
            if not str(getattr(request, field_name) or "").strip():
                raise AccountingError(f"导入信封缺少 {field_name}", 422)
        if not HEX.fullmatch(str(request.sha256 or "").lower()):
            raise AccountingError("原件 SHA-256 格式无效", 422)
        if not isinstance(request.size_bytes, int) or request.size_bytes < 0:
            raise AccountingError("原件大小无效", 422)
        _forbidden(request.rows)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
        rows = self._validate_rows(request, book["period"])
        adopted = [row for row in rows if row["status"] == "adopted"]
        total = sum(row["amount_cents"] for row in adopted)
        evidence = {
            "book_id": book_id,
            "source": [
                request.source_system,
                request.source_object,
                request.external_id,
                request.source_version,
            ],
            "sha256": request.sha256.lower(),
            "mapping_version": request.mapping_version,
            "rule_version": request.rule_version,
            "unit": request.unit,
            "currency": request.currency,
            "rows": rows,
        }
        fingerprint = _digest(evidence)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            self.access.company(conn, actor, book["company_id"], manage=True)
            latest = conn.execute(
                "SELECT * FROM fsc_import_envelopes WHERE book_id=? AND source_system=? "
                "AND source_object=? AND external_id=? ORDER BY source_version DESC LIMIT 1",
                (book_id, request.source_system, request.source_object, request.external_id),
            ).fetchone()
            if latest and int(latest["source_version"]) == request.source_version:
                if (
                    latest["sha256"] == request.sha256.lower()
                    and latest["fingerprint"] == fingerprint
                ):
                    return self._view(latest, replayed=True)
                raise AccountingError("同一导入版本的内容或映射不一致，请提升版本号", 409)
            if latest and request.source_version < int(latest["source_version"]):
                raise AccountingError("导入信封版本已过期，请使用最新版本或提升版本号", 409)
            stamp = _now()
            if latest:
                conn.execute(
                    "UPDATE fsc_import_envelopes SET status='superseded',superseded_at=? WHERE id=?",
                    (stamp, latest["id"]),
                )
            envelope_id = uuid4().hex
            conn.execute(
                "INSERT INTO fsc_import_envelopes(id,firm_id,company_id,book_id,period,source_system,"
                "source_object,external_id,source_version,filename,media_type,sha256,size_bytes,"
                "mapping_version,rule_version,unit,currency,rows_json,row_count,adopted_count,total_cents,"
                "status,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    envelope_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    book["period"],
                    request.source_system,
                    request.source_object,
                    request.external_id,
                    request.source_version,
                    request.filename,
                    request.media_type,
                    request.sha256.lower(),
                    request.size_bytes,
                    request.mapping_version,
                    request.rule_version,
                    request.unit,
                    request.currency,
                    json.dumps(rows, ensure_ascii=False),
                    len(rows),
                    len(adopted),
                    total,
                    "draft",
                    fingerprint,
                    actor.user_id,
                    stamp,
                ),
            )
            self._event(conn, envelope_id, "imported", actor, {"version": request.source_version})
            row = conn.execute(
                "SELECT * FROM fsc_import_envelopes WHERE id=?", (envelope_id,)
            ).fetchone()
            return self._view(row)

    def _validate_rows(self, request: ImportEnvelopeWrite, period: str) -> list[dict[str, Any]]:
        if not isinstance(request.rows, list) or not request.rows:
            raise AccountingError("导入信封必须包含至少一行", 422)
        if len(request.rows) > 5000:
            raise AccountingError("单个导入信封最多 5000 行", 413)
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(request.rows, start=1):
            if not isinstance(item, dict):
                raise AccountingError(f"第 {index} 行结构无效", 422)
            external_id = str(item.get("external_id") or "").strip()
            if not external_id:
                raise AccountingError(f"第 {index} 行缺少外部行 ID", 422)
            if external_id in seen:
                raise AccountingError(f"外部行 ID 重复：{external_id}", 422)
            seen.add(external_id)
            occurred = str(item.get("date") or "").strip()
            if not DATE.fullmatch(occurred):
                raise AccountingError(f"第 {index} 行日期无效", 422)
            if occurred[:7] != period:
                raise AccountingError(f"第 {index} 行日期不在账套期间内", 422)
            amount = item.get("amount_cents")
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise AccountingError(f"第 {index} 行金额必须为整数分", 422)
            quantity = item.get("quantity_milli", 0)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                raise AccountingError(f"第 {index} 行数量无效", 422)
            status = str(item.get("status") or "adopted")
            if status not in ROW_STATUS:
                raise AccountingError(f"第 {index} 行状态无效", 422)
            reason = str(item.get("reason") or "").strip()
            if status in {"excluded", "needs_review"} and not reason:
                raise AccountingError(f"第 {index} 行必须说明排除或待核对原因", 422)
            row_unit = str(item.get("unit") or request.unit)
            row_currency = str(item.get("currency") or request.currency)
            if row_unit != request.unit or row_currency != request.currency:
                raise AccountingError(f"第 {index} 行单位或币种与信封不一致", 422)
            target_kind = str(item.get("target_kind") or "").strip()
            target_id = str(item.get("target_id") or "").strip()
            if bool(target_kind) != bool(target_id):
                raise AccountingError(f"第 {index} 行贡献目标必须同时提供类型和标识", 422)
            rows.append(
                {
                    "row_number": index,
                    "external_id": external_id,
                    "date": occurred,
                    "amount_cents": amount,
                    "quantity_milli": quantity,
                    "status": status,
                    "reason": reason,
                    "unit": row_unit,
                    "currency": row_currency,
                    "target_kind": target_kind,
                    "target_id": target_id,
                    "raw": item.get("raw") if isinstance(item.get("raw"), dict) else {},
                }
            )
        return rows

    def confirm(self, actor, envelope_id: str, expected_version: int):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._row(conn, actor, envelope_id)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            row = self._row(conn, actor, envelope_id)
            self.access.company(conn, actor, row["company_id"], manage=True)
            if int(row["source_version"]) != int(expected_version):
                raise AccountingError("导入信封版本已变化，请重新读取", 409)
            if row["status"] != "draft":
                raise AccountingError("只有草稿导入信封可以确认", 409)
            if any(item["status"] == "needs_review" for item in self._rows(row)):
                raise AccountingError("仍有待核对行，不能确认导入信封", 409)
            stamp = _now()
            conn.execute(
                "UPDATE fsc_import_envelopes SET status='confirmed',confirmed_by=?,confirmed_at=? WHERE id=?",
                (actor.user_id, stamp, envelope_id),
            )
            self._event(conn, envelope_id, "confirmed", actor, {"version": expected_version})
            return self._view(
                conn.execute(
                    "SELECT * FROM fsc_import_envelopes WHERE id=?", (envelope_id,)
                ).fetchone()
            )

    def list(self, actor, book_id: str, period: str | None = None):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            sql = (
                "SELECT * FROM fsc_import_envelopes WHERE book_id=? AND firm_id=? AND company_id=?"
            )
            params: list[Any] = [book_id, actor.firm_id, book["company_id"]]
            if period:
                sql += " AND period=?"
                params.append(period)
            sql += " ORDER BY source_system,source_object,source_version DESC"
            rows = conn.execute(sql, tuple(params)).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def recent(self, actor, limit: int = 20):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT e.*,co.name AS company_name,b.name AS book_name "
                "FROM fsc_import_envelopes e "
                "JOIN companies co ON co.id=e.company_id "
                "JOIN books b ON b.id=e.book_id "
                "WHERE e.firm_id=? ORDER BY e.created_at DESC,e.id DESC LIMIT ?",
                (actor.firm_id, max(1, min(int(limit), 50))),
            ).fetchall()
            return {
                "items": [
                    {
                        **self._view(row),
                        "company_name": row["company_name"],
                        "book_name": row["book_name"],
                    }
                    for row in rows
                ]
            }

    def detail(self, actor, envelope_id: str):
        with self.db.transaction(actor=actor) as conn:
            row = self._row(conn, actor, envelope_id)
            return {**self._view(row), "rows": self._rows(row)}

    def row_receipt(self, actor, envelope_id: str, row_number: int):
        with self.db.transaction(actor=actor) as conn:
            row = self._row(conn, actor, envelope_id)
            items = self._rows(row)
            item = next((entry for entry in items if entry["row_number"] == int(row_number)), None)
            if item is None:
                raise AccountingError("导入行不存在", 404)
            evidence = {
                "envelope_id": row["id"],
                "book_id": row["book_id"],
                "period": row["period"],
                "source_system": row["source_system"],
                "source_object": row["source_object"],
                "source_version": int(row["source_version"]),
                "sha256": row["sha256"],
                "mapping_version": row["mapping_version"],
                "rule_version": row["rule_version"],
                "row": item,
            }
            return {**evidence, "receipt_fingerprint": _digest(evidence)}

    def _event(self, conn, envelope_id: str, event: str, actor, detail: dict):
        conn.execute(
            "INSERT INTO fsc_import_envelope_events(id,envelope_id,event,actor_id,at,detail_json) VALUES(?,?,?,?,?,?)",
            (
                uuid4().hex,
                envelope_id,
                event,
                actor.user_id,
                _now(),
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _row(self, conn, actor, envelope_id: str):
        row = conn.execute(
            "SELECT * FROM fsc_import_envelopes WHERE id=? AND firm_id=?",
            (envelope_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("导入信封不存在", 404)
        self.access.book(conn, actor, row["book_id"], "read")
        return row

    @staticmethod
    def _rows(row) -> list[dict]:
        value = row["rows_json"]
        return value if isinstance(value, list) else json.loads(value)

    @staticmethod
    def _view(row, replayed: bool = False):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "company_id": row["company_id"],
            "period": row["period"],
            "source_system": row["source_system"],
            "source_object": row["source_object"],
            "external_id": row["external_id"],
            "source_version": int(row["source_version"]),
            "filename": row["filename"],
            "media_type": row["media_type"],
            "sha256": row["sha256"],
            "size_bytes": int(row["size_bytes"]),
            "mapping_version": row["mapping_version"],
            "rule_version": row["rule_version"],
            "unit": row["unit"],
            "currency": row["currency"],
            "row_count": int(row["row_count"]),
            "adopted_count": int(row["adopted_count"]),
            "total_cents": int(row["total_cents"]),
            "status": row["status"],
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "replayed": replayed,
        }
