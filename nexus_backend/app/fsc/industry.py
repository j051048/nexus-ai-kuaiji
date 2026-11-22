"""Batch 06 三家行业专用台账处理器：物业与食材集采、贸易存货、内部医院汇总。

共同纪律（沿用 ``AdvertisingRevenueService`` 的范式）：
只消费**已确认**的共享导入信封；必须有一条**生效**的同处理器契约；产物是绑定
信封 + 契约 + 行号的确定性草稿（fingerprint 可复核、同键重放、来源或契约变化即
stale）；不写凭证、不生成应收、不猜任何税率或计价方法。
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from pydantic import BaseModel, Field

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from .privacy import assert_no_patient_privacy

#: 领域产出走账套「复核」能力：行业能力名（CAPABILITIES）是契约声明，
#: 不是访问层能力，二者不能混用（否则 access.book 会拿未知键去查权限表）。
REVIEW = "review"

#: 已批准的存货计价方法；未在契约里批准就必须 409，不能猜默认。
COSTING_METHODS = ("moving_weighted_average", "fifo")
PROPERTY_KINDS = ("property_amortization", "grocery_movement")
#: 食材行必须显式声明动作，缺一个就拒收（价差必须能追溯到收货/退货）。
GROCERY_KINDS = ("grocery_receipt", "grocery_return", "grocery_price_variance")


class IndustryDraftWrite(BaseModel):
    envelope_id: str = Field(min_length=1, max_length=80)


def _digest(value) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _rows(value):
    return value if isinstance(value, list) else json.loads(value)


class _IndustryBase:
    processor_code = ""
    capability = ""

    def __init__(self, db, access=None):
        self.db = db
        self.access = access or AccessControl()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- shared envelope/contract plumbing -------------------------------
    def _context(self, conn, actor, book_id: str, envelope_id: str, capability: str | None = None):
        book = self.access.book(conn, actor, book_id, capability or REVIEW)
        envelope = conn.execute(
            "SELECT * FROM fsc_import_envelopes WHERE id=? AND book_id=? AND firm_id=?",
            (envelope_id, book_id, actor.firm_id),
        ).fetchone()
        if not envelope:
            raise AccountingError("导入信封不存在或无权访问", 404)
        if envelope["status"] != "confirmed":
            raise AccountingError("导入信封尚未确认，不能生成领域输出", 409)
        contract = conn.execute(
            "SELECT * FROM fsc_industry_contracts WHERE company_id=? AND firm_id=? "
            "AND status='active' AND processor_code=? ORDER BY version DESC LIMIT 1",
            (book["company_id"], actor.firm_id, self.processor_code),
        ).fetchone()
        if not contract:
            raise AccountingError(
                f"没有已生效的 {self.processor_code} 处理器契约，不能生成领域输出", 409
            )
        return book, envelope, dict(contract) | {"policy": self._policy(contract)}

    @staticmethod
    def _policy(contract) -> dict:
        value = contract["evidence_policy_json"]
        return value if isinstance(value, dict) else json.loads(value or "{}")

    @staticmethod
    def _adopted(envelope) -> list[dict]:
        rows = [row for row in _rows(envelope["rows_json"]) if row.get("status") == "adopted"]
        if not rows:
            raise AccountingError("导入信封没有可生成的采用行", 422)
        return rows

    def _persist(
        self,
        conn,
        actor,
        book,
        envelope,
        contract,
        *,
        kind: str,
        lines: list[dict],
        total_cents: int,
    ) -> dict:
        existing = conn.execute(
            "SELECT * FROM fsc_industry_drafts WHERE envelope_id=? AND contract_id=? "
            "AND processor_code=? AND kind=?",
            (envelope["id"], contract["id"], self.processor_code, kind),
        ).fetchone()
        if existing:
            return {**self._view(existing), "replayed": True}
        fingerprint = _digest(
            {
                "processor": self.processor_code,
                "kind": kind,
                "envelope": envelope["fingerprint"],
                "contract": contract["fingerprint"],
                "source_version": int(envelope["source_version"]),
                "lines": lines,
                "total_cents": total_cents,
            }
        )
        identifier = uuid4().hex
        conn.execute(
            "INSERT INTO fsc_industry_drafts(id,firm_id,company_id,book_id,envelope_id,contract_id,"
            "processor_code,kind,period,source_version,total_cents,lines_json,fingerprint,"
            "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                actor.firm_id,
                book["company_id"],
                book["id"],
                envelope["id"],
                contract["id"],
                self.processor_code,
                kind,
                str(envelope["period"]),
                int(envelope["source_version"]),
                int(total_cents),
                json.dumps(lines, ensure_ascii=False),
                fingerprint,
                actor.user_id,
                self._now(),
            ),
        )
        row = conn.execute("SELECT * FROM fsc_industry_drafts WHERE id=?", (identifier,)).fetchone()
        return {**self._view(row), "replayed": False}

    def _view(self, row, envelope_status="confirmed", contract_status="active") -> dict:
        stale = envelope_status != "confirmed" or contract_status != "active"
        if envelope_status != "confirmed":
            reason = "导入信封已被新版本替代或失效"
        elif contract_status != "active":
            reason = "行业处理器契约已停用或被替代"
        else:
            reason = ""
        return {
            "id": row["id"],
            "company_id": row["company_id"],
            "book_id": row["book_id"],
            "envelope_id": row["envelope_id"],
            "contract_id": row["contract_id"],
            "processor_code": row["processor_code"],
            "kind": row["kind"],
            "period": row["period"],
            "source_version": int(row["source_version"]),
            "total_cents": int(row["total_cents"]),
            "lines": _rows(row["lines_json"]),
            "fingerprint": row["fingerprint"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "stale": stale,
            "stale_reason": reason,
        }

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, capability="read")
            rows = conn.execute(
                "SELECT d.*,e.status AS envelope_status,c.status AS contract_status "
                "FROM fsc_industry_drafts d "
                "LEFT JOIN fsc_import_envelopes e ON e.id=d.envelope_id "
                "LEFT JOIN fsc_industry_contracts c ON c.id=d.contract_id "
                "WHERE d.book_id=? AND d.firm_id=? AND d.processor_code=? "
                "ORDER BY d.created_at DESC,d.id DESC LIMIT 50",
                (book_id, actor.firm_id, self.processor_code),
            ).fetchall()
            return {
                "items": [
                    self._view(
                        row,
                        row["envelope_status"] or "missing",
                        row["contract_status"] or "missing",
                    )
                    for row in rows
                ]
            }


class PropertyServiceDrafts(_IndustryBase):
    """预收物业费按期摊销 + 食材收货/退货/采购价差。

    两件事分开出草稿：摊销按合同逐条汇总，食材按动作（收货/退货/价差）汇总，
    每行都保留来源行号，复核时能逐行回溯。跨期与非法单价/数量在信封与处理器
    两层都被拒。
    """

    processor_code = "property_service"
    capability = "property_service_billing"

    @staticmethod
    def _grocery(rows: list[dict], index: int) -> dict:
        raw = rows[index].get("raw") or {}
        action = str(raw.get("grocery_kind") or "").strip()
        if action not in GROCERY_KINDS:
            raise AccountingError(
                f"第 {rows[index]['row_number']} 行食材记录必须声明收货/退货/价差动作", 422
            )
        quantity = int(rows[index].get("quantity_milli") or 0)
        unit_price = raw.get("unit_price_cents")
        if not isinstance(unit_price, int) or isinstance(unit_price, bool) or unit_price < 0:
            raise AccountingError(f"第 {rows[index]['row_number']} 行单价必须是非负整数分", 422)
        if action != "grocery_price_variance":
            if quantity <= 0:
                raise AccountingError(
                    f"第 {rows[index]['row_number']} 行数量必须大于 0（单位为千分之一）", 422
                )
            expected = (unit_price * quantity + 500) // 1000
            if abs(expected - int(rows[index]["amount_cents"])) > 1:
                raise AccountingError(
                    f"第 {rows[index]['row_number']} 行金额与单价×数量不一致，不允许倒推金额", 422
                )
        return {"action": action, "quantity_milli": quantity, "unit_price_cents": unit_price}

    def generate(self, actor, book_id, request: IndustryDraftWrite, *, kind: str):
        if kind not in PROPERTY_KINDS:
            raise AccountingError(f"未知的物业台账产物：{kind}", 422)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, REVIEW)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, envelope, contract = self._context(
                conn, actor, book_id, request.envelope_id, REVIEW
            )
            rows = self._adopted(envelope)
            if kind == "property_amortization":
                lines = self._amortization(rows)
            else:
                lines = self._movements(rows)
            total = sum(int(item["amount_cents"]) for item in lines)
            return self._persist(
                conn, actor, book, envelope, contract, kind=kind, lines=lines, total_cents=total
            )

    @staticmethod
    def _amortization(rows: list[dict]) -> list[dict]:
        per_contract: dict[str, dict] = {}
        for row in rows:
            raw = row.get("raw") or {}
            if str(raw.get("grocery_kind") or "").strip():
                continue
            if row.get("target_kind") != "service_contract" or not row.get("target_id"):
                raise AccountingError(
                    f"第 {row['row_number']} 行物业费摊销必须绑定 service_contract 目标", 422
                )
            months = raw.get("amortize_months", 1)
            if not isinstance(months, int) or isinstance(months, bool) or months < 1:
                raise AccountingError(f"第 {row['row_number']} 行摊销月数必须为正整数", 422)
            amount = int(row["amount_cents"])
            if amount < 0:
                raise AccountingError(f"第 {row['row_number']} 行摊销金额不能为负", 422)
            item = per_contract.setdefault(
                row["target_id"],
                {
                    "contract_id": row["target_id"],
                    "amount_cents": 0,
                    "months": 0,
                    "row_numbers": [],
                },
            )
            item["amount_cents"] += amount
            item["months"] = max(item["months"], months)
            item["row_numbers"].append(int(row["row_number"]))
        lines = [item for item in per_contract.values()]
        if not lines:
            raise AccountingError("导入信封没有物业费摊销行", 422)
        for item in lines:
            item["row_numbers"].sort()
        return sorted(lines, key=lambda value: value["contract_id"])

    @staticmethod
    def _movements(rows: list[dict]) -> list[dict]:
        per_action: dict[str, dict] = {}
        for index, row in enumerate(rows):
            raw = row.get("raw") or {}
            if not str(raw.get("grocery_kind") or "").strip():
                continue
            detail = PropertyServiceDrafts._grocery(rows, index)
            sign = -1 if detail["action"] == "grocery_return" else 1
            item = per_action.setdefault(
                detail["action"],
                {"action": detail["action"], "amount_cents": 0, "quantity_milli": 0, "rows": []},
            )
            item["amount_cents"] += sign * int(row["amount_cents"])
            item["quantity_milli"] += sign * int(detail["quantity_milli"])
            item["rows"].append(int(row["row_number"]))
        lines = [item for item in per_action.values()]
        if not lines:
            raise AccountingError("导入信封没有食材收货/退货/价差行", 422)
        for item in lines:
            item["rows"].sort()
        return sorted(lines, key=lambda value: value["action"])


class TradeInventoryDrafts(_IndustryBase):
    """三单核对（合同 / 出入库单 / 发票）+ 已批准计价方法的成本结转。"""

    processor_code = "trade_inventory"
    capability = "trade_inventory_match"
    DOCUMENTS = ("contract", "inbound", "outbound", "invoice")

    @staticmethod
    def _method(contract) -> str:
        method = str((contract["policy"] or {}).get("inventory_costing_method") or "").strip()
        if method not in COSTING_METHODS:
            raise AccountingError(
                "企业尚未批准存货计价方法（moving_weighted_average / fifo），"
                "不能生成成本结转草稿；请在行业契约的 evidence_policy 中明确批准",
                409,
            )
        return method

    def generate(self, actor, book_id, request: IndustryDraftWrite, *, kind: str):
        if kind not in {"trade_purchase_match", "trade_cost_carry"}:
            raise AccountingError(f"未知的贸易台账产物：{kind}", 422)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, REVIEW)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, envelope, contract = self._context(
                conn, actor, book_id, request.envelope_id, REVIEW
            )
            method = self._method(contract)
            matched = self._match(self._adopted(envelope))
            if kind == "trade_purchase_match":
                lines = [
                    {key: value for key, value in item.items() if key != "lots"} for item in matched
                ]
            else:
                lines = self._carry(matched, method)
            total = sum(int(item["amount_cents"]) for item in lines)
            return self._persist(
                conn, actor, book, envelope, contract, kind=kind, lines=lines, total_cents=total
            )

    @staticmethod
    def _match(rows: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for row in rows:
            raw = row.get("raw") or {}
            document = str(raw.get("document") or "").strip()
            if document not in TradeInventoryDrafts.DOCUMENTS:
                raise AccountingError(
                    f"第 {row['row_number']} 行必须声明单据类型"
                    "（contract/inbound/outbound/invoice）",
                    422,
                )
            key = str(raw.get("match_key") or "").strip()
            if not key:
                raise AccountingError(f"第 {row['row_number']} 行缺少三单核对键", 422)
            quantity = int(row.get("quantity_milli") or 0)
            amount = int(row["amount_cents"])
            if quantity < 0 or amount < 0:
                raise AccountingError(f"第 {row['row_number']} 行数量与金额不能为负", 422)
            item = grouped.setdefault(
                key, {"match_key": key, "documents": {}, "lots": [], "row_numbers": []}
            )
            if document != "inbound" and document in item["documents"]:
                raise AccountingError(
                    f"三单核对键 {key} 的 {document} 单据出现多行，必须逐单一行", 422
                )
            item["documents"].setdefault(document, []).append(
                {
                    "row_number": int(row["row_number"]),
                    "quantity_milli": quantity,
                    "amount_cents": amount,
                }
            )
            item["row_numbers"].append(int(row["row_number"]))
            if document == "inbound":
                item["lots"].append(
                    {
                        "row_number": int(row["row_number"]),
                        "quantity_milli": quantity,
                        "amount_cents": amount,
                    }
                )
        lines = []
        for key in sorted(grouped):
            item = grouped[key]
            missing = [
                name for name in TradeInventoryDrafts.DOCUMENTS if not item["documents"].get(name)
            ]
            if missing:
                raise AccountingError(
                    f"三单核对不全：{key} 缺少 {', '.join(missing)}，不能生成成本结转", 409
                )
            # 入库可以是多批到货，因此按合计参与三单核对；合同与发票必须各一张。
            contract_row = item["documents"]["contract"][0]
            invoice_row = item["documents"]["invoice"][0]
            inbound_quantity = sum(lot["quantity_milli"] for lot in item["lots"])
            inbound_amount = sum(lot["amount_cents"] for lot in item["lots"])
            agreed = {
                (contract_row["quantity_milli"], contract_row["amount_cents"]),
                (invoice_row["quantity_milli"], invoice_row["amount_cents"]),
                (inbound_quantity, inbound_amount),
            }
            if len(agreed) != 1:
                raise AccountingError(
                    f"三单核对不一致：{key} 的合同/入库合计/发票数量或金额不同，不能生成成本结转",
                    409,
                )
            outbound = item["documents"]["outbound"][0]
            if outbound["quantity_milli"] <= 0:
                raise AccountingError(f"三单核对失败：{key} 的出库数量必须大于 0", 409)
            lines.append(
                {
                    "match_key": key,
                    "quantity_milli": contract_row["quantity_milli"],
                    "amount_cents": contract_row["amount_cents"],
                    "outbound_quantity_milli": outbound["quantity_milli"],
                    "documents": {
                        "contract": contract_row["row_number"],
                        "invoice": invoice_row["row_number"],
                        "outbound": outbound["row_number"],
                        "inbound": [lot["row_number"] for lot in item["lots"]],
                    },
                    "lots": item["lots"],
                }
            )
        return lines

    @staticmethod
    def _carry(matched: list[dict], method: str) -> list[dict]:
        lines = []
        for item in matched:
            lots = sorted(item["lots"], key=lambda lot: lot["row_number"])
            required = int(item["outbound_quantity_milli"])
            if method == "moving_weighted_average":
                total_quantity = sum(lot["quantity_milli"] for lot in lots)
                total_amount = sum(lot["amount_cents"] for lot in lots)
                if total_quantity <= 0:
                    raise AccountingError(
                        f"三单核对失败：{item['match_key']} 入库数量合计为零", 409
                    )
                if required > total_quantity:
                    raise AccountingError(
                        f"三单核对失败：{item['match_key']} 出库数量超过入库数量，不能按 {method} 结转",
                        409,
                    )
                cost = (total_amount * required + total_quantity // 2) // total_quantity
            else:  # fifo
                remaining = required
                cost = 0
                for lot in lots:
                    if remaining <= 0:
                        break
                    take = min(remaining, lot["quantity_milli"])
                    if lot["quantity_milli"] <= 0:
                        raise AccountingError(
                            f"三单核对失败：{item['match_key']} 入库批数量为零", 409
                        )
                    cost += (lot["amount_cents"] * take + lot["quantity_milli"] // 2) // lot[
                        "quantity_milli"
                    ]
                    remaining -= take
                if remaining > 0:
                    raise AccountingError(
                        f"三单核对失败：{item['match_key']} 出库数量超过入库数量，不能按 {method} 结转",
                        409,
                    )
            lines.append(
                {
                    "match_key": item["match_key"],
                    "method": method,
                    "quantity_milli": required,
                    "amount_cents": cost,
                    "inbound_rows": [lot["row_number"] for lot in lots],
                    "outbound_rows": [item["documents"]["outbound"]],
                    "invoice_rows": [item["documents"]["invoice"]],
                    "contract_rows": [item["documents"]["contract"]],
                }
            )
        return lines


class HospitalSummaryDrafts(_IndustryBase):
    """仅接收已批准的科室/期间汇总；患者级明细在信封入口与处理器各拦一次。"""

    processor_code = "hospital_summary"
    capability = "hospital_department_summary"

    def generate(self, actor, book_id, request: IndustryDraftWrite):
        with self.db.transaction(actor=actor) as conn:
            company_id = self.access.book(conn, actor, book_id, REVIEW)["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book, envelope, contract = self._context(
                conn, actor, book_id, request.envelope_id, REVIEW
            )
            rows = self._adopted(envelope)
            # 纵深防御：即使信封绕过入口（历史数据、迁移脚本），这里也要拦住。
            for row in rows:
                assert_no_patient_privacy(row, subject="内部医院汇总生成")
            departments: dict[str, dict] = {}
            for row in rows:
                if row.get("target_kind") != "department" or not row.get("target_id"):
                    raise AccountingError(
                        f"第 {row['row_number']} 行必须绑定 department 目标（只收科室汇总）", 422
                    )
                amount = int(row["amount_cents"])
                if amount < 0:
                    raise AccountingError(f"第 {row['row_number']} 行汇总金额不能为负", 422)
                item = departments.setdefault(
                    row["target_id"],
                    {
                        "department_id": row["target_id"],
                        "amount_cents": 0,
                        "quantity_milli": 0,
                        "row_numbers": [],
                    },
                )
                item["amount_cents"] += amount
                item["quantity_milli"] += int(row.get("quantity_milli") or 0)
                item["row_numbers"].append(int(row["row_number"]))
            lines = [departments[key] for key in sorted(departments)]
            for item in lines:
                item["row_numbers"].sort()
            total = sum(item["amount_cents"] for item in lines)
            return self._persist(
                conn,
                actor,
                book,
                envelope,
                contract,
                kind="hospital_department_summary",
                lines=lines,
                total_cents=total,
            )
