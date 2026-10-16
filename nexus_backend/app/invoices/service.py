"""发票导出表批次：只落证据行与勾稽线索，绝不产生会计事实。"""

import hashlib
import json
import re
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.queries import page_bounds
from ..accounting.service import now
from ..business.storage import MAX_FILE_BYTES
from .parser import parse_invoice_export


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_tax_id(value):
    return re.sub(r"\s+", "", value or "").upper()


def normalize_name(value):
    name = re.sub(r"\s+", "", value or "")
    return name.replace("（", "(").replace("）", ")").replace("，", ",").casefold()


class InvoiceImportService:
    def __init__(self, db, store, uploads):
        self.db, self.store, self.uploads = db, store, uploads
        self.access = AccessControl()

    def _audit(self, conn, actor, book, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,company_id,user_id,action,object_id,details,"
            "created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book["id"],
                book["company_id"],
                actor.user_id,
                action,
                object_id,
                encode(details),
                now(),
            ),
        )

    def create(self, actor, book_id, upload_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
        stored = self.uploads.take(actor, upload_id, "invoice_import")
        try:
            data = self.store.backend.read(stored["storage_key"], MAX_FILE_BYTES)
            if (
                len(data) != stored["size_bytes"]
                or hashlib.sha256(data).hexdigest() != stored["sha256"]
            ):
                raise AccountingError("原件与上传校验不一致，请重新上传", 409)
            parsed = parse_invoice_export(stored["filename"], data)
            batch = self._commit(actor, book, stored, parsed)
            self.uploads.confirm(actor, upload_id, "invoice_import")
            return batch
        except AccountingError:
            self.uploads.release(actor, upload_id)
            raise

    def _direction(self, row, company_tax):
        seller = normalize_tax_id(row["seller_tax"])
        buyer = normalize_tax_id(row["buyer_tax"])
        if company_tax:
            if seller and seller == company_tax:
                return "output"
            if buyer and buyer == company_tax:
                return "input"
        return "unknown"

    def _seen_before(self, conn, book_id):
        seen = set()
        for record in conn.execute(
            "SELECT rows_json FROM invoice_import_batches WHERE book_id=?", (book_id,)
        ):
            for old in json.loads(record["rows_json"]):
                if isinstance(old, dict) and old.get("key"):
                    seen.add(old["key"])
        return seen

    def _match(self, conn, book_id, rows):
        """用“交易对手 + 价税合计 + 方向”给发票行找银行流水候选，只作线索。"""
        expected = {"input": "expense", "output": "income"}
        needed = {
            row["total_cents"]
            for row in rows
            if not row["void"] and row["direction"] in expected and (row["total_cents"] or 0) > 0
        }
        if not needed:
            return 0
        by_amount = {}
        values = sorted(needed)
        for start in range(0, len(values), 400):
            chunk = values[start : start + 400]
            marks = ",".join("?" for _ in chunk)
            for source in conn.execute(
                "SELECT id,date,counterparty,amount_cents,direction FROM sources "
                f"WHERE book_id=? AND amount_cents IN ({marks})",
                (book_id, *chunk),
            ):
                by_amount.setdefault(source["amount_cents"], []).append(source)
        matched = 0
        for row in rows:
            row["match"] = None
            total = row["total_cents"]
            if row["void"] or row["direction"] not in expected or not total or total < 0:
                continue
            party = row["seller"] if row["direction"] == "input" else row["buyer"]
            name = normalize_name(party)
            if not name:
                continue
            for source in by_amount.get(total, []):
                if source["direction"] != expected[row["direction"]]:
                    continue
                bank = normalize_name(source["counterparty"])
                similar = bank == name or (
                    len(name) >= 4 and len(bank) >= 4 and (name in bank or bank in name)
                )
                if similar:
                    row["match"] = {
                        "source_id": source["id"],
                        "date": source["date"],
                        "counterparty": source["counterparty"],
                        "amount_cents": source["amount_cents"],
                    }
                    matched += 1
                    break
        return matched

    def _commit(self, actor, book, stored, parsed):
        with self.db.transaction(
            write=True, actor=actor, company_id=book["company_id"], write_scope="company"
        ) as conn:
            self.access.require_user(conn, actor.user_id)
            company = conn.execute(
                "SELECT tax_id FROM companies WHERE id=?", (book["company_id"],)
            ).fetchone()
            company_tax = normalize_tax_id(company["tax_id"] if company else "")
            rows = parsed["rows"]
            for row in rows:
                row["direction"] = self._direction(row, company_tax)
            kinds = {
                row["direction"]
                for row in rows
                if not row["void"] and row["direction"] in {"input", "output"}
            }
            direction = kinds.pop() if len(kinds) == 1 else "mixed"
            seen = self._seen_before(conn, book["id"])
            duplicates = 0
            for row in rows:
                if not row["duplicate"]:
                    if row["key"] in seen:
                        row["duplicate"] = True
                    else:
                        seen.add(row["key"])
                if row["duplicate"]:
                    duplicates += 1
            matched = self._match(conn, book["id"], rows)
            batch_id = uuid4().hex
            notes = {
                "warnings": parsed["warnings"],
                "skipped": parsed["skipped"],
                "duplicates": duplicates,
                "void": sum(1 for row in rows if row["void"]),
                "red": sum(1 for row in rows if row["red"]),
                "matched": matched,
                "source": parsed["source"],
                "columns": parsed["columns"],
                "company_tax_linked": bool(company_tax),
            }
            stamp = now()
            conn.execute(
                "INSERT INTO invoice_import_batches(id,book_id,firm_id,company_id,filename,"
                "storage_key,sha256,size_bytes,row_count,direction,rows_json,notes_json,created_by,"
                "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    book["id"],
                    actor.firm_id,
                    book["company_id"],
                    stored["filename"],
                    stored["storage_key"],
                    stored["sha256"],
                    stored["size_bytes"],
                    len(rows),
                    direction,
                    encode(rows),
                    encode(notes),
                    actor.user_id,
                    stamp,
                ),
            )
            self._audit(
                conn,
                actor,
                book,
                "invoice.import",
                batch_id,
                {
                    "filename": stored["filename"],
                    "row_count": len(rows),
                    "direction": direction,
                    "duplicates": duplicates,
                    "matched": matched,
                    "sha256": stored["sha256"],
                },
            )
            return {
                "id": batch_id,
                "filename": stored["filename"],
                "row_count": len(rows),
                "direction": direction,
                "duplicates": duplicates,
                "matched": matched,
                "created_at": stamp,
            }

    def list_batches(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id)
            total = conn.execute(
                "SELECT COUNT(*) AS count FROM invoice_import_batches WHERE book_id=?", (book_id,)
            ).fetchone()["count"]
            items = []
            for row in conn.execute(
                "SELECT id,filename,sha256,size_bytes,row_count,direction,notes_json,created_by,"
                "created_at FROM invoice_import_batches WHERE book_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (book_id, limit, offset),
            ):
                notes = json.loads(row["notes_json"])
                items.append(
                    {
                        **{
                            key: row[key]
                            for key in (
                                "id",
                                "filename",
                                "sha256",
                                "size_bytes",
                                "row_count",
                                "direction",
                                "created_by",
                                "created_at",
                            )
                        },
                        "duplicates": notes.get("duplicates", 0),
                        "matched": notes.get("matched", 0),
                        "warnings": notes.get("warnings", [])[:20],
                    }
                )
            return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_batch(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM invoice_import_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("导入批次不存在或已归档", 404)
            return {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "filename",
                        "sha256",
                        "size_bytes",
                        "row_count",
                        "direction",
                        "created_by",
                        "created_at",
                    )
                },
                "notes": json.loads(row["notes_json"]),
                "rows": json.loads(row["rows_json"]),
            }

    def original(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id)
            row = conn.execute(
                "SELECT filename,storage_key,sha256,size_bytes FROM invoice_import_batches "
                "WHERE id=? AND book_id=?",
                (batch_id, book_id),
            ).fetchone()
        if not row:
            raise AccountingError("导入批次不存在或已归档", 404)
        data = self.store.backend.read(row["storage_key"], MAX_FILE_BYTES)
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise AccountingError("原件校验不一致，已停止下载", 409)
        return row["filename"], data
