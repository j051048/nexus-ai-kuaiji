"""T+ file bridge. Imported records are references, never new journal sources.

No verified customer product/version is configured yet. Live submission fails
closed; the durable intent and frozen mapping package remain reviewable.
"""

import csv
import hashlib
import io
import json
from uuid import uuid4
from pydantic import ValidationError
from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError
from app.accounting.queries import page_bounds
from app.accounting.service import fingerprint, now
from app.delivery.service import DeliveryService
from .models import BridgeRecord


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FinancialBridgeService:
    def __init__(self, db):
        self.db, self.access, self.delivery = db, AccessControl(), DeliveryService(db)

    def _scope(self, conn, actor, book_id, write=False):
        book = self.access.book(conn, actor, book_id, "review" if write else "read")
        row = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=? AND firm_id=?",
            (book_id, actor.firm_id),
        ).fetchone()
        if not row:
            raise AccountingError("请先建立长期账套与期间关联", 409)
        return book, row["ledger_id"]

    @staticmethod
    def _configuration(conn, ledger_id):
        return conn.execute(
            "SELECT * FROM bridge_configurations WHERE ledger_id=? ORDER BY version DESC LIMIT 1",
            (ledger_id,),
        ).fetchone()

    def overview(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id)
            config = self._configuration(conn, ledger_id)
            count = conn.execute(
                "SELECT COUNT(*) FROM bridge_import_previews WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            imports = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,state,created_at,record_count,reason FROM bridge_import_previews WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (book_id, limit, offset),
                )
            ]
            intents = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,export_id,configuration_version,state,created_at,external_code FROM bridge_outbox WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (book_id, limit, offset),
                )
            ]
            intent_count = conn.execute(
                "SELECT COUNT(*) FROM bridge_outbox WHERE book_id=?", (book_id,)
            ).fetchone()[0]
            return {
                "book_id": book_id,
                "ledger_id": ledger_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "live_enabled": False,
                "mode": "file_bridge",
                "provider": "chanjet_tplus",
                "capabilities": {
                    "reference_import": True,
                    "frozen_package": True,
                    "live_read": False,
                    "live_write": False,
                },
                "configuration": {
                    **json.loads(config["payload_json"]),
                    "version": config["version"],
                }
                if config
                else None,
                "can_configure": bool(
                    book["permissions"]["review"]
                    and conn.execute(
                        "SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?",
                        (ledger_id, actor.user_id),
                    ).fetchone()
                ),
                "can_review": book["permissions"]["review"],
                "imports": imports,
                "outbox": intents,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "total": max(count, intent_count),
                    "has_more": offset + limit < max(count, intent_count),
                },
            }

    def configure(self, actor, book_id, request):
        payload = request.model_dump(exclude={"expected_version", "idempotency_key"})
        digest = fingerprint(
            {"book_id": book_id, "expected_version": request.expected_version, "payload": payload}
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            if not conn.execute(
                "SELECT 1 FROM ledger_managers WHERE ledger_id=? AND user_id=?",
                (ledger_id, actor.user_id),
            ).fetchone():
                raise AccountingError("修改跨月份的软件映射需要长期账套管理授权", 403)
            prior = conn.execute(
                "SELECT * FROM bridge_configurations WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["ledger_id"] != ledger_id or prior["request_fingerprint"] != digest:
                    raise AccountingError("配置编号已用于不同内容", 409)
                return {"version": prior["version"], "live_enabled": False}
            current = self._configuration(conn, ledger_id)
            if (current["version"] if current else 0) != request.expected_version:
                raise AccountingError("映射配置已更新，请刷新后重试", 409)
            if (
                current
                and json.loads(current["payload_json"])["external_book_id"]
                != request.external_book_id
            ):
                used = (
                    conn.execute(
                        "SELECT accounting.bridge_binding_used(?)", (ledger_id,)
                    ).fetchone()[0]
                    if self.db.is_postgres
                    else bool(
                        conn.execute(
                            "SELECT 1 FROM bridge_reference_records WHERE ledger_id=? LIMIT 1",
                            (ledger_id,),
                        ).fetchone()
                        or conn.execute(
                            "SELECT 1 FROM bridge_outbox WHERE ledger_id=? LIMIT 1", (ledger_id,)
                        ).fetchone()
                    )
                )
                if used:
                    raise AccountingError("已有参考记录或交付意图，不能重新绑定外部账套", 409)
            codes = {
                r["code"]
                for r in conn.execute(
                    "SELECT code FROM accounts WHERE book_id=? AND active=1", (book_id,)
                )
            }
            if any(x.local_code not in codes for x in request.accounts):
                raise AccountingError("本地科目必须属于当前账套且处于启用状态")
            version = request.expected_version + 1
            conn.execute(
                "INSERT INTO bridge_configurations(ledger_id,version,book_id,firm_id,company_id,payload_json,request_fingerprint,idempotency_key,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    ledger_id,
                    version,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    encode(payload),
                    digest,
                    request.idempotency_key,
                    actor.user_id,
                    now(),
                ),
            )
            return {"version": version, "live_enabled": False}

    def template(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            config = self._configuration(conn, ledger_id)
            if not config:
                raise AccountingError("请先配置外部账套标识与映射", 409)
            return {
                "schema": "hengzhang.tplus.reference.v1",
                "book_id": book_id,
                "company_id": book["company_id"],
                "external_book_id": json.loads(config["payload_json"])["external_book_id"],
                "period": book["period"],
                "records": [
                    {
                        "kind": "account",
                        "external_id": "REPLACE-ME",
                        "code": "1002",
                        "name": "银行存款",
                        "period": book["period"],
                        "debit": "0.00",
                        "credit": "0.00",
                        "source_ref": "填写客户导出文件及行号",
                    }
                ],
            }

    @staticmethod
    def _parse(request, book, config):
        if len(request.content.encode("utf-8")) > 2_000_000:
            raise AccountingError("接入文件最多 2 MB", 413)
        try:
            if request.format == "json":
                envelope = json.loads(request.content.lstrip("\ufeff"))
                if not isinstance(envelope, dict) or set(envelope) != {
                    "schema",
                    "book_id",
                    "company_id",
                    "external_book_id",
                    "period",
                    "records",
                }:
                    raise ValueError("scope envelope required")
                records = envelope.pop("records")
            else:
                reader = csv.DictReader(io.StringIO(request.content.lstrip("\ufeff")), strict=True)
                fields = {"book_id", "company_id", "external_book_id", *BridgeRecord.model_fields}
                if (
                    not reader.fieldnames
                    or set(reader.fieldnames) != fields
                    or len(reader.fieldnames) != len(fields)
                ):
                    raise ValueError("invalid CSV fields")
                rows = []
                for row in reader:
                    if len(rows) >= 1000 or None in row or any(v is None for v in row.values()):
                        raise ValueError("invalid CSV bounds")
                    rows.append(row)
                if not rows or any(
                    (r["book_id"], r["company_id"], r["external_book_id"])
                    != (book["id"], book["company_id"], config["external_book_id"])
                    for r in rows
                ):
                    raise ValueError("mixed scope")
                envelope = {
                    "schema": "hengzhang.tplus.reference.v1",
                    "book_id": book["id"],
                    "company_id": book["company_id"],
                    "period": book["period"],
                    "external_book_id": config["external_book_id"],
                }
                records = [
                    {
                        k: v
                        for k, v in r.items()
                        if k not in ("book_id", "company_id", "external_book_id")
                    }
                    for r in rows
                ]
            expected = {
                "schema": "hengzhang.tplus.reference.v1",
                "book_id": book["id"],
                "company_id": book["company_id"],
                "period": book["period"],
                "external_book_id": config["external_book_id"],
            }
            if (
                envelope != expected
                or not isinstance(records, list)
                or not 1 <= len(records) <= 1000
            ):
                raise ValueError("invalid scope or count")
            parsed = [BridgeRecord.model_validate(r).model_dump() for r in records]
            if any(r["period"] != book["period"] for r in parsed) or len(
                {(r["kind"], r["external_id"], r["period"]) for r in parsed}
            ) != len(parsed):
                raise ValueError("mixed periods or repeated ids")
            return parsed
        except (ValueError, TypeError, KeyError, csv.Error, ValidationError) as error:
            raise AccountingError(
                "文件格式或企业范围不匹配，请先转换为本账套的中性 JSON / CSV 模板"
            ) from error

    def preview_import(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            config = self._configuration(conn, ledger_id)
            if not config:
                raise AccountingError("请先配置外部账套", 409)
            records = self._parse(request, book, json.loads(config["payload_json"]))
            digest = fingerprint(
                {
                    "book_id": book_id,
                    "configuration_version": config["version"],
                    "records": records,
                    "reason": request.reason,
                    "raw_hash": hashlib.sha256(request.content.encode()).hexdigest(),
                }
            )
            prior = conn.execute(
                "SELECT * FROM bridge_import_previews WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["book_id"] != book_id or prior["fingerprint"] != digest:
                    raise AccountingError("导入编号已用于不同内容", 409)
                return self._preview(conn, prior)
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO bridge_import_previews(id,ledger_id,configuration_version,book_id,firm_id,company_id,records_json,record_count,fingerprint,idempotency_key,reason,created_by,created_at,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    ledger_id,
                    config["version"],
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    encode(records),
                    len(records),
                    digest,
                    request.idempotency_key,
                    request.reason,
                    actor.user_id,
                    now(),
                    "preview",
                ),
            )
            return self._preview(
                conn,
                conn.execute(
                    "SELECT * FROM bridge_import_previews WHERE id=?", (identifier,)
                ).fetchone(),
            )

    @staticmethod
    def _preview(conn, row):
        duplicates = conflicts = 0
        records = json.loads(row["records_json"])
        for record in records:
            prior = conn.execute(
                "SELECT record_json FROM bridge_reference_records WHERE ledger_id=? AND kind=? AND external_id=? AND period=?",
                (row["ledger_id"], record["kind"], record["external_id"], record["period"]),
            ).fetchone()
            if prior:
                duplicates += json.loads(prior[0]) == record
                conflicts += json.loads(prior[0]) != record
        return {
            "id": row["id"],
            "fingerprint": row["fingerprint"],
            "state": row["state"],
            "record_count": len(records),
            "duplicates": duplicates,
            "conflicts": conflicts,
            "records": records,
            "evidence_mode": "manually_supplied_file",
            "creates_journal_sources": False,
        }

    def get_preview(self, actor, book_id, preview_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM bridge_import_previews WHERE id=? AND book_id=?",
                (preview_id, book_id),
            ).fetchone()
            if not row:
                raise AccountingError("接入预览不存在或无权访问", 404)
            return self._preview(conn, row)

    def confirm_import(self, actor, book_id, preview_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            row = conn.execute(
                "SELECT * FROM bridge_import_previews WHERE id=? AND book_id=?",
                (preview_id, book_id),
            ).fetchone()
            if not row:
                raise AccountingError("接入预览不存在或无权访问", 404)
            if row["fingerprint"] != request.fingerprint:
                raise AccountingError("预览指纹不一致", 409)
            if row["ledger_id"] != ledger_id:
                raise AccountingError("该期间的长期账套关联已变更，请重新预览", 409)
            if row["state"] == "confirmed":
                return self._preview(conn, row)
            config = self._configuration(conn, ledger_id)
            if not config or config["version"] != row["configuration_version"]:
                raise AccountingError("映射配置已变化，请重新预览", 409)
            if self._preview(conn, row)["conflicts"]:
                raise AccountingError(
                    "外部记录编号与已保留内容冲突，请人工核对，原记录不会覆盖", 409
                )
            for record in json.loads(row["records_json"]):
                conn.execute(
                    "INSERT INTO bridge_reference_records(id,ledger_id,book_id,firm_id,company_id,preview_id,kind,external_id,period,record_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ledger_id,kind,external_id,period) DO NOTHING",
                    (
                        uuid4().hex,
                        ledger_id,
                        book_id,
                        actor.firm_id,
                        book["company_id"],
                        preview_id,
                        record["kind"],
                        record["external_id"],
                        record["period"],
                        encode(record),
                        now(),
                    ),
                )
            conn.execute(
                "UPDATE bridge_import_previews SET state='confirmed' WHERE id=? AND book_id=?",
                (preview_id, book_id),
            )
            return self._preview(
                conn,
                conn.execute(
                    "SELECT * FROM bridge_import_previews WHERE id=?", (preview_id,)
                ).fetchone(),
            )

    def prepare_export(self, actor, book_id, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            config = self._configuration(conn, ledger_id)
            existing = conn.execute(
                "SELECT * FROM bridge_outbox WHERE export_id=? AND book_id=?",
                (request.export_id, book_id),
            ).fetchone()
            digest = fingerprint(request.model_dump(exclude={"idempotency_key"}))
            if existing:
                if (
                    existing["configuration_version"] != request.configuration_version
                    or existing["request_fingerprint"] != digest
                ):
                    raise AccountingError("该导出已有冻结交付意图，不能重新映射或重复创建", 409)
                return self._outbox(existing)
            prior = conn.execute(
                "SELECT id FROM bridge_outbox WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                raise AccountingError("交付编号已用于另一导出", 409)
            if not config or config["version"] != request.configuration_version:
                raise AccountingError("映射配置已变化，请刷新后核对", 409)
            _, export = self.delivery._export(conn, actor, book_id, request.export_id, True)
            manifest = self.delivery._manifest(conn, export)
            if manifest["status"] == "pending_legacy":
                raise AccountingError("请先在交付工作台核验并补建历史冻结清单", 409)
            mapping = json.loads(config["payload_json"])
            accounts = {x["local_code"]: x["external_code"] for x in mapping["accounts"]}
            auxiliaries = {
                (x["kind"], x["local_value"]): x["external_code"] for x in mapping["auxiliaries"]
            }
            vouchers = []
            for item in manifest["items"]:
                lines = []
                for line in item["lines"]:
                    if line["account_code"] not in accounts or any(
                        (kind, value) not in auxiliaries
                        for kind, value in line["auxiliary_values"].items()
                    ):
                        raise AccountingError(
                            "已审核凭证使用的科目或辅助项缺少外部映射，请补齐后重新准备", 409
                        )
                    lines.append(
                        {
                            **line,
                            "local_account_code": line["account_code"],
                            "account_code": accounts[line["account_code"]],
                            "auxiliary_values": {
                                kind: auxiliaries[kind, value]
                                for kind, value in line["auxiliary_values"].items()
                            },
                        }
                    )
                vouchers.append(
                    {
                        **item,
                        "external_code": fingerprint({"book": book_id, "draft": item["draft_id"]})[
                            :40
                        ],
                        "lines": lines,
                    }
                )
            package = {
                "schema": "hengzhang.tplus.delivery.v1",
                "provider": "chanjet_tplus",
                "live_enabled": False,
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "external_book_id": mapping["external_book_id"],
                "configuration_version": config["version"],
                "export_id": export["id"],
                "manifest_fingerprint": fingerprint(manifest),
                "vouchers": vouchers,
            }
            identifier = uuid4().hex
            conn.execute(
                "INSERT INTO bridge_outbox(id,ledger_id,configuration_version,export_id,book_id,firm_id,company_id,external_code,package_json,request_fingerprint,idempotency_key,reason,created_by,created_at,state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    ledger_id,
                    config["version"],
                    export["id"],
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    fingerprint({"book": book_id, "export": export["id"]})[:40],
                    encode(package),
                    digest,
                    request.idempotency_key,
                    request.reason,
                    actor.user_id,
                    now(),
                    "awaiting_adapter",
                ),
            )
            return self._outbox(
                conn.execute("SELECT * FROM bridge_outbox WHERE id=?", (identifier,)).fetchone()
            )

    @staticmethod
    def _outbox(row):
        return {
            "id": row["id"],
            "export_id": row["export_id"],
            "state": row["state"],
            "external_code": row["external_code"],
            "package": json.loads(row["package_json"]),
            "live_enabled": False,
            "automatic_resend": False,
        }

    def get_outbox(self, actor, book_id, intent_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM bridge_outbox WHERE id=? AND book_id=?", (intent_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("交付意图不存在或无权访问", 404)
            return self._outbox(row)

    def dispatch(self, actor, book_id, intent_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id, True)
            if not conn.execute(
                "SELECT id FROM bridge_outbox WHERE id=? AND book_id=?", (intent_id, book_id)
            ).fetchone():
                raise AccountingError("交付意图不存在或无权访问", 404)
        raise AccountingError(
            "T+ 具体产品版本与授权尚未验证，真实发送未启用；请使用冻结交付包及导入回执核验流程", 409
        )
