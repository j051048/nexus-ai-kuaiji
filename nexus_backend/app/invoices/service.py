"""发票导出表批次：只落证据行与勾稽线索，绝不产生会计事实。"""

import hashlib
import json
import re
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.catalog import CatalogService
from ..accounting.domain import AccountingError
from ..accounting.queries import page_bounds
from ..accounting.service import fingerprint, now
from ..business.storage import MAX_FILE_BYTES
from ..ledgers.service import LedgersService
from .parser import parse_invoice_export


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_tax_id(value):
    return re.sub(r"\s+", "", value or "").upper()


def normalize_name(value):
    name = re.sub(r"\s+", "", value or "")
    return name.replace("（", "(").replace("）", ")").replace("，", ",").casefold()


def accrual_ready(row):
    """A normal, complete invoice row with no current bank candidate."""
    amount, tax, total = row.get("amount_cents"), row.get("tax_cents"), row.get("total_cents")
    return (
        not row.get("void")
        and not row.get("red")
        and not row.get("duplicate")
        and row.get("direction") in {"input", "output"}
        and bool(row.get("date"))
        and row.get("match") is None
        and type(amount) is int
        and type(tax) is int
        and type(total) is int
        and amount >= 0
        and tax >= 0
        and total > 0
        and amount + tax == total
    )


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
            self.access.book(conn, actor, book_id, "read")
        stored = self.uploads.peek(actor, upload_id, "invoice_import", allow_consumed=True)
        parsed = parse_invoice_export(stored["filename"], self._stored_data(stored))
        return self.uploads.consume(actor, upload_id, "invoice_import", book_id,
            lambda conn, metadata: self._commit(actor,
                self.access.book(conn, actor, book_id, "read" if metadata.get("_upload_replay") else "edit"),
                metadata, parsed, conn=conn))

    def preview(self, actor, book_id, upload_id):
        """Parse and summarize a stored upload without creating a batch."""
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "edit")
        stored = self.uploads.peek(actor, upload_id, "invoice_import")
        parsed = parse_invoice_export(stored["filename"], self._stored_data(stored))
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            _rows, summary = self._analyze(conn, book, parsed)
        return {
            "upload_id": upload_id,
            "filename": stored["filename"],
            "size_bytes": stored["size_bytes"],
            **summary,
        }

    def _stored_data(self, stored):
        data = self.store.backend.read(stored["storage_key"], MAX_FILE_BYTES)
        if (
            len(data) != stored["size_bytes"]
            or hashlib.sha256(data).hexdigest() != stored["sha256"]
        ):
            raise AccountingError("原件与上传校验不一致，请重新上传", 409)
        return data

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
                f"WHERE book_id=? AND source_kind='bank' AND amount_cents IN ({marks})",
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

    def _analyze(self, conn, book, parsed):
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
        dates = sorted(row["date"] for row in rows if row["date"])
        company_tax_matched = sum(
            1
            for row in rows
            if not row["void"]
            and company_tax
            and company_tax
            in {
                normalize_tax_id(row["seller_tax"]),
                normalize_tax_id(row["buyer_tax"]),
            }
        )
        summary = {
            "row_count": len(rows),
            "direction": direction,
            "duplicates": duplicates,
            "matched": matched,
            "void": sum(1 for row in rows if row["void"]),
            "red": sum(1 for row in rows if row["red"]),
            "unknown_direction": sum(
                1 for row in rows if not row["void"] and row["direction"] == "unknown"
            ),
            "unmatched_eligible": sum(1 for row in rows if accrual_ready(row)),
            "company_tax_linked": bool(company_tax),
            "company_tax_matched": company_tax_matched,
            "date_from": dates[0] if dates else None,
            "date_to": dates[-1] if dates else None,
            "skipped": parsed["skipped"],
            "warnings": parsed["warnings"],
            "source": parsed["source"],
            "columns": parsed["columns"],
        }
        return rows, summary

    def _commit(self, actor, book, stored, parsed, conn=None):
        if conn is None:
            with self.db.transaction(
                write=True, actor=actor, company_id=book["company_id"], write_scope="company"
            ) as transaction:
                return self._commit(actor, book, stored, parsed, transaction)
        self.access.require_user(conn, actor.user_id)
        if stored.get("_upload_replay"):
            item = conn.execute("SELECT * FROM invoice_import_batches WHERE storage_key=? AND book_id=?", (stored["storage_key"], book["id"])).fetchone()
            summary = json.loads(item["notes_json"])
            return {"id": item["id"], "filename": item["filename"], "row_count": item["row_count"], "direction": item["direction"], "duplicates": summary["duplicates"], "matched": summary["matched"], "created_at": item["created_at"]}
        book = self.access.book(conn, actor, book["id"], "edit")
        if book["period_status"] != "open":
            raise AccountingError("该会计期间已关闭，不能继续导入", 403)
        rows, summary = self._analyze(conn, book, parsed)
        direction = summary["direction"]
        duplicates = summary["duplicates"]
        matched = summary["matched"]
        batch_id = uuid4().hex
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
                encode(summary),
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

    @staticmethod
    def _accrual_external_id(batch_id, line):
        return f"invoice:{batch_id}:{line}"

    def accrue(self, actor, book_id, batch_id, request):
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, "edit")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.access.require_user(conn, actor.user_id)
            book = self.access.book(conn, actor, book_id, "edit")
            if book["period_status"] != "open":
                raise AccountingError("该会计期间已关闭，不可生成挂账草稿", 403)
            batch = conn.execute(
                "SELECT * FROM invoice_import_batches WHERE id=? AND book_id=?",
                (batch_id, book_id),
            ).fetchone()
            if not batch:
                raise AccountingError("导入批次不存在或已归档", 404)
            rows = json.loads(batch["rows_json"])
            self._match(conn, book_id, rows)
            by_line = {row["line"]: row for row in rows if isinstance(row, dict)}
            accounts = {
                row["code"]: row
                for row in conn.execute(
                    "SELECT code,name,category,normal_balance,auxiliary_requirements FROM accounts "
                    "WHERE book_id=? AND active=1",
                    (book_id,),
                )
            }
            ledger, catalog = LedgersService(self.db), CatalogService(self.db)
            pending, existing = [], []
            for item in request.items:
                invoice = by_line.get(item.line)
                if not invoice:
                    raise AccountingError(f"发票第 {item.line} 行不存在，请刷新批次", 404)
                if not accrual_ready(invoice):
                    raise AccountingError(
                        f"发票第 {item.line} 行已匹配银行流水、重复/红冲/作废，或金额不完整，不能挂账",
                        409,
                    )
                if not invoice["date"].startswith(book["period"] + "-"):
                    raise AccountingError(
                        f"发票第 {item.line} 行日期不属于当前账套期间 {book['period']}",
                        409,
                    )
                tax_cents = invoice["tax_cents"]
                codes = [item.settlement_account, item.profit_account]
                if tax_cents:
                    if not item.tax_account:
                        raise AccountingError(f"发票第 {item.line} 行有税额，请选择税额科目")
                    codes.append(item.tax_account)
                if len(codes) != len(set(codes)):
                    raise AccountingError(f"发票第 {item.line} 行的三个科目必须互不相同")
                missing = [code for code in codes if code not in accounts]
                if missing:
                    raise AccountingError(f"科目 {missing[0]} 不属于当前账套或已停用，请刷新科目表")
                external_id = self._accrual_external_id(batch_id, item.line)
                source = conn.execute(
                    "SELECT id FROM sources WHERE book_id=? AND external_id=?",
                    (book_id, external_id),
                ).fetchone()
                if source:
                    draft = conn.execute(
                        "SELECT id,status,version FROM drafts WHERE source_id=? AND book_id=?",
                        (source["id"], book_id),
                    ).fetchone()
                    if not draft:
                        raise AccountingError(
                            f"发票第 {item.line} 行已有来源但缺少草稿，请人工核对", 409
                        )
                    existing.append(
                        {
                            "line": item.line,
                            "source_id": source["id"],
                            "draft_id": draft["id"],
                            "status": draft["status"],
                            "version": draft["version"],
                        }
                    )
                else:
                    pending.append((item, invoice))
            if existing and pending:
                raise AccountingError("所选发票中部分已生成挂账草稿，请刷新后核对", 409)
            if not pending:
                return {"created": 0, "replayed": True, "items": existing}

            stamp = now()
            created = []
            for item, invoice in pending:
                direction = invoice["direction"]
                amount, tax, total = (
                    invoice["amount_cents"],
                    invoice["tax_cents"],
                    invoice["total_cents"],
                )
                party = invoice["seller"] if direction == "input" else invoice["buyer"]
                counterparty = (
                    ledger.resolve_in_transaction(
                        conn,
                        actor,
                        book["company_id"],
                        "counterparty",
                        party,
                        book_id,
                    )
                    if party
                    else None
                )
                item_name = invoice.get("item") or "发票事项"
                description = (
                    f"{'进项' if direction == 'input' else '销项'}发票挂账 "
                    f"{invoice['number']} · {party or '往来方待确认'} · {item_name}"
                )[:500]
                evidence_ref = f"发票导入批次 {batch_id} 第 {item.line} 行：{invoice['key']}"[:500]
                source_id, draft_id = uuid4().hex, uuid4().hex
                conn.execute(
                    "INSERT INTO sources(id,book_id,external_id,source_name,date,description,"
                    "counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'business')",
                    (
                        source_id,
                        book_id,
                        self._accrual_external_id(batch_id, item.line),
                        "电子发票挂账",
                        invoice["date"],
                        description,
                        party,
                        total,
                        "expense" if direction == "input" else "income",
                        evidence_ref,
                        fingerprint(
                            {
                                "batch_id": batch_id,
                                "line": item.line,
                                "invoice": invoice["key"],
                                "amount_cents": total,
                                "direction": direction,
                            }
                        ),
                    ),
                )
                conn.execute(
                    "INSERT INTO source_evidence(source_id,book_id,evidence_ref,created_at,"
                    "created_by) VALUES(?,?,?,?,?)",
                    (source_id, book_id, evidence_ref, stamp, actor.user_id),
                )
                explanation = (
                    f"发票导入批次 {batch_id} 第 {item.line} 行未找到银行流水候选，"
                    f"会计按往来挂账处理：{request.reason}"
                )
                conn.execute(
                    "INSERT INTO drafts(id,book_id,source_id,description,explanation) "
                    "VALUES(?,?,?,?,?)",
                    (draft_id, book_id, source_id, description, explanation),
                )
                lines = []
                if direction == "output":
                    lines.append((item.settlement_account, total, 0))
                    if amount:
                        lines.append((item.profit_account, 0, amount))
                    if tax:
                        lines.append((item.tax_account, 0, tax))
                else:
                    if amount:
                        lines.append((item.profit_account, amount, 0))
                    if tax:
                        lines.append((item.tax_account, tax, 0))
                    lines.append((item.settlement_account, 0, total))
                if sum(line[1] for line in lines) != sum(line[2] for line in lines):
                    raise AccountingError("挂账分录借贷不平衡，批次未生成草稿", 409)
                conn.executemany(
                    "INSERT INTO draft_lines"
                    "(draft_id,book_id,ordinal,account_code,debit_cents,credit_cents) "
                    "VALUES(?,?,?,?,?,?)",
                    [(draft_id, book_id, ordinal, *line) for ordinal, line in enumerate(lines)],
                )
                auxiliaries = [{} for _line in lines]
                if counterparty:
                    for ordinal, (account_code, _debit, _credit) in enumerate(lines):
                        requirements = json.loads(accounts[account_code]["auxiliary_requirements"])
                        if "counterparty" in requirements:
                            auxiliaries[ordinal]["counterparty"] = counterparty["entity_id"]
                if any(auxiliaries):
                    catalog.save_auxiliaries_in_transaction(
                        conn, actor, book_id, draft_id, auxiliaries
                    )
                self._audit(
                    conn,
                    actor,
                    book,
                    "invoice.accrual",
                    draft_id,
                    {
                        "batch_id": batch_id,
                        "line": item.line,
                        "invoice": invoice["key"],
                        "direction": direction,
                        "amount_cents": total,
                        "accounts": {
                            "settlement": item.settlement_account,
                            "profit": item.profit_account,
                            "tax": item.tax_account,
                        },
                        "counterparty_entity_id": (
                            counterparty["entity_id"] if counterparty else None
                        ),
                        "source_id": source_id,
                        "reason": request.reason,
                    },
                )
                created.append(
                    {
                        "line": item.line,
                        "source_id": source_id,
                        "draft_id": draft_id,
                        "status": "draft",
                        "version": 1,
                    }
                )
            return {"created": len(created), "replayed": False, "items": created}

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
            rows = json.loads(row["rows_json"])
            accruals = {}
            prefix = f"invoice:{batch_id}:"
            for record in conn.execute(
                "SELECT s.id AS source_id,s.external_id,d.id AS draft_id,d.status,d.version "
                "FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id "
                "WHERE s.book_id=? AND s.external_id LIKE ?",
                (book_id, prefix + "%"),
            ):
                try:
                    line = int(record["external_id"].rsplit(":", 1)[1])
                except (TypeError, ValueError):
                    continue
                accruals[line] = {
                    "source_id": record["source_id"],
                    "draft_id": record["draft_id"],
                    "status": record["status"],
                    "version": record["version"],
                }
            for invoice in rows:
                invoice["accrual"] = accruals.get(invoice["line"])
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
                "rows": rows,
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
