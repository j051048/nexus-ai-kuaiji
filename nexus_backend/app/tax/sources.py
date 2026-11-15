"""Versioned tax source adapters: parse, normalize, and retain row evidence."""
from __future__ import annotations

from datetime import date, datetime, timezone
import base64
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from uuid import uuid4

from ..accounting.database import Database
from ..accounting.domain import AccountingError
from ..import_wizard.parser import parse_file
from ..ledgers.service import LedgersService

MAX_ROWS = 5000
MAX_BYTES = 10 * 1024 * 1024
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _digest(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _amount(text: str):
    value = text.strip().replace(",", "").replace("，", "")
    if value in {"", "-", "—"}:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("金额格式无效") from error
    if not number.is_finite() or abs(number) > Decimal('999999999999.99'):
        raise ValueError("金额必须为有限且在允许范围内的数值")
    if number.as_tuple().exponent < -2:
        raise ValueError("金额最多两位小数")
    return int(number * 100)


def _quantity(text: str):
    value = text.strip().replace(",", "")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("数量格式无效") from error
    if not number.is_finite() or number < 0 or number > Decimal('999999999.999') or number.as_tuple().exponent < -3:
        raise ValueError("数量必须为非负、最多三位小数")
    return int(number * 1000)


class TaxSourceAdapter:
    def __init__(self, db: Database):
        self.db = db
        self.ledgers = LedgersService(db)

    def import_source(self, actor, book_id, *, filename, data, source_system, external_id,
                      version, sheet_name, column_map, rule_version):
        if not data or len(data) > MAX_BYTES:
            raise AccountingError("文件不能为空，单个文件最多 10MB", 413)
        if not isinstance(version, int) or version < 1:
            raise AccountingError("版本号必须为正整数", 422)
        if not column_map.get("external_id") or not column_map.get("amount"):
            raise AccountingError("必须映射外部行 ID 与金额列", 422)
        parsed = parse_file(filename, data, sheet_name=sheet_name)
        headers = parsed.get("headers") or []
        index = {}
        for field, header in column_map.items():
            if not header:
                continue
            if header not in headers:
                if field in {"driver_external_id", "vehicle_external_id", "discount", "adjustment", "adjustment_reason"}:
                    continue
                raise AccountingError(f"找不到列：{header}", 422)
            index[field] = headers.index(header)
        file_hash = sha256(data).hexdigest()
        contract_hash = _digest({"sheet": parsed["sheet_name"], "columns": column_map,
                                 "rule": rule_version})
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            latest = conn.execute(
                "SELECT * FROM tax_source_files WHERE book_id=? AND source_system=? AND external_id=? ORDER BY version DESC LIMIT 1",
                (book_id, source_system, external_id),
            ).fetchone()
            if latest and latest["version"] == version:
                if (latest["sha256"] == file_hash
                        and latest["import_contract_sha256"] == contract_hash):
                    return self._view(latest, replayed=True)
                raise AccountingError("同一来源版本的文件内容不一致，请提升版本号", 409)
            if latest and version < latest["version"]:
                raise AccountingError("来源文件版本已过期，请使用最新版本或提升版本号", 409)
            rows = self._rows(conn, parsed, index, book, source_system, external_id, version)
            adopted = [row for row in rows if row["status"] == "adopted"]
            amount_total = sum(row["amount_cents"] for row in adopted)
            quantity_total = sum(row["quantity_milli"] for row in adopted)
            file_id = uuid4().hex
            fingerprint = _digest({"file": file_hash, "source": [source_system, external_id, version],
                                   "rows": rows, "rule": rule_version})
            if latest:
                stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
                conn.execute("UPDATE tax_source_files SET status='superseded',superseded_at=? WHERE id=?",
                             (stamp, latest["id"]))
                conn.execute("UPDATE tax_source_bindings SET invalidated_at=?,invalidated_reason='source_superseded' "
                             "WHERE source_file_id=? AND frozen=FALSE AND invalidated_at IS NULL", (stamp, latest["id"]))
            conn.execute(
                "INSERT INTO tax_source_files(id,book_id,firm_id,company_id,period,source_system,external_id,version,"
                "filename,sha256,size_bytes,sheet_name,rule_version,status,rows_json,amount_total_cents,"
                "quantity_milli,fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (file_id, book_id, actor.firm_id, book["company_id"], book["period"], source_system,
                 external_id, version, filename, file_hash, len(data), parsed["sheet_name"], rule_version,
                 "active", json.dumps(rows, ensure_ascii=False), amount_total, quantity_total,
                 fingerprint, actor.user_id, datetime.now(timezone.utc).isoformat(timespec='seconds')),
            )
            row = conn.execute("SELECT * FROM tax_source_files WHERE id=?", (file_id,)).fetchone()
            conn.execute(
                "UPDATE tax_source_files SET import_contract_sha256=?,original_content_base64=? WHERE id=?",
                (contract_hash, base64.b64encode(data).decode("ascii"), file_id),
            )
            self.ledgers._history(conn, actor, book, file_id, "tax.source.import", {
                "source_system": source_system, "external_id": external_id, "version": version,
                "sha256": file_hash, "rows": len(rows), "status": "active",
            })
            return self._view(row, replayed=False)

    def _rows(self, conn, parsed, index, book, source_system, external_id, version):
        output, seen = [], set()
        base = {"source_system": source_system, "source_external_id": external_id,
                "source_version": version, "sheet_name": parsed["sheet_name"]}
        headers = parsed["headers"]
        for offset, values in enumerate(parsed["rows"], start=2):
            cells = {field: (values[col] if col < len(values) else "").strip()
                     for field, col in index.items()}
            if not any(cells.values()):
                continue
            row_number = parsed.get("row_positions", [])[offset - 2] if offset - 2 < len(parsed.get("row_positions", [])) else offset
            row = {**base, "row_number": row_number, "raw": {headers[col]: values[col] for col in range(min(len(headers), len(values)))},
                   "status": "adopted", "reason": "", "external_id": cells.get("external_id", ""),
                   "date": cells.get("date", ""), "description": cells.get("description", ""),
                   "target_id": cells.get("target_id", ""), "amount_cents": 0, "quantity_milli": 0,
                   "normalized": cells}
            try:
                amount = _amount(cells.get("amount", ""))
            except ValueError as error:
                row.update(status="needs_review", reason=str(error))
                output.append(row)
                continue
            try:
                valid_date = date.fromisoformat(row["date"]).isoformat() == row["date"]
            except ValueError:
                valid_date = False
            if not row["external_id"]:
                row.update(status="needs_review", reason="缺外部行ID")
            elif not valid_date:
                row.update(status="needs_review", reason="缺少有效发生日期，不能推定为当前期间")
            elif amount is None:
                row.update(status="needs_review", reason="缺金额")
            elif amount == 0:
                row.update(status="excluded", reason="零金额行")
            elif row["date"] and (not DATE.fullmatch(row["date"]) or row["date"][:7] != book["period"]):
                row.update(status="excluded", reason="发生日期不在本账期")
            elif row["target_id"]:
                target = conn.execute("SELECT id FROM company_master_entities WHERE id=? AND firm_id=? AND company_id=?",
                                      (row["target_id"], book["firm_id"], book["company_id"])).fetchone()
                if not target:
                    row.update(status="needs_review", reason="挂接目标不存在或不属于本企业")
            key = (row["external_id"], row["date"], amount)
            if row["status"] == "adopted" and key in seen:
                row.update(status="needs_review", reason="文件内重复行")
            if row["status"] == "adopted":
                seen.add(key)
                try:
                    qty = _quantity(cells.get("quantity", ""))
                except ValueError as error:
                    row.update(status="needs_review", reason=str(error))
                else:
                    row["amount_cents"] = amount
                    row["quantity_milli"] = qty or 0
            output.append(row)
            if len(output) > MAX_ROWS:
                raise AccountingError("每次最多 5,000 行，请拆分文件", 413)
        return output

    def list_sources(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "read")
            rows = conn.execute("SELECT * FROM tax_source_files WHERE book_id=? AND firm_id=? ORDER BY created_at DESC",
                                (book_id, actor.firm_id)).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def download_original(self, actor, book_id, source_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            row = conn.execute("SELECT filename,sha256,size_bytes,original_content_base64 FROM tax_source_files WHERE id=? AND book_id=? AND firm_id=?",
                               (source_id, book_id, actor.firm_id)).fetchone()
            if not row:
                raise AccountingError("来源原件不存在或无权访问", 404)
            try:
                content = base64.b64decode(row["original_content_base64"], validate=True)
            except (ValueError, TypeError):
                content = b""
            if len(content) != row["size_bytes"] or sha256(content).hexdigest() != row["sha256"]:
                raise AccountingError("原件缺失或完整性校验失败，请联系资料管理员核对", 409)
            return row["filename"], content

    def _view(self, row, replayed=False):
        return {"id": row["id"], "book_id": row["book_id"], "period": row["period"],
                "source_system": row["source_system"], "external_id": row["external_id"],
                "version": row["version"], "filename": row["filename"], "sha256": row["sha256"],
                "size_bytes": row["size_bytes"], "sheet_name": row["sheet_name"],
                "rule_version": row["rule_version"], "status": row["status"],
                "rows": json.loads(row["rows_json"]), "amount_total_cents": row["amount_total_cents"],
                "quantity_milli": row["quantity_milli"], "fingerprint": row["fingerprint"],
                "replayed": replayed, "real_external_system": False}


    def import_batch(self, actor, book_id, entries):
        results = []
        for entry in entries:
            try:
                saved = self.import_source(
                    actor, book_id, filename=entry["filename"], data=entry["data"],
                    source_system=entry["source_system"], external_id=entry["external_id"],
                    version=entry["version"], sheet_name=entry["sheet_name"],
                    column_map=entry["column_map"], rule_version=entry["rule_version"],
                )
                results.append({"status": "imported", "external_id": entry["external_id"],
                                "source_id": saved["id"], "rows": len(saved["rows"])})
            except AccountingError as error:
                results.append({"status": "failed", "external_id": entry["external_id"],
                                "http_status": error.status, "error": str(error),
                                "retryable": error.status in {409, 422}})
        return {
            "items": results,
            "imported": sum(1 for item in results if item["status"] == "imported"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
        }

    def bind_output(self, actor, book_id, source_file_id, *, output_kind, output_id,
                    output_version, frozen=False):
        if output_kind not in {"workpaper", "preview", "approval"}:
            raise AccountingError("输出类型无效", 422)
        if not isinstance(output_version, int) or output_version < 1:
            raise AccountingError("输出版本无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            source = conn.execute(
                "SELECT * FROM tax_source_files WHERE id=? AND book_id=? AND firm_id=?",
                (source_file_id, book_id, actor.firm_id),
            ).fetchone()
            if not source:
                raise AccountingError("来源文件不存在或不属于当前账套", 404)
            if source["status"] != "active":
                raise AccountingError("来源文件已过期，不能绑定新输出", 409)
            existing = conn.execute(
                "SELECT * FROM tax_source_bindings WHERE book_id=? AND output_kind=? AND output_id=? "
                "AND output_version=? AND source_file_id=?",
                (book_id, output_kind, output_id, output_version, source_file_id),
            ).fetchone()
            if existing:
                if existing["frozen"] == bool(frozen):
                    return self._binding(existing)
                raise AccountingError("该来源已绑定该输出版本，冻结状态不同需生成新版本", 409)
            binding_id = uuid4().hex
            conn.execute(
                "INSERT INTO tax_source_bindings(id,firm_id,company_id,book_id,period,source_file_id,source_system,"
                "external_id,source_fingerprint,output_kind,output_id,output_version,frozen,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (binding_id, actor.firm_id, book["company_id"], book_id, book["period"], source_file_id,
                 source["source_system"], source["external_id"], source["fingerprint"], output_kind,
                 output_id, output_version, bool(frozen), actor.user_id,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            row = conn.execute("SELECT * FROM tax_source_bindings WHERE id=?", (binding_id,)).fetchone()
            return self._binding(row)

    def validate_output(self, actor, book_id, *, output_kind, output_id, output_version):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT * FROM tax_source_bindings WHERE book_id=? AND output_kind=? AND output_id=? "
                "AND output_version=? ORDER BY created_at,id",
                (book_id, output_kind, output_id, output_version),
            ).fetchall()
            if not rows:
                raise AccountingError("该输出版本没有来源绑定", 404)
            checked = []
            for row in rows:
                if row["invalidated_at"]:
                    raise AccountingError("来源已升级，未冻结输出需要重新生成", 409)
                current = conn.execute(
                    "SELECT * FROM tax_source_files WHERE book_id=? AND source_system=? AND external_id=? AND status='active' "
                    "ORDER BY version DESC LIMIT 1",
                    (book_id, row["source_system"], row["external_id"]),
                ).fetchone()
                historical = False
                if current and current["fingerprint"] != row["source_fingerprint"]:
                    if row["frozen"]:
                        historical = True
                    else:
                        conn.execute("UPDATE tax_source_bindings SET invalidated_at=?,invalidated_reason='source_changed' WHERE id=?",
                                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), row["id"]))
                        raise AccountingError("来源内容已变化，未冻结输出需要重新生成", 409)
                checked.append({**self._binding(row), "valid": True, "historical": historical,
                                "current_fingerprint": current["fingerprint"] if current else row["source_fingerprint"]})
            first = checked[0]
            return {**first, "sources": checked,
                    "historical": all(item["historical"] for item in checked)}
    def record_contributions(self, actor, book_id, source_file_id, *, output_kind, output_id,
                             output_version, contributions):
        if output_kind not in {"workpaper", "preview", "approval"}:
            raise AccountingError("输出类型无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            source = conn.execute(
                "SELECT * FROM tax_source_files WHERE id=? AND book_id=? AND firm_id=?",
                (source_file_id, book_id, actor.firm_id),
            ).fetchone()
            if not source:
                raise AccountingError("来源文件不存在或不属于当前账套", 404)
            if source["status"] != "active":
                raise AccountingError("来源文件已过期，不能登记贡献", 409)
            binding = conn.execute(
                "SELECT * FROM tax_source_bindings WHERE source_file_id=? AND output_kind=? "
                "AND output_id=? AND output_version=? AND invalidated_at IS NULL",
                (source_file_id, output_kind, output_id, output_version),
            ).fetchone()
            if not binding:
                raise AccountingError("输出未绑定该来源，不能登记贡献", 409)
            view = self._view(source)
            checked = validate_contributions(view, contributions)
            for item in contributions:
                row = next(row for row in view["rows"] if row["row_number"] == item["row_number"])
                lines = item.get("lines") or []
                if lines:
                    _validate_contribution_lines(lines, row)
            conn.execute(
                "DELETE FROM tax_source_contributions WHERE source_file_id=? AND output_kind=? "
                "AND output_id=? AND output_version=?",
                (source_file_id, output_kind, output_id, output_version),
            )
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for item in contributions:
                row = next(row for row in view["rows"] if row["row_number"] == item["row_number"])
                contribution_id = uuid4().hex
                conn.execute(
                    "INSERT INTO tax_source_contributions(id,firm_id,company_id,book_id,source_file_id,"
                    "row_number,output_kind,output_id,output_version,amount_cents,quantity_milli,"
                    "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (contribution_id, actor.firm_id, book["company_id"], book_id, source_file_id,
                     item["row_number"], output_kind, output_id, output_version, row["amount_cents"],
                     row["quantity_milli"], actor.user_id, stamp),
                )
                for line in item.get("lines") or []:
                    conn.execute(
                        "INSERT INTO tax_source_contribution_lines(id,contribution_id,output_line_id,"
                        "amount_cents,quantity_milli) VALUES(?,?,?,?,?)",
                        (uuid4().hex, contribution_id, line["output_line_id"],
                         int(line["amount_cents"]), int(line.get("quantity_milli", 0))),
                    )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (actor.firm_id, book_id, actor.user_id, "tax.source.contributions", source_file_id,
                 json.dumps({"output_kind": output_kind, "output_id": output_id, **checked}), stamp),
            )
            return checked

    def list_contributions(self, actor, book_id, *, output_kind, output_id, output_version=None):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            sql = ("SELECT id,source_file_id,row_number,output_version,amount_cents,quantity_milli "
                   "FROM tax_source_contributions WHERE book_id=? AND output_kind=? AND output_id=?")
            params = [book_id, output_kind, output_id]
            if output_version is not None:
                sql += " AND output_version=?"
                params.append(output_version)
            rows = conn.execute(sql + " ORDER BY output_version,row_number", tuple(params)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["lines"] = [dict(line) for line in conn.execute(
                    "SELECT output_line_id,amount_cents,quantity_milli FROM tax_source_contribution_lines "
                    "WHERE contribution_id=? ORDER BY output_line_id",
                    (row["id"],),
                ).fetchall()]
                result.append(item)
            return result

    def aggregate_contributions(self, actor, book_id, *, output_kind, output_id, output_version):
        rows = self.list_contributions(
            actor, book_id, output_kind=output_kind, output_id=output_id, output_version=output_version,
        )
        if not rows:
            raise AccountingError("该输出版本没有来源贡献", 404)
        lines = {}
        for row in rows:
            for line in row["lines"]:
                item = lines.setdefault(line["output_line_id"], {"output_line_id": line["output_line_id"],
                                                                   "amount_cents": 0, "quantity_milli": 0})
                item["amount_cents"] += line["amount_cents"]
                item["quantity_milli"] += line["quantity_milli"]
        return {
            "output_kind": output_kind, "output_id": output_id, "output_version": output_version,
            "source_count": len({row["source_file_id"] for row in rows}),
            "row_count": len(rows),
            "amount_total_cents": sum(row["amount_cents"] for row in rows),
            "quantity_milli": sum(row["quantity_milli"] for row in rows),
            "lines": [lines[key] for key in sorted(lines)],
        }

    def _binding(self, row):
        return {"id": row["id"], "book_id": row["book_id"], "source_file_id": row["source_file_id"],
                "source_fingerprint": row["source_fingerprint"], "output_kind": row["output_kind"],
                "output_id": row["output_id"], "output_version": row["output_version"],
                "frozen": bool(row["frozen"]), "invalidated_at": row["invalidated_at"],
                "invalidated_reason": row["invalidated_reason"]}


    def review_row(self, actor, book_id, source_id, row_number, *, status, reason):
        if status not in {"adopted", "excluded", "needs_review"}:
            raise AccountingError("来源行状态无效", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            source = conn.execute(
                "SELECT * FROM tax_source_files WHERE id=? AND book_id=? AND firm_id=?",
                (source_id, book_id, actor.firm_id),
            ).fetchone()
            if not source:
                raise AccountingError("来源文件不存在", 404)
            if source["status"] != "active":
                raise AccountingError("旧版本来源不能修改核定", 409)
            rows = json.loads(source["rows_json"])
            row = next((item for item in rows if int(item["row_number"]) == int(row_number)), None)
            if not row:
                raise AccountingError("来源行不存在", 404)
            if status == "excluded" and not (reason or "").strip():
                raise AccountingError("排除来源行必须说明原因", 422)
            if status == "adopted" and row["status"] != "adopted":
                # Re-adoption is only for a previously valid row explicitly excluded by a reviewer.
                # Malformed/missing facts require a corrected source version, never a status override.
                normalized = row.get("normalized", {})
                try:
                    valid = (bool(row["external_id"]) and date.fromisoformat(row["date"]).strftime("%Y-%m") == book["period"]
                             and _amount(normalized.get("amount", "")) == row["amount_cents"]
                             and row["amount_cents"] != 0)
                except (ValueError, TypeError):
                    valid = False
                if not valid or row["status"] == "needs_review":
                    raise AccountingError("来源事实未核定，请更正原文件并上传新版本，不能仅改为采用", 409)
            row["status"] = status
            row["reason"] = (reason or "").strip() or None
            amount = sum(int(item["amount_cents"]) for item in rows if item["status"] == "adopted")
            quantity = sum(int(item["quantity_milli"]) for item in rows if item["status"] == "adopted")
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE tax_source_files SET rows_json=?,amount_total_cents=?,quantity_milli=?,fingerprint=? WHERE id=?",
                (json.dumps(rows, ensure_ascii=False), amount, quantity,
                 _digest({"file": source["sha256"], "source": [source["source_system"], source["external_id"], source["version"]],
                          "rows": rows, "rule": source["rule_version"]}), source_id),
            )
            conn.execute(
                "UPDATE tax_source_bindings SET invalidated_at=?,invalidated_reason='source_review_changed' "
                "WHERE source_file_id=? AND frozen=FALSE AND invalidated_at IS NULL",
                (stamp, source_id),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (actor.firm_id, book_id, actor.user_id, "tax.source.row_review", source_id,
                 json.dumps({"row_number": row_number, "status": status, "reason": row["reason"]}),
                 stamp),
            )
            updated = conn.execute("SELECT * FROM tax_source_files WHERE id=?", (source_id,)).fetchone()
            return self._view(updated)



def _validate_contribution_lines(lines, row):
    seen = set()
    amount = 0
    quantity = 0
    for line in lines:
        output_line_id = (line.get("output_line_id") or "").strip()
        if not output_line_id:
            raise AccountingError("贡献输出行不能为空", 422)
        if output_line_id in seen:
            raise AccountingError(f"贡献输出行 {output_line_id} 重复", 409)
        seen.add(output_line_id)
        amount += int(line.get("amount_cents", 0))
        quantity += int(line.get("quantity_milli", 0))
    if amount != int(row["amount_cents"]) or quantity != int(row["quantity_milli"]):
        raise AccountingError(f"来源行 {row['row_number']} 的拆分行金额/数量与采用值不一致", 409)

def validate_contributions(result, contributions):
    """Validate that output lines consume every adopted source row exactly once."""
    adopted = {row["row_number"]: row for row in result["rows"] if row["status"] == "adopted"}
    seen = set()
    amount_total = 0
    quantity_total = 0
    for item in contributions:
        row_number = item.get("row_number")
        if row_number in seen:
            raise AccountingError(f"来源行 {row_number} 被重复采用", 409)
        row = adopted.get(row_number)
        if not row:
            raise AccountingError(f"来源行 {row_number} 未采用或不存在，不能作为贡献", 409)
        if int(item.get("amount_cents", -1)) != int(row["amount_cents"]):
            raise AccountingError(f"来源行 {row_number} 金额与原始采用值不一致", 409)
        quantity = int(item.get("quantity_milli", 0))
        if quantity < 0 or quantity != int(row["quantity_milli"]):
            raise AccountingError(f"来源行 {row_number} 数量与原始采用值不一致", 409)
        seen.add(row_number)
        amount_total += int(row["amount_cents"])
        quantity_total += int(row["quantity_milli"])
    missing = sorted(set(adopted) - seen)
    if missing:
        raise AccountingError(f"已采用来源行未全部贡献：{','.join(map(str, missing))}", 409)
    if amount_total != int(result["amount_total_cents"]) or quantity_total != int(result["quantity_milli"]):
        raise AccountingError("贡献合计与来源采用合计不一致", 409)
    return {"rows": len(seen), "amount_total_cents": amount_total, "quantity_milli": quantity_total}
