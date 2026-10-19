import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pydantic import ValidationError

from ..accounting.access import AccessControl
from ..accounting.bank_imports import BankImportService
from ..accounting.domain import AccountingError
from ..accounting.models import ImportRequest, SourceRow, cents_to_money, money_to_cents
from ..accounting.service import fingerprint, now
from .models import MappingSettings, PreviewRequest
from .parser import convert_row, parse_file, validate_import_original
from .storage import ImportOriginalStore


def dumped(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ImportWizardService:
    def __init__(self, db, storage, ledger=None, uploads=None):
        self.db, self.ledger = db, ledger
        self.access = AccessControl()
        self.storage = storage.clone(ImportOriginalStore)
        self.bank = BankImportService(db)
        self.uploads = uploads

    def _book(self, conn, actor, book_id, edit=False):
        book = self.access.book(conn, actor, book_id, "edit" if edit else "read")
        if edit and book["period_status"] != "open":
            raise AccountingError("该会计期间已关闭，不能继续导入", 403)
        return book

    def _company_of_book(self, actor, book_id):
        """Resolve the book's company up front for a company-scoped write lock."""
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id)["company_id"]

    def upload(self, actor, book_id, filename, data):
        with self.db.transaction(actor=actor) as conn:
            company_id = self._book(conn, actor, book_id, True)["company_id"]
        metadata, table = self.storage.put(filename, data)
        try:
            return self._record(actor, book_id, company_id, metadata, table)
        except Exception:
            self.storage.discard(metadata)
            raise

    def upload_stored(self, actor, book_id, stored):
        """两段式直传：校验已存原始件后登记，不再复制对象。"""
        with self.db.transaction(actor=actor) as conn:
            company_id = self._book(conn, actor, book_id, True)["company_id"]
        data = self.storage.read(stored)
        filename, media_type = validate_import_original(
            stored["filename"], stored["media_type"], data
        )
        metadata = {**stored, "filename": filename, "media_type": media_type}
        table = parse_file(filename, data)
        return self._record(actor, book_id, company_id, metadata, table)

    def upload_intent(self, actor, book_id, upload_id):
        if self.uploads is None:
            raise AccountingError("大文件直传尚未配置", 409)
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
        stored = self.uploads.peek(actor, upload_id, "bank_import", allow_consumed=True)
        data = self.storage.read(stored)
        filename, media_type = validate_import_original(
            stored["filename"], stored["media_type"], data
        )
        table = parse_file(filename, data)
        return self.uploads.consume(
            actor,
            upload_id,
            "bank_import",
            book_id,
            lambda conn, metadata: self._record(
                actor,
                book_id,
                None,
                {**metadata, "filename": filename, "media_type": media_type},
                table,
                conn=conn,
            ),
        )

    def _record(self, actor, book_id, company_id, metadata, table, conn=None):
        if conn is None:
            with self.db.transaction(
                write=True, actor=actor, company_id=company_id, write_scope="company"
            ) as transaction:
                return self._record(actor, book_id, company_id, metadata, table, transaction)
        book = self._book(conn, actor, book_id, not metadata.get("_upload_replay"))
        if metadata.get("_upload_replay"):
            item = self._file(conn, book_id, metadata["id"])
            return self._upload_result(dict(item), json.loads(item["table_json"]))
        threshold = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        count = conn.execute(
            "SELECT COUNT(*) FROM bank_import_files WHERE book_id=? AND created_by=? AND created_at>?",
            (book_id, actor.user_id, threshold),
        ).fetchone()[0]
        if count >= 20:
            raise AccountingError("此账套每人每小时最多上传20个导入文件，请稍后重试", 429)
        conn.execute(
            "INSERT INTO bank_import_files(id,book_id,firm_id,company_id,storage_key,filename,media_type,sha256,size_bytes,table_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                metadata["id"],
                book_id,
                actor.firm_id,
                book["company_id"],
                metadata["storage_key"],
                metadata["filename"],
                metadata["media_type"],
                metadata["sha256"],
                metadata["size_bytes"],
                dumped(table),
                actor.user_id,
                now(),
            ),
        )
        result = self._upload_result(metadata, table)
        self.storage.retain(metadata)
        return result

    @staticmethod
    def _upload_result(metadata, table):
        return {
            "file_id": metadata["id"],
            "filename": metadata["filename"],
            "sha256": metadata["sha256"],
            "headers": table["headers"],
            "sample_rows": table["rows"][:5],
            "row_count": len(table["rows"]),
            "encoding": table["encoding"],
            "suggested_columns": table["suggested_columns"],
            "ambiguous_fields": table["ambiguous_fields"],
            "sheet_name": table.get("sheet_name"),
        }

    def _file(self, conn, book_id, file_id):
        item = conn.execute(
            "SELECT * FROM bank_import_files WHERE id=? AND book_id=?", (file_id, book_id)
        ).fetchone()
        if not item:
            raise AccountingError("导入原件不存在或无权访问", 404)
        return item

    def profiles(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id, True)
            records = conn.execute(
                "SELECT p.* FROM bank_import_profiles p WHERE p.book_id=? AND NOT EXISTS(SELECT 1 FROM bank_import_profiles n WHERE n.book_id=p.book_id AND n.namespace=p.namespace AND n.bank_account=p.bank_account AND n.version>p.version) ORDER BY p.created_at DESC,p.id LIMIT 50",
                (book_id,),
            ).fetchall()
            return {
                "profiles": [
                    {
                        "id": row["id"],
                        "namespace": row["namespace"],
                        "bank_account": row["bank_account"],
                        "headers": json.loads(row["headers_json"]),
                        "mapping": json.loads(row["mapping_json"]),
                        "version": row["version"],
                    }
                    for row in records
                ]
            }

    def _rows(self, conn, actor, book, item, request):
        table = json.loads(item["table_json"])
        if any(column >= len(table["headers"]) for column in request.mapping.columns.values()):
            raise AccountingError("映射列超出文件实际列数", 422)
        output, seen, seen_facts, cents = [], {}, set(), {"income": 0, "expense": 0}
        for number, raw in enumerate(table["rows"], 2):
            errors, warnings, source, status = [], [], None, "new"
            try:
                source_row, generated = convert_row(raw, request.mapping, item["sha256"], number)
                source = source_row.model_dump()
                if source_row.date[:7] != book["period"]:
                    errors.append("交易日期不属于当前会计期间")
                digest = fingerprint(
                    source_row.model_dump(exclude={"external_id", "stable_transaction_id"})
                )
                stable = source_row.stable_transaction_id
                if generated:
                    warnings.append(
                        "缺银行交易号，仅同一文件同一行可幂等；换文件或排序后须人工辨别相似流水"
                    )
                previous = seen.get(stable)
                existing = conn.execute(
                    "SELECT source_id,content_fingerprint FROM bank_import_keys WHERE book_id=? AND namespace=? AND bank_account=? AND stable_transaction_id=?",
                    (book["id"], request.namespace, request.bank_account, stable),
                ).fetchone()
                if previous:
                    status = "duplicate" if previous == digest else "conflict"
                elif existing:
                    status = (
                        "duplicate" if existing["content_fingerprint"] == digest else "conflict"
                    )
                else:
                    elsewhere = (
                        conn.execute(
                            "SELECT accounting.bank_identity_elsewhere(?,?,?,?)",
                            (book["id"], request.namespace, request.bank_account, stable),
                        ).fetchone()[0]
                        if self.db.is_postgres
                        else conn.execute(
                            "SELECT 1 FROM bank_import_keys WHERE firm_id=? AND company_id=? AND book_id<>? AND namespace=? AND bank_account=? AND stable_transaction_id=?",
                            (
                                actor.firm_id,
                                book["company_id"],
                                book["id"],
                                request.namespace,
                                request.bank_account,
                                stable,
                            ),
                        ).fetchone()
                        is not None
                    )
                    if elsewhere:
                        errors.append("该企业其他账套已有同一交易号，请联系会计核对")
                    similar = conn.execute(
                        "SELECT 1 FROM sources s LEFT JOIN bank_import_keys k ON k.source_id=s.id AND k.book_id=s.book_id WHERE s.book_id=? AND s.source_kind='bank' AND s.date=? AND s.direction=? AND s.amount_cents=? AND s.counterparty=? AND (k.bank_account=? OR k.source_id IS NULL) AND NOT EXISTS(SELECT 1 FROM bank_source_dispositions d WHERE d.source_id=s.id AND d.book_id=s.book_id) LIMIT 1",
                        (
                            book["id"],
                            source_row.date,
                            source_row.direction,
                            money_to_cents(source_row.amount),
                            source_row.counterparty,
                            request.bank_account,
                        ),
                    ).fetchone()
                    if similar:
                        warnings.append(
                            "存在同日期、金额、方向和交易对方的流水；导入后会计须辨别是否独立交易"
                        )
                if status == "conflict":
                    errors.append("同一交易号对应不同内容，整批禁止确认")
                facts = (
                    source_row.date,
                    source_row.amount,
                    source_row.direction,
                    source_row.counterparty,
                )
                if facts in seen_facts and status == "new":
                    warnings.append(
                        "本文件有同日期、金额、方向和交易对方的其他交易号，导入后须辨别是否独立交易"
                    )
                seen_facts.add(facts)
                seen[stable] = digest
                if status == "new" and not errors:
                    cents[source_row.direction] += money_to_cents(source_row.amount)
            except ValidationError as error:
                labels = {
                    "external_id": "流水编号",
                    "stable_transaction_id": "银行交易号",
                    "date": "日期",
                    "description": "摘要",
                    "counterparty": "交易对方",
                    "amount": "金额",
                    "direction": "收入支出方向",
                    "evidence_ref": "凭据引用",
                }
                for problem in error.errors():
                    label = labels.get(str(problem["loc"][-1]), "字段")
                    if problem["type"] == "string_too_long":
                        errors.append(f"{label}过长，最多{problem['ctx']['max_length']}字")
                    elif problem["type"] == "string_too_short":
                        errors.append(f"{label}不能为空")
                    else:
                        errors.append(
                            label + "：" + problem["msg"].removeprefix("Value error, ")[:180]
                        )
            except ValueError as error:
                errors.append(str(error).split("\n")[0][:200])
            positions = table.get("row_positions", [])
            position = positions[number - 2] if number - 2 < len(positions) else number
            output.append(
                {
                    "line": number,
                    "original_position": position,
                    "raw": raw,
                    "source": source,
                    "status": "error" if errors else status,
                    "errors": errors,
                    "warnings": warnings,
                }
            )
        # Each transaction obeys MAX_CENTS; a batch aggregate may legitimately
        # exceed that per-transaction ceiling, still formatted with integers.
        return output, {
            direction: f"{amount // 100}.{amount % 100:02d}" for direction, amount in cents.items()
        }

    def preview(self, actor, book_id, request: PreviewRequest):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, True)
            item = self._file(conn, book_id, request.file_id)
            self.storage.read(item)
            rows, totals = self._rows(conn, actor, book, item, request)
            identifier = uuid4().hex
            payload = {
                "request": request.model_dump(),
                "rows": rows,
                "totals": totals,
                "parser_version": 1,
            }
            conn.execute(
                "INSERT INTO bank_import_previews(id,book_id,firm_id,company_id,file_id,payload_json,fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    item["id"],
                    dumped(payload),
                    fingerprint(payload),
                    actor.user_id,
                    now(),
                ),
            )
            return {
                "id": identifier,
                "rows": rows,
                "totals": totals,
                "error_count": sum(bool(row["errors"]) for row in rows),
                "new_count": sum(row["status"] == "new" for row in rows),
                "duplicate_count": sum(row["status"] == "duplicate" for row in rows),
                "company_name": book["company_name"],
                "period": book["period"],
                "namespace": request.namespace,
                "bank_account": request.bank_account,
            }

    def confirm(self, actor, book_id, preview_id, remember=False):
        company_id = self._company_of_book(actor, book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, book_id, True)
            preview = conn.execute(
                "SELECT * FROM bank_import_previews WHERE id=? AND book_id=?", (preview_id, book_id)
            ).fetchone()
            if not preview:
                raise AccountingError("预览不存在或无权访问", 404)
            prior = conn.execute(
                "SELECT result_json FROM bank_import_confirmations WHERE preview_id=? AND book_id=?",
                (preview_id, book_id),
            ).fetchone()
            if prior:
                return {**json.loads(prior[0]), "replayed": True}
            payload = json.loads(preview["payload_json"])
            request = PreviewRequest(**payload["request"])
            item = self._file(conn, book_id, request.file_id)
            self.storage.read(item)
            if any(row["errors"] for row in payload["rows"]):
                raise AccountingError("预览存在错误，请修正后重新预览，不能跳过错误行", 409)
            current_rows, _ = self._rows(conn, actor, book, item, request)
            if any(row["errors"] for row in current_rows):
                raise AccountingError("预览后交易身份或内容已变化，请重新核对；整批未导入", 409)
            imported_request = ImportRequest(
                source_name=item["filename"],
                namespace=request.namespace,
                bank_account=request.bank_account,
                rows=[SourceRow(**row["source"]) for row in payload["rows"]],
            )
            result = self.bank.import_in_transaction(conn, actor, book, imported_request)
            result = {**result, "book_id": book_id, "preview_id": preview_id, "file_id": item["id"]}
            for row in payload["rows"]:
                identity = conn.execute(
                    "SELECT source_id FROM bank_import_keys WHERE book_id=? AND namespace=? AND bank_account=? AND stable_transaction_id=?",
                    (
                        book_id,
                        request.namespace,
                        request.bank_account,
                        row["source"]["stable_transaction_id"],
                    ),
                ).fetchone()
                conn.execute(
                    "INSERT INTO bank_import_source_files(preview_id,file_id,book_id,firm_id,company_id,source_id,row_number) VALUES(?,?,?,?,?,?,?)",
                    (
                        preview_id,
                        item["id"],
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        identity[0],
                        row["line"],
                    ),
                )
            conn.execute(
                "INSERT INTO bank_import_confirmations(preview_id,book_id,firm_id,company_id,result_json,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    preview_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    dumped(result),
                    actor.user_id,
                    now(),
                ),
            )
            if remember:
                version = conn.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM bank_import_profiles WHERE book_id=? AND namespace=? AND bank_account=?",
                    (book_id, request.namespace, request.bank_account),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO bank_import_profiles(id,book_id,firm_id,company_id,namespace,bank_account,version,headers_json,mapping_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        request.namespace,
                        request.bank_account,
                        version,
                        dumped(json.loads(item["table_json"])["headers"]),
                        dumped(request.mapping.model_dump()),
                        actor.user_id,
                        now(),
                    ),
                )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    book_id,
                    actor.user_id,
                    "source.import.wizard",
                    preview_id,
                    dumped({"file_sha256": item["sha256"], "parser_version": 1, **result}),
                    now(),
                ),
            )
            return result

    def original(self, actor, book_id, file_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            item = self._file(conn, book_id, file_id)
            data = self.storage.read(item)
            return {"data": data, "filename": item["filename"], "media_type": item["media_type"]}

    def evidence(self, actor, book_id, source_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            if not conn.execute(
                "SELECT 1 FROM sources WHERE id=? AND book_id=?", (source_id, book_id)
            ).fetchone():
                raise AccountingError("来源不存在或无权访问", 404)
            records = conn.execute(
                "SELECT f.id,f.filename,f.sha256,f.size_bytes,f.table_json,p.payload_json,l.row_number FROM bank_import_source_files l JOIN bank_import_files f ON f.id=l.file_id AND f.book_id=l.book_id JOIN bank_import_previews p ON p.id=l.preview_id AND p.book_id=l.book_id WHERE l.source_id=? AND l.book_id=? ORDER BY f.created_at DESC,f.id LIMIT 20",
                (source_id, book_id),
            ).fetchall()
            return {
                "originals": [
                    {
                        "file_id": r["id"],
                        "filename": r["filename"],
                        "sha256": r["sha256"],
                        "row_number": r["row_number"],
                        "original_position": next(
                            (
                                line.get("original_position", line["line"])
                                for line in json.loads(r["payload_json"])["rows"]
                                if line["line"] == r["row_number"]
                            ),
                            r["row_number"],
                        ),
                        "headers": json.loads(r["table_json"])["headers"],
                        "raw": next(
                            (
                                line["raw"]
                                for line in json.loads(r["payload_json"])["rows"]
                                if line["line"] == r["row_number"]
                            ),
                            [],
                        ),
                        "normalized": next(
                            (
                                line["source"]
                                for line in json.loads(r["payload_json"])["rows"]
                                if line["line"] == r["row_number"]
                            ),
                            None,
                        ),
                        "mapping": json.loads(r["payload_json"])["request"]["mapping"],
                    }
                    for r in records
                ]
            }
