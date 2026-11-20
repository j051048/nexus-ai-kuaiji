"""T+ file bridge. Imported records are references, never new journal sources.

No verified customer product/version is configured yet. Live submission fails
closed; the durable intent and frozen mapping package remain reviewable.
"""

import csv
import base64
import binascii
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
from . import tplus54
from .models import (
    BridgeManualReturn,
    BridgeRecord,
    BridgeReceiptImport,
    BridgeReturnImport,
    BridgeReturnRowHandle,
)
from .roundtrip import parse_return_file


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


NEUTRAL_USAGE = "按 column_mapping 把该软件的导出文件整理为 neutral.records，再通过“预览导入”上传。"
VENDOR_REFERENCES = {
    "yonyou_tplus": {
        "label": "用友 T+（畅捷通）",
        "notes": [
            "在 T+ 总账中按期间导出“科目余额表”和“凭证列表”，导出为 Excel 后另存为 CSV。",
            "余额表的期初/本期发生/期末列按借贷方向分别对应 neutral 的 debit/credit；一次导入只放一个期间。",
        ],
        "column_mapping": {
            "balance": {
                "vendor_columns": [
                    "科目编码",
                    "科目名称",
                    "期初余额",
                    "本期借方发生",
                    "本期贷方发生",
                    "期末余额",
                ],
                "neutral_fields": {
                    "kind": "balance",
                    "code": "科目编码",
                    "name": "科目名称",
                    "debit": "本期借方发生",
                    "credit": "本期贷方发生",
                    "period": "导出期间，统一填 YYYY-MM",
                    "source_ref": "导出文件名 + 行号",
                },
            },
            "voucher": {
                "vendor_columns": [
                    "凭证日期",
                    "凭证字号",
                    "摘要",
                    "科目编码",
                    "借方金额",
                    "贷方金额",
                ],
                "neutral_fields": {
                    "kind": "voucher",
                    "external_id": "凭证字号",
                    "code": "科目编码",
                    "debit": "借方金额",
                    "credit": "贷方金额",
                    "source_ref": "摘要 + 行号",
                },
            },
        },
    },
    "kingdee_jingdouyun": {
        "label": "金蝶精斗云（云会计）",
        "notes": [
            "在精斗云“账簿-科目余额表/凭证”导出 Excel 后另存为 CSV；余额表按“本期发生额”两列取 debit/credit。",
        ],
        "column_mapping": {
            "balance": {
                "vendor_columns": [
                    "科目代码",
                    "科目名称",
                    "期初余额",
                    "本期发生额借方",
                    "本期发生额贷方",
                    "期末余额",
                ],
                "neutral_fields": {
                    "kind": "balance",
                    "code": "科目代码",
                    "name": "科目名称",
                    "debit": "本期发生额借方",
                    "credit": "本期发生额贷方",
                    "period": "导出期间，统一填 YYYY-MM",
                    "source_ref": "导出文件名 + 行号",
                },
            },
            "voucher": {
                "vendor_columns": [
                    "凭证日期",
                    "凭证字",
                    "凭证号",
                    "摘要",
                    "科目代码",
                    "借方金额",
                    "贷方金额",
                ],
                "neutral_fields": {
                    "kind": "voucher",
                    "external_id": "凭证字 + 凭证号",
                    "code": "科目代码",
                    "debit": "借方金额",
                    "credit": "贷方金额",
                    "source_ref": "摘要 + 行号",
                },
            },
        },
    },
    "kingdee_yunxingchen": {
        "label": "金蝶云·星辰",
        "notes": [
            "星辰的科目余额表带“方向”列，请按方向把余额换算为借/贷列再对应 neutral 的 debit/credit。",
            "凭证导出使用“借方本位币金额/贷方本位币金额”两列。",
        ],
        "column_mapping": {
            "balance": {
                "vendor_columns": [
                    "科目编码",
                    "科目名称",
                    "方向",
                    "期初余额",
                    "本期发生额借方",
                    "本期发生额贷方",
                    "期末余额",
                ],
                "neutral_fields": {
                    "kind": "balance",
                    "code": "科目编码",
                    "name": "科目名称",
                    "debit": "本期发生额借方（按方向列核对）",
                    "credit": "本期发生额贷方（按方向列核对）",
                    "period": "导出期间，统一填 YYYY-MM",
                    "source_ref": "导出文件名 + 行号",
                },
            },
            "voucher": {
                "vendor_columns": [
                    "凭证日期",
                    "凭证字",
                    "凭证号",
                    "摘要",
                    "科目编码",
                    "借方本位币金额",
                    "贷方本位币金额",
                ],
                "neutral_fields": {
                    "kind": "voucher",
                    "external_id": "凭证字 + 凭证号",
                    "code": "科目编码",
                    "debit": "借方本位币金额",
                    "credit": "贷方本位币金额",
                    "source_ref": "摘要 + 行号",
                },
            },
        },
    },
    "ningmengyun": {
        "label": "柠檬云财务",
        "notes": [
            "柠檬云“账簿-科目余额表/凭证汇总”导出 Excel 后另存为 CSV；余额表的本期借方/贷方直接对应 debit/credit。",
        ],
        "column_mapping": {
            "balance": {
                "vendor_columns": [
                    "科目编码",
                    "科目名称",
                    "期初余额",
                    "本期借方",
                    "本期贷方",
                    "期末余额",
                ],
                "neutral_fields": {
                    "kind": "balance",
                    "code": "科目编码",
                    "name": "科目名称",
                    "debit": "本期借方",
                    "credit": "本期贷方",
                    "period": "导出期间，统一填 YYYY-MM",
                    "source_ref": "导出文件名 + 行号",
                },
            },
            "voucher": {
                "vendor_columns": ["日期", "凭证号", "摘要", "科目编码", "借方金额", "贷方金额"],
                "neutral_fields": {
                    "kind": "voucher",
                    "external_id": "凭证号",
                    "code": "科目编码",
                    "debit": "借方金额",
                    "credit": "贷方金额",
                    "source_ref": "摘要 + 行号",
                },
            },
        },
    },
}


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

    def _company_for_book(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book, _ = self._scope(conn, actor, book_id, False)
            return book["company_id"]

    @staticmethod
    def _configuration(conn, ledger_id):
        return conn.execute(
            "SELECT * FROM bridge_configurations WHERE ledger_id=? ORDER BY version DESC LIMIT 1",
            (ledger_id,),
        ).fetchone()

    @staticmethod
    def _external_state(conn, outbox_id, book_id):
        artifacts = conn.execute(
            "SELECT id,created_at FROM bridge_export_artifacts WHERE outbox_id=? AND book_id=? ORDER BY created_at,id",
            (outbox_id, book_id),
        ).fetchall()
        receipts = conn.execute(
            "SELECT r.* FROM bridge_export_receipts r JOIN bridge_export_artifacts a ON a.id=r.artifact_id "
            "WHERE a.outbox_id=? AND a.book_id=? ORDER BY r.created_at,r.id",
            (outbox_id, book_id),
        ).fetchall()
        returns = conn.execute(
            "SELECT r.* FROM bridge_return_imports r JOIN bridge_export_artifacts a ON a.id=r.artifact_id "
            "WHERE a.outbox_id=? AND a.book_id=? ORDER BY r.created_at,r.id",
            (outbox_id, book_id),
        ).fetchall()
        accepted = sum(int(row["accepted_count"]) for row in receipts)
        rejected = sum(int(row["rejected_count"]) for row in receipts)
        posted = [row for row in receipts if bool(row["external_posting"])]
        posted += [row for row in returns if bool(row["posted"]) and bool(row["evidence_verified"])]
        received_rows = [
            row
            for row in receipts
            if int(row["accepted_count"]) > 0 or int(row["duplicate_count"]) > 0
        ]
        received_rows += [row for row in returns if bool(row["received"])]
        received = bool(received_rows)
        discrepancy = sum(int(row["discrepancy_count"]) for row in returns)
        last_received = (
            sorted(received_rows, key=lambda row: (row["created_at"], row["id"]))[-1]
            if received_rows
            else None
        )
        last_posted = (
            sorted(posted, key=lambda row: (row["created_at"], row["id"]))[-1] if posted else None
        )
        first_artifact = artifacts[0] if artifacts else None
        statuses = {
            "exported": {
                "at": first_artifact["created_at"] if first_artifact else None,
                "evidence": first_artifact["id"] if first_artifact else None,
            },
            "received": {
                "at": last_received["created_at"] if last_received else None,
                "evidence": last_received["id"] if last_received else None,
            },
            "posted": {
                "at": last_posted["created_at"] if last_posted else None,
                "evidence": last_posted["id"] if last_posted else None,
            },
        }
        if posted:
            summary = "posted"
        elif received and (rejected or discrepancy):
            summary = "partial"
        elif received:
            summary = "received"
        elif artifacts:
            summary = "result_unknown"
        else:
            summary = "not_exported"
        return {
            "statuses": statuses,
            "result_summary": summary,
            "partial_failure": bool(received and (rejected or discrepancy)),
            "result_unknown": bool(artifacts) and not receipts and not returns,
        }

    def overview(self, actor, book_id, limit=20, offset=0):
        page_bounds(limit, offset)
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id)
            config = self._configuration(conn, ledger_id)
            policy = conn.execute(
                "SELECT formal_book_system,formal_book_system_name,approved_by,version "
                "FROM accounting_policies WHERE ledger_id=? AND status='active'",
                (ledger_id,),
            ).fetchone()
            formal_book = {
                "system": policy["formal_book_system"] if policy else "unassessed",
                "name": policy["formal_book_system_name"] if policy else "",
                "policy_version": policy["version"] if policy else None,
                "approved": bool(policy["approved_by"]) if policy else False,
            }
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
            intents = []
            for r in conn.execute(
                "SELECT id,export_id,configuration_version,state,created_at,external_code FROM bridge_outbox WHERE book_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (book_id, limit, offset),
            ):
                intents.append({**dict(r), **self._external_state(conn, r["id"], book_id)})
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
                "formal_book": formal_book,
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

    def template(self, actor, book_id, vendor=None):
        with self.db.transaction(actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            config = self._configuration(conn, ledger_id)
            if not config:
                raise AccountingError("请先配置外部账套标识与映射", 409)
            neutral = {
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
        if vendor in (None, "", "neutral"):
            return neutral
        profile = VENDOR_REFERENCES.get(vendor)
        if not profile:
            raise AccountingError("暂不支持该软件的导出参考，请使用中性模板", 404)
        return {
            "schema": "hengzhang.vendor-reference.v1",
            "vendor": vendor,
            "label": profile["label"],
            "notes": profile["notes"],
            "column_mapping": profile["column_mapping"],
            "neutral": neutral,
            "usage": NEUTRAL_USAGE,
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
            return {**self._outbox(row), **self._external_state(conn, intent_id, book_id)}

    def _frozen_package(self, conn, actor, book_id, outbox_row):
        package = json.loads(outbox_row["package_json"])
        _, export = self.delivery._export(conn, actor, book_id, package["export_id"], True)
        manifest = self.delivery._manifest(conn, export)
        if manifest.get("status") == "pending_legacy":
            raise AccountingError("交付清单尚未冻结，不能生成适配文件", 409)
        if fingerprint(manifest) != package.get("manifest_fingerprint"):
            raise AccountingError("冻结清单内容已变化，旧适配文件不能继续使用", 409)
        # Frozen files remain historical evidence; a stale fee source must not
        # be offered as a currently usable export or accepted receipt.
        from app.tax.fee_guard import fee_context

        for item in manifest["items"]:
            source = conn.execute(
                "SELECT s.* FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE d.id=? AND d.book_id=?",
                (item["draft_id"], book_id),
            ).fetchone()
            if source:
                lines = conn.execute(
                    "SELECT * FROM draft_lines WHERE draft_id=? AND book_id=? ORDER BY ordinal",
                    (item["draft_id"], book_id),
                ).fetchall()
                _, issues = fee_context(conn, source, lines)
                if issues:
                    raise AccountingError(
                        "规费来源已失效，旧适配文件仅保留历史证据，请重新核定", 409
                    )
        return package

    @staticmethod
    def _artifact_view(row):
        return {
            "id": row["id"],
            "outbox_id": row["outbox_id"],
            "profile": row["profile"],
            "filename": row["filename"],
            "content_sha256": row["content_sha256"],
            "manifest_fingerprint": row["manifest_fingerprint"],
            "package_fingerprint": row["package_fingerprint"],
            "row_count": row["row_count"],
            "voucher_count": row["voucher_count"],
            "created_at": row["created_at"],
            "synthetic": True,
            "native_tplus_format": False,
        }

    def create_artifact(self, actor, book_id, intent_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            book, ledger_id = self._scope(conn, actor, book_id, True)
            intent = conn.execute(
                "SELECT * FROM bridge_outbox WHERE id=? AND book_id=?", (intent_id, book_id)
            ).fetchone()
            if not intent:
                raise AccountingError("交付意图不存在或无权访问", 404)
            package = self._frozen_package(conn, actor, book_id, intent)
            content, codes, voucher_count = tplus54.render(package)
            digest = tplus54.content_hash(content)
            prior = conn.execute(
                "SELECT * FROM bridge_export_artifacts WHERE outbox_id=? AND content_sha256=?",
                (intent_id, digest),
            ).fetchone()
            if prior:
                return self._artifact_view(prior)
            identifier = uuid4().hex
            filename = f"tplus54-synthetic-{digest[:12]}.csv"
            meta = {
                "outbox_id": intent_id,
                "content_sha256": digest,
                "manifest_fingerprint": package["manifest_fingerprint"],
                "package_fingerprint": fingerprint(package),
                "profile": tplus54.PROFILE,
            }
            conn.execute(
                "INSERT INTO bridge_export_artifacts(id,outbox_id,ledger_id,book_id,firm_id,company_id,profile,filename,content_sha256,manifest_fingerprint,package_fingerprint,voucher_codes_json,row_count,voucher_count,created_by,created_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(outbox_id,content_sha256) DO NOTHING",
                (
                    identifier,
                    intent_id,
                    ledger_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    tplus54.PROFILE,
                    filename,
                    digest,
                    package["manifest_fingerprint"],
                    meta["package_fingerprint"],
                    tplus54.stable_json(codes),
                    len(content.splitlines()) - 1,
                    voucher_count,
                    actor.user_id,
                    now(),
                    fingerprint(meta),
                ),
            )
            return self._artifact_view(
                conn.execute(
                    "SELECT * FROM bridge_export_artifacts WHERE outbox_id=? AND content_sha256=?",
                    (intent_id, digest),
                ).fetchone()
            )

    def list_artifacts(self, actor, book_id, intent_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id)
            if not conn.execute(
                "SELECT 1 FROM bridge_outbox WHERE id=? AND book_id=?", (intent_id, book_id)
            ).fetchone():
                raise AccountingError("交付意图不存在或无权访问", 404)
            return {
                "artifacts": [
                    self._artifact_view(row)
                    for row in conn.execute(
                        "SELECT * FROM bridge_export_artifacts WHERE outbox_id=? AND book_id=? ORDER BY created_at,id",
                        (intent_id, book_id),
                    )
                ]
            }

    def download_artifact(self, actor, book_id, artifact_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id, True)
            artifact = conn.execute(
                "SELECT * FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone()
            if not artifact:
                raise AccountingError("适配文件不存在或无权访问", 404)
            intent = conn.execute(
                "SELECT * FROM bridge_outbox WHERE id=? AND book_id=?",
                (artifact["outbox_id"], book_id),
            ).fetchone()
            if not intent:
                raise AccountingError("交付意图不存在或无权访问", 404)
            package = self._frozen_package(conn, actor, book_id, intent)
            content, _, _ = tplus54.render(package)
            if tplus54.content_hash(content) != artifact["content_sha256"]:
                raise AccountingError("来源或冻结清单已变化，旧适配文件不能下载", 409)
            return artifact["filename"], content

    @staticmethod
    def _receipt_view(conn, row):
        rows = [
            dict(item)
            for item in conn.execute(
                "SELECT row_number,voucher_external_code,status,reason FROM bridge_export_receipt_rows WHERE receipt_id=? ORDER BY row_number",
                (row["id"],),
            )
        ]
        return {
            "id": row["id"],
            "artifact_id": row["artifact_id"],
            "receipt_code": row["receipt_code"],
            "content_sha256": row["content_sha256"],
            "result_status": row["result_status"],
            "row_count": row["row_count"],
            "accepted_count": row["accepted_count"],
            "rejected_count": row["rejected_count"],
            "duplicate_count": row["duplicate_count"],
            "unknown_count": row["unknown_count"],
            "external_posting": bool(row["external_posting"]),
            "created_at": row["created_at"],
            "rows": rows,
            "note": "回执仅登记客户文件结果，不代表 T+ 已独立过账；外发仍关闭。",
        }

    def record_receipt(self, actor, book_id, artifact_id, request: BridgeReceiptImport):
        raw = request.content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.db.transaction(write=True, actor=actor) as conn:
            book, _ = self._scope(conn, actor, book_id, True)
            artifact = conn.execute(
                "SELECT * FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone()
            if not artifact:
                raise AccountingError("适配文件不存在或无权访问", 404)
            intent = conn.execute(
                "SELECT * FROM bridge_outbox WHERE id=? AND book_id=?",
                (artifact["outbox_id"], book_id),
            ).fetchone()
            if not intent:
                raise AccountingError("交付意图不存在或无权访问", 404)
            package = self._frozen_package(conn, actor, book_id, intent)
            current, _, _ = tplus54.render(package)
            if tplus54.content_hash(current) != artifact["content_sha256"]:
                raise AccountingError("来源或冻结清单已变化，不能接纳旧文件回执", 409)
            prior = conn.execute(
                "SELECT * FROM bridge_export_receipts WHERE artifact_id=? AND (receipt_code=? OR content_sha256=?)",
                (artifact_id, request.receipt_code, digest),
            ).fetchone()
            if prior:
                if (
                    prior["receipt_code"] != request.receipt_code
                    or prior["content_sha256"] != digest
                ):
                    raise AccountingError("回执编号已用于不同内容，原记录不会覆盖", 409)
                return self._receipt_view(conn, prior)
            rows = tplus54.parse_result(raw, json.loads(artifact["voucher_codes_json"]))
            status, counts = tplus54.result_summary(rows)
            identifier = uuid4().hex
            meta = {
                "artifact_id": artifact_id,
                "receipt_code": request.receipt_code,
                "content_sha256": digest,
                "result_status": status,
                "counts": counts,
            }
            inserted = conn.execute(
                "INSERT INTO bridge_export_receipts(id,artifact_id,book_id,firm_id,company_id,receipt_code,content_sha256,result_status,row_count,accepted_count,rejected_count,duplicate_count,unknown_count,external_posting,created_by,created_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (
                    identifier,
                    artifact_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    request.receipt_code,
                    digest,
                    status,
                    len(rows),
                    counts["accepted"],
                    counts["rejected"],
                    counts["duplicate"],
                    counts["unknown"],
                    False,
                    actor.user_id,
                    now(),
                    fingerprint(meta),
                ),
            )
            if getattr(inserted, "rowcount", 1) == 0:
                prior = conn.execute(
                    "SELECT * FROM bridge_export_receipts WHERE artifact_id=? AND (receipt_code=? OR content_sha256=?)",
                    (artifact_id, request.receipt_code, digest),
                ).fetchone()
                if (
                    prior is None
                    or prior["receipt_code"] != request.receipt_code
                    or prior["content_sha256"] != digest
                ):
                    raise AccountingError("回执写入冲突，请重新核对", 409)
                return self._receipt_view(conn, prior)
            for row in rows:
                conn.execute(
                    "INSERT INTO bridge_export_receipt_rows(receipt_id,row_number,voucher_external_code,status,reason,raw_hash) VALUES(?,?,?,?,?,?)",
                    (
                        identifier,
                        row["row_number"],
                        row["voucher_external_code"],
                        row["status"],
                        row["reason"],
                        hashlib.sha256(tplus54.stable_json(row).encode()).hexdigest(),
                    ),
                )
            return self._receipt_view(
                conn,
                conn.execute(
                    "SELECT * FROM bridge_export_receipts WHERE id=?", (identifier,)
                ).fetchone(),
            )

    def list_receipts(self, actor, book_id, artifact_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id)
            if not conn.execute(
                "SELECT 1 FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone():
                raise AccountingError("适配文件不存在或无权访问", 404)
            return {
                "receipts": [
                    self._receipt_view(conn, row)
                    for row in conn.execute(
                        "SELECT * FROM bridge_export_receipts WHERE artifact_id=? AND book_id=? ORDER BY created_at,id",
                        (artifact_id, book_id),
                    )
                ]
            }

    def attach_evidence(self, actor, book_id, artifact_id, import_id, payload):
        try:
            content = base64.b64decode(payload.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise AccountingError("证据原件内容不是有效 base64", 422)
        if not content or len(content) > 2_000_000:
            raise AccountingError("证据原件须在 2MB 以内", 422)
        if not (
            content.startswith(b"%PDF-")
            or content.startswith(bytes.fromhex("89504e470d0a1a0a"))
            or content.startswith(bytes.fromhex("ffd8ff"))
        ):
            raise AccountingError("证据原件仅接受 PDF/PNG/JPEG", 422)
        company_id = self._company_for_book(actor, book_id)
        digest = hashlib.sha256(content).hexdigest()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, _ledger_id = self._scope(conn, actor, book_id, True)
            if not conn.execute(
                "SELECT 1 FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone():
                raise AccountingError("交付包不存在或无权访问", 404)
            row = conn.execute(
                "SELECT i.* FROM bridge_return_imports i WHERE i.id=? AND i.book_id=? AND i.artifact_id=?",
                (import_id, book_id, artifact_id),
            ).fetchone()
            if not row:
                raise AccountingError("返回文件记录不存在", 404)
            if row["result_status"] != "accepted" or row["row_count"] == 0:
                raise AccountingError("只有已验证的手工回执可补证据原件", 409)
            if conn.execute(
                "SELECT 1 FROM bridge_return_evidence WHERE import_id=?", (import_id,)
            ).fetchone():
                raise AccountingError("该回执已绑定证据原件", 409)
            conn.execute(
                "INSERT INTO bridge_return_evidence(import_id,book_id,firm_id,company_id,filename,media_type,sha256,size_bytes,content,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    book_id,
                    book["firm_id"],
                    book["company_id"],
                    payload.filename[:200],
                    payload.media_type[:100],
                    digest,
                    len(content),
                    content,
                    actor.user_id,
                    now(),
                ),
            )
            conn.execute(
                "UPDATE bridge_return_imports SET evidence_verified=TRUE WHERE id=?",
                (import_id,),
            )
            self.delivery._audit(
                conn, actor, book_id, "bridge.return.evidence", import_id, {"sha256": digest}
            )
            return {
                "import_id": import_id,
                "filename": payload.filename,
                "media_type": payload.media_type,
                "sha256": digest,
                "size_bytes": len(content),
            }

    def evidence_for(self, actor, book_id, import_id, artifact_id=None):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id, False)
            sql = (
                "SELECT e.* FROM bridge_return_evidence e JOIN bridge_return_imports i ON i.id=e.import_id "
                "WHERE e.import_id=? AND e.book_id=?"
            )
            params = [import_id, book_id]
            if artifact_id:
                sql += " AND i.artifact_id=?"
                params.append(artifact_id)
            value = conn.execute(sql, params).fetchone()
            if not value:
                raise AccountingError("回执证据不存在或无权访问", 404)
            return value

    @staticmethod
    def _return_view(conn, row):
        rows = [
            {
                "voucher_code": item["voucher_code"],
                "external_code": item["external_code"],
                "status": item["status"],
                "reason": item["reason"],
                "posted_date": item["posted_date"],
                "expected_cents": item["expected_cents"],
                "actual_cents": item["actual_cents"],
                "expected_accounts": json.loads(item["expected_accounts_json"] or "[]"),
                "actual_accounts": json.loads(item["actual_accounts_json"] or "[]"),
                "handled": handler is not None,
                "handler": (
                    {
                        "action": handler["action"],
                        "reason": handler["reason"],
                        "evidence_ref": handler["evidence_ref"],
                        "handled_by": handler["handled_by"],
                        "handled_at": handler["handled_at"],
                    }
                    if handler
                    else None
                ),
            }
            for item in conn.execute(
                "SELECT * FROM bridge_return_rows WHERE import_id=? ORDER BY voucher_code",
                (row["id"],),
            )
            for handler in [
                conn.execute(
                    "SELECT * FROM bridge_return_row_handlers WHERE import_id=? AND voucher_code=?",
                    (row["id"], item["voucher_code"]),
                ).fetchone()
            ]
        ]
        return {
            "id": row["id"],
            "artifact_id": row["artifact_id"],
            "batch_code": row["batch_code"],
            "content_sha256": row["content_sha256"],
            "result_status": row["result_status"],
            "received": bool(row["received"]),
            "posted": bool(row["posted"]) and bool(row["evidence_verified"]),
            "posted_declared": bool(row["posted"]),
            "evidence_verified": bool(row["evidence_verified"]),
            "row_count": row["row_count"],
            "counts": {
                key: row[f"{key}_count"]
                for key in (
                    "accepted",
                    "rejected",
                    "duplicate",
                    "unknown",
                    "missing",
                    "extra",
                    "mismatch",
                    "discrepancy",
                )
            },
            "created_at": row["created_at"],
            "rows": rows,
            "note": "返回文件只登记客户软件结果；外发仍关闭，手工过账回执另行核验。",
        }

    def record_return(self, actor, book_id, artifact_id, request: BridgeReturnImport):
        company_id = self._company_for_book(actor, book_id)
        raw = request.content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, _ = self._scope(conn, actor, book_id, True)
            artifact = conn.execute(
                "SELECT * FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone()
            if not artifact:
                raise AccountingError("适配文件不存在或无权访问", 404)
            intent = conn.execute(
                "SELECT * FROM bridge_outbox WHERE id=? AND book_id=?",
                (artifact["outbox_id"], book_id),
            ).fetchone()
            if not intent:
                raise AccountingError("交付意图不存在或无权访问", 404)
            if request.batch_code.strip() != str(intent["external_code"]):
                raise AccountingError("批次标识与冻结交付批次不一致，拒绝登记", 422)
            package = self._frozen_package(conn, actor, book_id, intent)
            current, _, _ = tplus54.render(package)
            if tplus54.content_hash(current) != artifact["content_sha256"]:
                raise AccountingError("来源或冻结清单已变化，不能接纳旧返回文件", 409)
            prior = conn.execute(
                "SELECT * FROM bridge_return_imports WHERE artifact_id=? AND (batch_code=? OR content_sha256=?)",
                (artifact_id, request.batch_code, digest),
            ).fetchone()
            if prior:
                if prior["batch_code"] != request.batch_code or prior["content_sha256"] != digest:
                    raise AccountingError("返回文件批次已用于不同内容，原记录不会覆盖", 409)
                return self._return_view(conn, prior)
            exported = []
            for voucher in package.get("vouchers", []):
                external = str(voucher.get("external_code") or "")
                if not external:
                    raise AccountingError("冻结凭证缺少外部编号，拒绝核对", 409)
                amount = 0
                accounts = set()
                for line in voucher.get("lines") or []:
                    amount += int(round(float(line.get("debit") or 0) * 100))
                    if line.get("account_code"):
                        accounts.add(str(line["account_code"]))
                exported.append(
                    {
                        "voucher_code": f"T54-{external}",
                        "amount_cents": amount,
                        "accounts": sorted(accounts),
                    }
                )
            parsed = parse_return_file(raw, batch_code=request.batch_code, exported=exported)
            counts = parsed["counts"]
            import_id = uuid4().hex
            meta = {
                "artifact_id": artifact_id,
                "batch_code": request.batch_code,
                "content_sha256": digest,
                "status": parsed["status"],
                "counts": counts,
            }
            conn.execute(
                "INSERT INTO bridge_return_imports(id,artifact_id,book_id,firm_id,company_id,batch_code,content_sha256,content,result_status,received,posted,evidence_verified,row_count,accepted_count,rejected_count,duplicate_count,unknown_count,missing_count,extra_count,mismatch_count,discrepancy_count,created_by,created_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    artifact_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    request.batch_code,
                    digest,
                    raw,
                    parsed["status"],
                    bool(parsed["received"]),
                    bool(parsed["posted"]),
                    True,
                    len(parsed["rows"]),
                    counts["accepted"],
                    counts["rejected"],
                    counts["duplicate"],
                    counts["unknown"],
                    counts["missing"],
                    counts["extra"],
                    counts["amount_mismatch"] + counts["account_mismatch"],
                    sum(
                        counts[key]
                        for key in (
                            "rejected",
                            "missing",
                            "extra",
                            "amount_mismatch",
                            "account_mismatch",
                            "unknown",
                        )
                    ),
                    actor.user_id,
                    now(),
                    fingerprint(meta),
                ),
            )
            for item in parsed["rows"]:
                expected = next(
                    (e for e in exported if e["voucher_code"] == item["voucher_code"]), None
                )
                conn.execute(
                    "INSERT INTO bridge_return_rows(import_id,voucher_code,external_code,status,reason,posted_date,expected_cents,actual_cents,expected_accounts_json,actual_accounts_json,raw_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        import_id,
                        item["voucher_code"],
                        item["external_code"],
                        item["status"],
                        item["reason"],
                        item["posted_date"],
                        expected["amount_cents"] if expected else None,
                        item["actual_cents"],
                        encode(expected["accounts"] if expected else []),
                        encode(item["accounts"]),
                        hashlib.sha256(encode(item).encode()).hexdigest(),
                    ),
                )
            self.delivery._audit(
                conn,
                actor,
                book_id,
                "bridge.return.import",
                import_id,
                {**meta, "row_count": len(parsed["rows"])},
            )
            return self._return_view(
                conn,
                conn.execute(
                    "SELECT * FROM bridge_return_imports WHERE id=?", (import_id,)
                ).fetchone(),
            )

    def record_manual_return(
        self,
        actor,
        book_id,
        artifact_id,
        request: BridgeManualReturn,
        *,
        evidence: bytes | None = None,
        filename: str = "",
        media_type: str = "",
    ):
        company_id = self._company_for_book(actor, book_id)
        if len(request.evidence_ref.strip()) < 5:
            raise AccountingError("手工登记必须填写可核验的接收或过账证据", 422)
        if request.posted and not request.received:
            raise AccountingError("未登记接收不能直接登记过账", 422)
        if request.posted and not request.posted_date.strip():
            raise AccountingError("登记过账必须填写对方过账日期", 422)
        payload = {
            "manual": True,
            "batch_code": request.batch_code.strip(),
            "received": request.received,
            "posted": request.posted,
            "posted_date": request.posted_date.strip(),
            "evidence_ref": request.evidence_ref.strip(),
            "note": request.note.strip(),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, _ = self._scope(conn, actor, book_id, True)
            artifact = conn.execute(
                "SELECT * FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone()
            if not artifact:
                raise AccountingError("适配文件不存在或无权访问", 404)
            intent = conn.execute(
                "SELECT * FROM bridge_outbox WHERE id=? AND book_id=?",
                (artifact["outbox_id"], book_id),
            ).fetchone()
            if not intent:
                raise AccountingError("交付意图不存在或无权访问", 404)
            if request.batch_code.strip() != intent["external_code"]:
                raise AccountingError("批次标识与冻结交付包不一致", 422)
            package = self._frozen_package(conn, actor, book_id, intent)
            current, _, _ = tplus54.render(package)
            if tplus54.content_hash(current) != artifact["content_sha256"]:
                raise AccountingError("来源或冻结清单已变化，不能登记旧交付结果", 409)
            prior = conn.execute(
                "SELECT * FROM bridge_return_imports WHERE artifact_id=? AND batch_code=?",
                (artifact_id, request.batch_code.strip()),
            ).fetchone()
            if prior:
                if prior["content_sha256"] != digest:
                    raise AccountingError("该批次已有不同的手工回执，原记录不会覆盖", 409)
                return self._return_view(conn, prior)
            import_id = uuid4().hex
            created_at = now()
            conn.execute(
                "INSERT INTO bridge_return_imports(id,artifact_id,book_id,firm_id,company_id,batch_code,content_sha256,content,result_status,received,posted,evidence_verified,row_count,accepted_count,rejected_count,duplicate_count,unknown_count,missing_count,extra_count,mismatch_count,discrepancy_count,created_by,created_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    artifact_id,
                    book_id,
                    book["firm_id"],
                    book["company_id"],
                    request.batch_code.strip(),
                    digest,
                    raw,
                    "accepted",
                    bool(request.received),
                    bool(request.posted),
                    evidence is not None,
                    1,
                    1,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    actor.user_id,
                    created_at,
                    digest,
                ),
            )
            conn.execute(
                "INSERT INTO bridge_return_rows(import_id,voucher_code,external_code,status,reason,posted_date,expected_cents,actual_cents,expected_accounts_json,actual_accounts_json,raw_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    "MANUAL",
                    request.batch_code.strip(),
                    "accepted",
                    f"手工登记整批接收：{request.note.strip() or request.evidence_ref.strip()}",
                    request.posted_date.strip() or None if request.posted else None,
                    None,
                    None,
                    "[]",
                    "[]",
                    digest,
                ),
            )
            if evidence is not None:
                if not evidence or len(evidence) > 2_000_000:
                    raise AccountingError("证据原件须在 2MB 以内", 422)
                if not (
                    evidence.startswith(b"%PDF-")
                    or evidence.startswith(bytes.fromhex("89504e470d0a1a0a"))
                    or evidence.startswith(bytes.fromhex("ffd8ff"))
                ):
                    raise AccountingError("证据原件仅接受 PDF/PNG/JPEG", 422)
                conn.execute(
                    "INSERT INTO bridge_return_evidence(import_id,book_id,firm_id,company_id,filename,media_type,sha256,size_bytes,content,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        import_id,
                        book_id,
                        book["firm_id"],
                        book["company_id"],
                        filename or "手动回执证据",
                        media_type or "application/octet-stream",
                        digest,
                        len(evidence),
                        evidence,
                        actor.user_id,
                        now(),
                    ),
                )
            self.delivery._audit(
                conn,
                actor,
                book_id,
                "bridge.return.manual",
                import_id,
                {
                    "batch_code": request.batch_code.strip(),
                    "received": request.received,
                    "posted": request.posted,
                    "posted_date": request.posted_date.strip(),
                    "evidence_ref": request.evidence_ref.strip(),
                },
            )
            return self._return_view(
                conn,
                conn.execute(
                    "SELECT * FROM bridge_return_imports WHERE id=?", (import_id,)
                ).fetchone(),
            )

    def handle_return_row(
        self,
        actor,
        book_id,
        artifact_id,
        import_id,
        voucher_code,
        request: BridgeReturnRowHandle,
    ):
        company_id = self._company_for_book(actor, book_id)
        reason = request.reason.strip()
        evidence = request.evidence_ref.strip()
        if len(reason) < 3:
            raise AccountingError("差异处置必须说明原因", 422)
        if len(evidence) < 5:
            raise AccountingError("差异处置必须填写可核验的证据", 422)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self._scope(conn, actor, book_id, True)
            artifact = conn.execute(
                "SELECT * FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone()
            if not artifact:
                raise AccountingError("适配文件不存在或无权访问", 404)
            imported = conn.execute(
                "SELECT * FROM bridge_return_imports WHERE id=? AND artifact_id=? AND book_id=?",
                (import_id, artifact_id, book_id),
            ).fetchone()
            if not imported:
                raise AccountingError("返回文件不存在或无权访问", 404)
            row = conn.execute(
                "SELECT * FROM bridge_return_rows WHERE import_id=? AND voucher_code=?",
                (import_id, voucher_code),
            ).fetchone()
            if not row:
                raise AccountingError("返回凭证行不存在", 404)
            if row["status"] in {"accepted", "duplicate"}:
                raise AccountingError("一致凭证无需差异处置", 409)
            existing = conn.execute(
                "SELECT * FROM bridge_return_row_handlers WHERE import_id=? AND voucher_code=?",
                (import_id, voucher_code),
            ).fetchone()
            if existing:
                if (
                    existing["action"] != request.action
                    or existing["reason"] != reason
                    or existing["evidence_ref"] != evidence
                ):
                    raise AccountingError("该凭证差异已处置，原记录不会覆盖", 409)
                return self._return_view(conn, imported)
            conn.execute(
                "INSERT INTO bridge_return_row_handlers(import_id,voucher_code,book_id,firm_id,company_id,action,reason,evidence_ref,handled_by,handled_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    import_id,
                    voucher_code,
                    book_id,
                    imported["firm_id"],
                    imported["company_id"],
                    request.action,
                    reason,
                    evidence,
                    actor.user_id,
                    now(),
                    fingerprint(
                        {
                            "import_id": import_id,
                            "voucher_code": voucher_code,
                            "action": request.action,
                            "reason": reason,
                            "evidence_ref": evidence,
                        }
                    ),
                ),
            )
            self.delivery._audit(
                conn,
                actor,
                book_id,
                "bridge.return.row_handle",
                import_id,
                {
                    "voucher_code": voucher_code,
                    "action": request.action,
                    "reason": reason,
                    "evidence_ref": evidence,
                },
            )
            return self._return_view(conn, imported)

    def list_returns(self, actor, book_id, artifact_id):
        with self.db.transaction(actor=actor) as conn:
            self._scope(conn, actor, book_id)
            if not conn.execute(
                "SELECT 1 FROM bridge_export_artifacts WHERE id=? AND book_id=?",
                (artifact_id, book_id),
            ).fetchone():
                raise AccountingError("适配文件不存在或无权访问", 404)
            return {
                "returns": [
                    self._return_view(conn, row)
                    for row in conn.execute(
                        "SELECT * FROM bridge_return_imports WHERE artifact_id=? AND book_id=? ORDER BY created_at,id",
                        (artifact_id, book_id),
                    )
                ]
            }

    def list_return_tasks(self, actor, book_id, limit=50):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT i.id import_id, i.artifact_id, i.book_id, i.company_id, i.batch_code, i.fingerprint, "
                "a.outbox_id, r.voucher_code, r.status, r.reason, r.expected_cents, r.actual_cents "
                "FROM bridge_return_rows r JOIN bridge_return_imports i ON i.id=r.import_id "
                "JOIN bridge_export_artifacts a ON a.id=i.artifact_id "
                "LEFT JOIN bridge_return_row_handlers h ON h.import_id=r.import_id AND h.voucher_code=r.voucher_code "
                "WHERE i.firm_id=? AND i.book_id=? AND i.company_id=? AND h.import_id IS NULL "
                "AND r.status NOT IN ('accepted','duplicate') "
                "ORDER BY i.created_at, i.id, r.voucher_code LIMIT ?",
                (actor.firm_id, book_id, book["company_id"], max(1, min(int(limit), 200))),
            ).fetchall()
            return [
                {
                    "task_type": "bridge_return",
                    "object_type": "bridge_return",
                    "object_id": f"{row['import_id']}:{row['voucher_code']}",
                    "company_id": row["company_id"],
                    "book_id": row["book_id"],
                    "artifact_id": row["artifact_id"],
                    "outbox_id": row["outbox_id"],
                    "import_id": row["import_id"],
                    "voucher_code": row["voucher_code"],
                    "status": "action",
                    "available_action": "handle_return",
                    "business_action": "handle_return",
                    "task_version": row["voucher_code"] + ":" + row["fingerprint"],
                    "amount_cents": abs(
                        int(row["actual_cents"] or 0) - int(row["expected_cents"] or 0)
                    ),
                    "reason": row["reason"] or row["status"],
                }
                for row in rows
            ]

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
