"""Frozen export manifests and deterministic comparison of manually supplied facts.

`verified` means the supplied receipt data matches the frozen export; it is not
an assertion that an independently authenticated software provider posted it.
No stored field can grant a verified state or reopen an exported journal.
"""

import csv
import hashlib
import io
import json
from collections import Counter
from uuid import uuid4
from pydantic import ValidationError
from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.models import cents_to_money
from ..accounting.queries import page_bounds
from ..accounting.service import fingerprint, now, safe_csv_text
from .models import ReceiptCreate, ReceiptFacts

MAX_CONTENT_BYTES = 2_000_000
CSV_FIELDS = [
    "book_id",
    "company_id",
    "period",
    "currency",
    "draft_id",
    "external_voucher_id",
    "status",
    "date",
    "amount",
    "description",
    "ordinal",
    "account_code",
    "debit",
    "credit",
    "auxiliary_values",
]


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DeliveryService:
    def __init__(self, db):
        self.db, self.access = db, AccessControl()

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor.firm_id, book_id, actor.user_id, action, object_id, encode(details), now()),
        )

    def _export(self, conn, actor, book_id, export_id, write=False):
        book = self.access.book(conn, actor, book_id, "review" if write else "read")
        row = conn.execute(
            "SELECT * FROM exports WHERE id=? AND book_id=?", (export_id, book_id)
        ).fetchone()
        if not row:
            raise AccountingError("导出记录不存在或无权访问", 404)
        return book, row

    @staticmethod
    def _lines(conn, book_id, draft_id):
        rows = conn.execute(
            "SELECT l.*,a.values_json FROM draft_lines l LEFT JOIN draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal WHERE l.book_id=? AND l.draft_id=? ORDER BY l.ordinal",
            (book_id, draft_id),
        )
        return [
            {
                "ordinal": row["ordinal"],
                "account_code": row["account_code"],
                "debit": cents_to_money(row["debit_cents"]),
                "credit": cents_to_money(row["credit_cents"]),
                "auxiliary_values": json.loads(row["values_json"]) if row["values_json"] else {},
            }
            for row in rows
        ]

    def capture_export_in_transaction(self, conn, actor, book, export_id, selected):
        """Call after frozen statuses and draft.export audit, before commit."""
        self.access.book(conn, actor, book["id"], "review")
        if conn.execute(
            "SELECT 1 FROM delivery_manifests WHERE export_id=? AND book_id=?",
            (export_id, book["id"]),
        ).fetchone():
            return
        items = []
        for chosen in sorted(selected, key=lambda row: row["id"]):
            draft = conn.execute(
                "SELECT * FROM drafts WHERE id=? AND book_id=?", (chosen["id"], book["id"])
            ).fetchone()
            source = (
                conn.execute(
                    "SELECT * FROM sources WHERE id=? AND book_id=?",
                    (draft["source_id"], book["id"]),
                ).fetchone()
                if draft
                else None
            )
            if (
                not draft
                or not source
                or draft["status"] != "exported"
                or draft["version"] != chosen["version"] + 1
            ):
                raise AccountingError("导出清单必须绑定本事务刚冻结的审核版本", 409)
            lines = self._lines(conn, book["id"], draft["id"])
            if [
                {k: row[k] for k in ("account_code", "debit", "credit", "auxiliary_values")}
                for row in lines
            ] != [
                {
                    "account_code": row["account_code"],
                    "debit": row["debit"],
                    "credit": row["credit"],
                    "auxiliary_values": row.get("auxiliary_values", {}),
                }
                for row in chosen["lines"]
            ] or draft["description"] != chosen["description"]:
                raise AccountingError("导出清单与已冻结分录不同", 409)
            items.append(
                {
                    "draft_id": draft["id"],
                    "source_id": source["id"],
                    "approved_version": chosen["version"],
                    "exported_version": draft["version"],
                    "date": source["date"],
                    "description": draft["description"],
                    "amount": cents_to_money(source["amount_cents"]),
                    "currency": "CNY",
                    "source_fingerprint": source["fingerprint"],
                    "lines": lines,
                }
            )
        catalog = [
            dict(row)
            for row in conn.execute(
                "SELECT code,name,active,version,auxiliary_requirements FROM accounts WHERE book_id=? ORDER BY code",
                (book["id"],),
            )
        ]
        payload = {
            "version": 1,
            "export_id": export_id,
            "book_id": book["id"],
            "company_id": book["company_id"],
            "period": book["period"],
            "currency": "CNY",
            "catalog": catalog,
            "items": items,
        }
        self._store_manifest(conn, actor, book, export_id, payload, "current_export")

    def _store_manifest(self, conn, actor, book, export_id, payload, origin):
        if self.db.is_postgres:
            conn.execute(
                "SELECT accounting.capture_delivery_manifest(?,?,?)",
                (export_id, encode(payload), origin),
            )
        else:
            conn.execute(
                "INSERT INTO delivery_manifests(export_id,book_id,firm_id,company_id,period,origin,payload_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    export_id,
                    book["id"],
                    actor.firm_id,
                    book["company_id"],
                    book["period"],
                    origin,
                    encode(payload),
                    actor.user_id,
                    now(),
                ),
            )

    def _manifest(self, conn, export_row):
        row = conn.execute(
            "SELECT * FROM delivery_manifests WHERE export_id=? AND book_id=?",
            (export_row["id"], export_row["book_id"]),
        ).fetchone()
        if not row:
            # Historical exports did not retain a catalog snapshot. We do not
            # manufacture it from today's accounts or silently assert delivery.
            return {
                "export_id": export_row["id"],
                "status": "pending_legacy",
                "origin": "legacy",
                "items": [],
                "canonical_fingerprint": None,
                "catalog_fingerprint": None,
                "reason": "旧导出缺少完整历史清单；原 CSV 保留，需人工核对历史凭证和客户软件记录",
            }
        payload = json.loads(row["payload_json"])
        return {
            **payload,
            "status": "captured",
            "origin": row["origin"],
            "canonical_fingerprint": fingerprint(payload),
            "catalog_fingerprint": fingerprint(payload["catalog"])
            if payload.get("catalog") is not None
            else None,
        }

    def manifest(self, actor, book_id, export_id):
        with self.db.transaction(actor=actor) as conn:
            _, row = self._export(conn, actor, book_id, export_id)
            return self._manifest(conn, row)

    def backfill_manifest(self, actor, book_id, export_id):
        """Reconstruct only immutable audit-backed journals, never today's chart."""
        with self.db.transaction(write=True, actor=actor) as conn:
            book, export = self._export(conn, actor, book_id, export_id, write=True)
            if conn.execute(
                "SELECT 1 FROM delivery_manifests WHERE export_id=? AND book_id=?",
                (export_id, book_id),
            ).fetchone():
                return self._manifest(conn, export)
            audits = conn.execute(
                "SELECT details FROM audit_events WHERE firm_id=? AND book_id=? AND action='draft.export' AND object_id=? ORDER BY id",
                (actor.firm_id, book_id, export_id),
            ).fetchall()
            try:
                if len(audits) != 1:
                    raise ValueError("missing export lineage")
                ids = json.loads(audits[0]["details"])["draft_ids"]
                if (
                    not isinstance(ids, list)
                    or len(ids) != export["count"]
                    or len(set(ids)) != len(ids)
                    or not 1 <= len(ids) <= 1000
                ):
                    raise ValueError("invalid immutable selection")
                items, expected_rows = [], []
                actual_rows = list(
                    csv.reader(io.StringIO(export["csv"].lstrip("\ufeff")), strict=True)
                )
                headers = [
                    "凭证ID",
                    "日期",
                    "摘要",
                    "科目编码",
                    "借方金额",
                    "贷方金额",
                    "币种",
                    "证据引用",
                    "辅助部门",
                    "辅助人员",
                    "辅助交易对方",
                    "辅助项目",
                ]
                if not actual_rows or actual_rows[0] not in (headers, headers[:8]):
                    raise ValueError("unknown historical CSV format")
                width = len(actual_rows[0])
                for draft_id in sorted(ids):
                    draft = conn.execute(
                        "SELECT * FROM drafts WHERE id=? AND book_id=? AND status='exported'",
                        (draft_id, book_id),
                    ).fetchone()
                    source = (
                        conn.execute(
                            "SELECT * FROM sources WHERE id=? AND book_id=?",
                            (draft["source_id"], book_id),
                        ).fetchone()
                        if draft
                        else None
                    )
                    approval = conn.execute(
                        "SELECT details FROM audit_events WHERE firm_id=? AND book_id=? AND action='draft.approve' AND object_id=? ORDER BY id",
                        (actor.firm_id, book_id, draft_id),
                    ).fetchall()
                    if (
                        not draft
                        or not source
                        or len(approval) != 1
                        or source["date"][:7] != book["period"]
                    ):
                        raise ValueError("missing frozen journal")
                    proof = json.loads(approval[0]["details"])
                    lines = self._lines(conn, book_id, draft_id)
                    public = [
                        {
                            key: line[key]
                            for key in ("account_code", "debit", "credit", "auxiliary_values")
                        }
                        for line in lines
                    ]
                    proof_lines = [
                        {**line, "auxiliary_values": line.get("auxiliary_values", {})}
                        for line in proof["lines"]
                    ]
                    if (
                        proof.get("reviewed_version") != draft["version"] - 2
                        or public != proof_lines
                        or not isinstance(proof.get("evidence_ref"), str)
                    ):
                        raise ValueError("approval cannot prove original line version")
                    items.append(
                        {
                            "draft_id": draft_id,
                            "source_id": source["id"],
                            "approved_version": draft["version"] - 1,
                            "exported_version": draft["version"],
                            "date": source["date"],
                            "description": draft["description"],
                            "amount": cents_to_money(source["amount_cents"]),
                            "currency": "CNY",
                            "source_fingerprint": source["fingerprint"],
                            "lines": lines,
                        }
                    )
                    for line in lines:
                        row = [
                            safe_csv_text(draft_id),
                            source["date"],
                            safe_csv_text(draft["description"]),
                            safe_csv_text(line["account_code"]),
                            line["debit"],
                            line["credit"],
                            "CNY",
                            safe_csv_text(proof["evidence_ref"]),
                            *[
                                safe_csv_text(line["auxiliary_values"].get(key, ""))
                                for key in ("department", "employee", "counterparty", "project")
                            ],
                        ]
                        if width == 8 and any(line["auxiliary_values"].values()):
                            raise ValueError("historical export does not prove auxiliaries")
                        expected_rows.append(row[:width])
                if sorted(actual_rows[1:]) != sorted(expected_rows):
                    raise ValueError("historical CSV differs from approval proof")
                payload = {
                    "version": 1,
                    "export_id": export_id,
                    "book_id": book_id,
                    "company_id": book["company_id"],
                    "period": book["period"],
                    "currency": "CNY",
                    "catalog": None,
                    "items": items,
                }
            except (ValueError, TypeError, KeyError, csv.Error):
                return self._manifest(conn, export)
            self._store_manifest(conn, actor, book, export_id, payload, "legacy_audit")
            self._audit(
                conn,
                actor,
                book_id,
                "delivery.manifest.backfill",
                export_id,
                {
                    "origin": "legacy_audit",
                    "historical_catalog_known": False,
                    "canonical_fingerprint": fingerprint(payload),
                },
            )
            return self._manifest(conn, export)

    def overview(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id)
            total = conn.execute(
                "SELECT COUNT(*) FROM exports WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            exports = []
            for row in conn.execute(
                "SELECT e.*,m.origin AS manifest_origin FROM exports e LEFT JOIN delivery_manifests m ON m.export_id=e.id AND m.book_id=e.book_id WHERE e.book_id=? ORDER BY e.created_at DESC,e.id DESC LIMIT ? OFFSET ?",
                (book_id, limit, offset),
            ):
                receipt_count = conn.execute(
                    "SELECT COUNT(*) FROM delivery_receipts WHERE export_id=? AND book_id=?",
                    (row["id"], book_id),
                ).fetchone()[0]
                exports.append(
                    {
                        "id": row["id"],
                        "filename": row["filename"],
                        "count": row["count"],
                        "created_at": row["created_at"],
                        "manifest_status": "captured"
                        if row["manifest_origin"]
                        else "pending_legacy",
                        "receipt_count": receipt_count,
                    }
                )
            return {
                "book_id": book_id,
                "book_name": book["name"],
                "company_id": book["company_id"],
                "company_name": book["company_name"],
                "period": book["period"],
                "period_status": book["period_status"],
                "can_import": book["permissions"]["review"],
                "exports": exports,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(exports) < total,
                },
                "mode": "manual_receipt_no_external_connection",
            }

    def detail(self, actor, book_id, export_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book, export = self._export(conn, actor, book_id, export_id)
            total = conn.execute(
                "SELECT COUNT(*) FROM delivery_receipts WHERE export_id=? AND book_id=?",
                (export_id, book_id),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM delivery_receipts WHERE export_id=? AND book_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (export_id, book_id, limit, offset),
            ).fetchall()
            return {
                "manifest": self._manifest(conn, export),
                "can_import": book["permissions"]["review"],
                "receipts": [self._receipt_json(conn, actor, export, row) for row in rows],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": total,
                    "has_more": offset + len(rows) < total,
                },
            }

    def _receipt_json(self, conn, actor, export, row):
        body = json.loads(row["body_json"])
        manifest = self._manifest(conn, export)
        current = {item["draft_id"]: item for item in manifest["items"]}
        counts = Counter(item["draft_id"] for item in body["items"])
        external_counts = Counter(
            item["external_voucher_id"] for item in body["items"] if item.get("external_voucher_id")
        )
        results = []
        envelope_issues = []
        missing_envelope = []
        expected = {
            "book_id": row["book_id"],
            "company_id": row["company_id"],
            "period": manifest.get("period"),
            "currency": "CNY",
        }
        for key, value in expected.items():
            if body.get(key) is None or value is None:
                missing_envelope.append(key)
            elif body[key] != value:
                envelope_issues.append(key + "_mismatch")
        for item in body["items"]:
            issues, missing = list(envelope_issues), list(missing_envelope)
            original = current.get(item["draft_id"])
            if counts[item["draft_id"]] > 1:
                issues.append("duplicate_draft_id")
            external = item.get("external_voucher_id")
            if external and external_counts[external] > 1:
                issues.append("duplicate_external_voucher_id")
            if not original:
                issues.append(
                    "legacy_manifest_missing"
                    if manifest["status"] == "pending_legacy"
                    else "unknown_draft_id"
                )
            else:
                for key in ("date", "amount", "description"):
                    if item.get(key) is None:
                        missing.append(key)
                    elif item[key] != original[key]:
                        issues.append(key + "_mismatch")
                if item.get("lines") is None:
                    missing.append("lines")
                else:
                    expected_lines = {line["ordinal"]: line for line in original["lines"]}
                    ordinals = [line.get("ordinal") for line in item["lines"]]
                    if any(value is None for value in ordinals):
                        missing.append("line_ordinal")
                    elif len(set(ordinals)) != len(ordinals) or set(ordinals) != set(
                        expected_lines
                    ):
                        issues.append("line_set_mismatch")
                    for line in item["lines"]:
                        wanted = expected_lines.get(line.get("ordinal"))
                        if wanted:
                            for key in ("account_code", "debit", "credit", "auxiliary_values"):
                                if line.get(key) is None:
                                    missing.append("line_" + key)
                                elif line[key] != wanted[key]:
                                    issues.append("line_" + key + "_mismatch")
            if item["status"] == "rejected":
                issues.append("external_rejected")
            elif item["status"] == "unknown":
                missing.append("external_status_unknown")
            elif not external:
                missing.append("external_voucher_id")
            if external:
                historical = conn.execute(
                    "SELECT DISTINCT claimed_draft_id,external_voucher_id FROM delivery_receipt_items WHERE book_id=? AND receipt_id<>? AND (claimed_draft_id=? OR external_voucher_id=?)",
                    (row["book_id"], row["id"], item["draft_id"], external),
                ).fetchall()
                if any(
                    h["claimed_draft_id"] != item["draft_id"]
                    and h["external_voucher_id"] == external
                    for h in historical
                ):
                    issues.append("external_voucher_reused")
                if any(
                    h["claimed_draft_id"] == item["draft_id"]
                    and h["external_voucher_id"]
                    and h["external_voucher_id"] != external
                    for h in historical
                ):
                    issues.append("multiple_external_vouchers")
            state = "discrepancy" if issues else "received_unverified" if missing else "verified"
            notes = [
                dict(note)
                for note in conn.execute(
                    "SELECT id,issue_key,reason,created_by,created_at FROM delivery_notes WHERE receipt_id=? AND book_id=? AND draft_id=? ORDER BY created_at,id",
                    (row["id"], row["book_id"], item["draft_id"]),
                )
            ]
            results.append(
                {
                    "draft_id": item["draft_id"],
                    "external_voucher_id": external,
                    "reported_status": item["status"],
                    "state": state,
                    "issues": sorted(set(issues)),
                    "missing_fields": sorted(set(missing)),
                    "notes": notes,
                }
            )
        absent = sorted(set(current) - set(counts))
        for draft_id in absent:
            notes = [
                dict(note)
                for note in conn.execute(
                    "SELECT id,issue_key,reason,created_by,created_at FROM delivery_notes WHERE receipt_id=? AND book_id=? AND draft_id=? ORDER BY created_at,id",
                    (row["id"], row["book_id"], draft_id),
                )
            ]
            results.append(
                {
                    "draft_id": draft_id,
                    "external_voucher_id": None,
                    "reported_status": "unknown",
                    "state": "received_unverified",
                    "issues": [],
                    "missing_fields": ["receipt_item_missing"],
                    "notes": notes,
                }
            )
        summary = {
            key: sum(item["state"] == key for item in results)
            for key in ("verified", "received_unverified", "discrepancy")
        }
        return {
            "id": row["id"],
            "export_id": row["export_id"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
            "reason": row["reason"],
            "import_format": row["import_format"],
            "raw_hash": row["raw_hash"],
            "items": results,
            "summary": summary,
            "evidence_mode": "manually_supplied_receipt",
            "automatic_resend": False,
        }

    def create_receipt(
        self, actor, book_id, export_id, request, import_format="structured", raw_hash=None
    ):
        body = request.model_dump(exclude={"idempotency_key", "reason"})
        if len(encode(body).encode("utf-8")) > MAX_CONTENT_BYTES:
            raise AccountingError("结构化回执事实最多 2 MB", 413)
        digest = fingerprint(
            {
                "book_id": book_id,
                "export_id": export_id,
                "facts": body,
                "reason": request.reason,
                "format": import_format,
                "raw_hash": raw_hash,
            }
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            book, export = self._export(conn, actor, book_id, export_id, write=True)
            prior = conn.execute(
                "SELECT * FROM delivery_receipts WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if (
                    prior["request_fingerprint"] != digest
                    or prior["book_id"] != book_id
                    or prior["export_id"] != export_id
                ):
                    raise AccountingError("回执幂等键已用于不同导出或不同内容", 409)
                return self._receipt_json(conn, actor, export, prior)
            receipt_id = uuid4().hex
            conn.execute(
                "INSERT INTO delivery_receipts(id,export_id,book_id,firm_id,company_id,body_json,request_fingerprint,idempotency_key,reason,import_format,raw_hash,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    export_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    encode(body),
                    digest,
                    request.idempotency_key,
                    request.reason,
                    import_format,
                    raw_hash or fingerprint(body),
                    actor.user_id,
                    now(),
                ),
            )
            for ordinal, item in enumerate(body["items"]):
                conn.execute(
                    "INSERT INTO delivery_receipt_items(receipt_id,book_id,firm_id,company_id,ordinal,claimed_draft_id,external_voucher_id,raw_json) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        receipt_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        ordinal,
                        item["draft_id"],
                        item.get("external_voucher_id"),
                        encode(item),
                    ),
                )
            self._audit(
                conn,
                actor,
                book_id,
                "delivery.receipt.import",
                receipt_id,
                {"export_id": export_id, "fact_count": len(body["items"]), "format": import_format},
            )
            return self._receipt_json(
                conn,
                actor,
                export,
                conn.execute(
                    "SELECT * FROM delivery_receipts WHERE id=? AND book_id=?",
                    (receipt_id, book_id),
                ).fetchone(),
            )

    def import_receipt(self, actor, book_id, export_id, request):
        if len(request.content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise AccountingError("回执文件最多 2 MB", 413)
        # Authorization precedes parsing or retaining customer data.
        with self.db.transaction(actor=actor) as conn:
            self._export(conn, actor, book_id, export_id, write=True)
        try:
            if request.format == "json":
                facts = ReceiptFacts.model_validate(json.loads(request.content.lstrip("\ufeff")))
            else:
                parsed = self._parse_csv(request.content)
                with self.db.transaction(actor=actor) as conn:
                    _, export = self._export(conn, actor, book_id, export_id, write=True)
                    self._decode_csv_manifest_fields(conn, export, parsed)
                # Safety encoding adds one character at the schema's maximum
                # length. Only proven encodings may be decoded before bounds.
                facts = ReceiptFacts.model_validate(parsed)
            payload = ReceiptCreate(
                **facts.model_dump(), idempotency_key=request.idempotency_key, reason=request.reason
            )
        except (ValidationError, ValueError, csv.Error, TypeError, KeyError) as error:
            raise AccountingError(
                "回执格式无效，请使用本导出的中性 JSON / CSV 模板；金额须为精确字符串", 422
            ) from error
        return self.create_receipt(
            actor,
            book_id,
            export_id,
            payload,
            request.format,
            hashlib.sha256(request.content.encode("utf-8")).hexdigest(),
        )

    def _decode_csv_manifest_fields(self, conn, export, facts):
        """Reverse only this frozen field's own exact server safety encoding.

        Real apostrophes and changed text stay untouched; unknown vouchers and
        unknown line ordinals are never interpreted against another source.
        """
        manifest = self._manifest(conn, export)
        expected = {item["draft_id"]: item for item in manifest["items"]}
        for item in facts["items"]:
            original = expected.get(item["draft_id"])
            if original is None:
                continue
            if item.get("description") == safe_csv_text(original["description"]):
                item["description"] = original["description"]
            lines = {line["ordinal"]: line for line in original["lines"]}
            for line in item["lines"] or []:
                wanted = lines.get(line["ordinal"])
                if wanted is not None and line.get("account_code") == safe_csv_text(
                    wanted["account_code"]
                ):
                    line["account_code"] = wanted["account_code"]

    @staticmethod
    def _parse_csv(content):
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
        if (
            not reader.fieldnames
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or set(reader.fieldnames) - set(CSV_FIELDS)
            or not {"draft_id", "status"} <= set(reader.fieldnames)
        ):
            raise ValueError("invalid CSV header")
        envelopes, grouped = {}, {}
        row_count = 0
        for row in reader:
            row_count += 1
            if row_count > 10_000 or None in row or any(value is None for value in row.values()):
                raise ValueError("invalid CSV bounds")
            for key in ("book_id", "company_id", "period", "currency"):
                value = row.get(key) or None
                if key in envelopes and envelopes[key] != value:
                    raise ValueError("mixed CSV scope")
                envelopes[key] = value
            key = (row["draft_id"], row.get("external_voucher_id") or None, row["status"])
            values = {
                name: row.get(name) or None
                for name in (
                    "draft_id",
                    "external_voucher_id",
                    "status",
                    "date",
                    "amount",
                    "description",
                )
            }
            if key not in grouped:
                grouped[key] = {**values, "lines": None}
            elif any(grouped[key][name] != value for name, value in values.items()):
                raise ValueError("inconsistent voucher rows")
            if any(
                row.get(name)
                for name in ("ordinal", "account_code", "debit", "credit", "auxiliary_values")
            ):
                line = {name: row.get(name) or None for name in ("account_code", "debit", "credit")}
                line["ordinal"] = int(row["ordinal"]) if row.get("ordinal") else None
                line["auxiliary_values"] = (
                    json.loads(row["auxiliary_values"]) if row.get("auxiliary_values") else None
                )
                if grouped[key]["lines"] is None:
                    grouped[key]["lines"] = []
                grouped[key]["lines"].append(line)
        return {**envelopes, "items": list(grouped.values())}

    def template(self, actor, book_id, export_id, format="json"):
        value = self.manifest(actor, book_id, export_id)
        if value["status"] != "captured":
            raise AccountingError(
                "旧导出没有完整历史清单，请先人工核对原 CSV；不能生成伪历史回执模板", 409
            )
        body = {key: value[key] for key in ("book_id", "company_id", "period", "currency")}
        body["items"] = [
            {key: item[key] for key in ("draft_id", "date", "amount", "description", "lines")}
            | {"external_voucher_id": None, "status": "unknown"}
            for item in value["items"]
        ]
        if format == "json":
            return json.dumps(body, ensure_ascii=False, indent=2)
        if format != "csv":
            raise AccountingError("模板格式无效")
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS, lineterminator="\r\n")
        writer.writeheader()
        for item in body["items"]:
            for line in item["lines"]:
                row = (
                    {key: body[key] for key in ("book_id", "company_id", "period", "currency")}
                    | {
                        key: item[key]
                        for key in (
                            "draft_id",
                            "external_voucher_id",
                            "status",
                            "date",
                            "amount",
                            "description",
                        )
                    }
                    | line
                )
                row["auxiliary_values"] = encode(row["auxiliary_values"])
                writer.writerow(
                    {
                        key: safe_csv_text(value) if isinstance(value, str) else value
                        for key, value in row.items()
                    }
                )
        return "\ufeff" + out.getvalue()

    def add_note(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            receipt = conn.execute(
                "SELECT * FROM delivery_receipts WHERE id=? AND book_id=?",
                (request.receipt_id, book_id),
            ).fetchone()
            if not receipt:
                raise AccountingError("回执不存在或无权访问", 404)
            _, export = self._export(conn, actor, book_id, receipt["export_id"])
            result = self._receipt_json(conn, actor, export, receipt)
            item = next(
                (item for item in result["items"] if item["draft_id"] == request.draft_id), None
            )
            if not item or request.issue_key not in item["issues"] + item["missing_fields"]:
                raise AccountingError("处理记录须对应当前真实差异或待核验字段", 409)
            digest = fingerprint(
                {"book_id": book_id, "request": request.model_dump(exclude={"idempotency_key"})}
            )
            prior = conn.execute(
                "SELECT id,request_fingerprint FROM delivery_notes WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("处理记录幂等键已用于不同内容", 409)
            else:
                conn.execute(
                    "INSERT INTO delivery_notes(id,receipt_id,book_id,firm_id,company_id,draft_id,issue_key,reason,request_fingerprint,idempotency_key,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        request.receipt_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        request.draft_id,
                        request.issue_key,
                        request.reason,
                        digest,
                        request.idempotency_key,
                        actor.user_id,
                        now(),
                    ),
                )
                self._audit(
                    conn,
                    actor,
                    book_id,
                    "delivery.discrepancy.note",
                    request.receipt_id,
                    {
                        "draft_id": request.draft_id,
                        "issue_key": request.issue_key,
                        "reason": request.reason,
                    },
                )
            return self._receipt_json(conn, actor, export, receipt)
