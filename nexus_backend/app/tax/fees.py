"""Synthetic taxi-fee batches over B-adopted source rows.

Deterministic, explicit policy only. No default fee rules and no external calls.
The generated accounting draft is the controlled large voucher; every line keeps
its source row, driver and vehicle identity.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.catalog import CatalogService
from ..accounting.review_policy import ReviewPolicyService
from ..accounting.service import AccountingService, now
from ..ledgers.service import LedgersService
from .fee_guard import transport_issues, transport_snapshot

MAX_LINES = 2400


def _payer_matches(payer: str, driver: str) -> bool:
    payer = payer.replace("核算主体", "").strip()
    driver = driver.strip()
    return bool(payer and driver and (payer == driver or driver in payer or payer in driver))


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _cents(value, *, field):
    text = str(value if value is not None else "").strip().replace(",", "").replace("，", "")
    if text in {"", "-", "—"}:
        return 0
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise AccountingError(f"{field}金额格式无效", 422) from error
    if (
        not number.is_finite()
        or abs(number) > Decimal("999999999999.99")
        or number.as_tuple().exponent < -2
    ):
        raise AccountingError(f"{field}最多两位小数", 422)
    return int(number * 100)


def _raw(row, *keys):
    normalized = row.get("normalized", {})
    if keys[0] in normalized:
        return str(normalized[keys[0]]).strip()
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _policy(value):
    if not isinstance(value, dict):
        raise AccountingError("规费策略必须显式提供", 422)
    allowed = {"version", "driver_receivable_account", "vehicle_revenue_account"}
    if set(value) - allowed:
        raise AccountingError("规费策略包含未知配置", 422)
    version = str(value.get("version") or "").strip()
    receivable = str(value.get("driver_receivable_account") or "").strip()
    revenue = str(value.get("vehicle_revenue_account") or "").strip()
    if not version or not receivable or not revenue:
        raise AccountingError("规费策略缺少版本或科目", 422)
    if receivable == revenue:
        raise AccountingError("驾驶员应收与单车收入不能使用同一科目", 422)
    return {
        "version": version,
        "driver_receivable_account": receivable,
        "vehicle_revenue_account": revenue,
    }


class TaxFeeService:
    def __init__(self, db):
        self.db = db
        self.ledgers = LedgersService(db)

    def generate(self, actor, book_id, *, period, source_file_ids, policy):
        policy = _policy(policy)
        source_file_ids = list(dict.fromkeys(source_file_ids or []))
        if not source_file_ids or len(source_file_ids) > 20:
            raise AccountingError("规费批次须选择 1–20 个已采用来源", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            AccountingService(self.db)._open(book)
            policy["review_policy_version"] = ReviewPolicyService.policy_in_transaction(
                conn, book_id
            )["version"]
            if period != book["period"]:
                raise AccountingError("规费批次期间必须与当前账套期间一致", 409)
            sources = []
            for source_id in source_file_ids:
                source = conn.execute(
                    "SELECT * FROM tax_source_files WHERE id=? AND book_id=? AND firm_id=?",
                    (source_id, book_id, actor.firm_id),
                ).fetchone()
                if not source:
                    raise AccountingError("来源文件不存在或不属于当前账套", 404)
                if source["status"] != "active":
                    raise AccountingError("来源文件已过期，不能生成新批次", 409)
                if source["period"] != period:
                    raise AccountingError("来源文件期间与批次不一致", 409)
                sources.append(source)
            accounts = {}
            for code in (policy["driver_receivable_account"], policy["vehicle_revenue_account"]):
                row = conn.execute(
                    "SELECT code,category,normal_balance FROM accounts WHERE book_id=? AND code=? AND active=1",
                    (book_id, code),
                ).fetchone()
                if not row:
                    raise AccountingError(f"规费科目 {code} 不存在或已停用", 409)
                category, normal = (
                    ("asset", "debit")
                    if code == policy["driver_receivable_account"]
                    else ("income", "credit")
                )
                if row["category"] != category or row["normal_balance"] != normal:
                    raise AccountingError(f"规费科目 {code} 的核算类别或余额方向不适用", 409)
                accounts[code] = row["code"]
            source_fingerprint = _digest(sorted((s["id"], s["fingerprint"]) for s in sources))
            batch_id = uuid4().hex
            batch_rows, lines, seen = [], [], set()
            identities, transport_cache = {}, {}
            for source in sources:
                rows = json.loads(source["rows_json"])
                for row in rows:
                    if row.get("status") != "adopted":
                        continue
                    row_number = int(row.get("row_number") or 0)
                    external_id = str(row.get("external_id") or "").strip()
                    key = (source["id"], row_number)
                    if key in seen:
                        continue
                    seen.add(key)
                    driver_ext = _raw(row, "driver_external_id", "驾驶员编号", "驾驶员")
                    vehicle_ext = _raw(row, "vehicle_external_id", "车辆编号", "车辆")
                    reason = ""
                    driver = vehicle = None
                    if not driver_ext or not vehicle_ext:
                        reason = "缺少驾驶员或车辆稳定编号"
                    else:
                        driver = conn.execute(
                            "SELECT id FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind='driver' AND external_id=? AND active=TRUE",
                            (actor.firm_id, book["company_id"], driver_ext),
                        ).fetchone()
                        vehicle = conn.execute(
                            "SELECT id FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind='vehicle' AND external_id=? AND active=TRUE",
                            (actor.firm_id, book["company_id"], vehicle_ext),
                        ).fetchone()
                        if not driver or not vehicle:
                            reason = "驾驶员或车辆主档缺失/已停用"
                    occurred = str(row.get("date") or _raw(row, "日期", "date") or f"{period}-01")
                    try:
                        day = date.fromisoformat(occurred).isoformat()
                    except ValueError:
                        reason = "作业日期无效"
                        day = f"{period}-01"
                    if (
                        not reason
                        and not conn.execute(
                            "SELECT 1 FROM transport_assignments WHERE firm_id=? AND company_id=? AND vehicle_id=? AND driver_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)",
                            (
                                actor.firm_id,
                                book["company_id"],
                                vehicle["id"],
                                driver["id"],
                                day,
                                day,
                            ),
                        ).fetchone()
                    ):
                        reason = "当日无有效的车辆-驾驶员班次核定"
                    if not reason:
                        key = (day, vehicle_ext, driver_ext)
                        try:
                            if key not in transport_cache:
                                transport_cache[key] = (
                                    self.ledgers.resolve_transport_in_transaction(
                                        conn,
                                        actor,
                                        book_id,
                                        day,
                                        vehicle_external_id=vehicle_ext,
                                        driver_external_id=driver_ext,
                                    )
                                )
                            identity = transport_snapshot(
                                transport_cache[key], source["id"], row_number
                            )
                        except AccountingError:
                            identity = None
                        if identity is None:
                            reason = "驾驶员往来主体或车辆项目缺少当日有效核定映射"
                        else:
                            identities[(source["id"], row_number)] = identity
                    gross = int(row.get("amount_cents") or 0)
                    try:
                        discount = _cents(_raw(row, "discount", "优惠减免"), field="优惠减免")
                        adjustment = _cents(_raw(row, "adjustment", "调整"), field="调整")
                    except AccountingError as error:
                        reason, discount, adjustment = str(error), 0, 0
                    net = gross - discount + adjustment
                    if reason:
                        status = "rejected"
                    elif net < 0:
                        status, reason = "rejected", "净额不能为负"
                    elif net == 0:
                        status, reason = "zero", "零额不生成分录"
                    else:
                        status = "adopted"
                    row_id = uuid4().hex
                    batch_rows.append(
                        {
                            "id": row_id,
                            "source_file_id": source["id"],
                            "source_row_number": row_number,
                            "external_id": external_id,
                            "driver_id": driver["id"] if driver else None,
                            "vehicle_id": vehicle["id"] if vehicle else None,
                            "gross_cents": gross,
                            "discount_cents": discount,
                            "adjustment_cents": adjustment,
                            "net_cents": net,
                            "status": status,
                            "reason": reason,
                        }
                    )
                    if status == "adopted" and adjustment:
                        adjustment_reason = _raw(row, "adjustment_reason", "调整原因")
                        if not adjustment_reason:
                            batch_rows[-1].update(
                                status="rejected", reason="调整缺少源行理由", net_cents=0
                            )
                        else:
                            # Keep the source-row reason so 336/335 differences
                            # remain auditable instead of being silently netted.
                            batch_rows[-1]["reason"] = adjustment_reason
            total = sum(item["net_cents"] for item in batch_rows if item["status"] == "adopted")
            adopted = [item for item in batch_rows if item["status"] == "adopted"]
            if not adopted or total <= 0:
                raise AccountingError("批次没有可生成的非零净额，未创建凭证", 409)
            if len(adopted) * 2 > MAX_LINES:
                raise AccountingError(f"单张受控大凭证最多 {MAX_LINES} 行，请拆分批次", 413)
            transport_facts = sorted(
                (
                    identities[(item["source_file_id"], item["source_row_number"])]
                    for item in adopted
                ),
                key=lambda item: (item["source_file_id"], item["source_row_number"]),
            )
            batch_fingerprint = _digest(
                {
                    "period": period,
                    "sources": source_fingerprint,
                    "policy": policy,
                    "transport": transport_facts,
                }
            )
            existing = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE book_id=? AND batch_fingerprint=?",
                (book_id, batch_fingerprint),
            ).fetchone()
            if existing:
                return self._view(conn, existing)
            stamp = now()
            source_id, draft_id = uuid4().hex, uuid4().hex
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,amount_cents,direction,evidence_ref,fingerprint,source_kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    book_id,
                    f"tax-fee:{batch_id}",
                    "出租车规费批次",
                    f"{period}-01",
                    f"规费批次 {batch_id}",
                    "驾驶员/车辆规费",
                    total,
                    "income",
                    f"tax-fee-batch:{batch_id}",
                    batch_fingerprint,
                    "bank",
                ),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (
                    draft_id,
                    book_id,
                    source_id,
                    f"出租车规费 {period}",
                    f"按显式策略 {policy['version']} 和已采用来源生成；批次 {batch_id}；须独立复核。",
                ),
            )
            ordinal = 0
            ordinal_by_row = {}
            auxiliaries = []
            for item in adopted:
                for role in ("receivable", "revenue"):
                    code = (
                        policy["driver_receivable_account"]
                        if role == "receivable"
                        else policy["vehicle_revenue_account"]
                    )
                    debit, credit = (
                        (item["net_cents"], 0) if role == "receivable" else (0, item["net_cents"])
                    )
                    conn.execute(
                        "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
                        (draft_id, book_id, ordinal, code, debit, credit),
                    )
                    conn.execute(
                        "INSERT INTO draft_line_sources(draft_id,book_id,ordinal,source_file_id,source_row_number,driver_id,vehicle_id,role,amount_cents) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            draft_id,
                            book_id,
                            ordinal,
                            item["source_file_id"],
                            item["source_row_number"],
                            item["driver_id"],
                            item["vehicle_id"],
                            role,
                            item["net_cents"],
                        ),
                    )
                    ordinal_by_row.setdefault(item["id"], ordinal)
                    dimension = "counterparty" if role == "receivable" else "project"
                    auxiliaries.append(
                        {
                            dimension: identities[
                                (item["source_file_id"], item["source_row_number"])
                            ][dimension]["name"]
                        }
                    )
                    ordinal += 1
            CatalogService(self.db).save_auxiliaries_in_transaction(
                conn,
                actor,
                book_id,
                draft_id,
                auxiliaries,
            )
            conn.execute(
                "INSERT INTO tax_fee_batches(id,firm_id,company_id,book_id,period,version,draft_id,policy_json,policy_fingerprint,source_fingerprint,batch_fingerprint,total_cents,row_count,skipped_count,created_by,created_at,transport_snapshot_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    period,
                    1,
                    draft_id,
                    json.dumps(policy, ensure_ascii=False, sort_keys=True),
                    _digest(policy),
                    source_fingerprint,
                    batch_fingerprint,
                    total,
                    len(batch_rows),
                    sum(1 for r in batch_rows if r["status"] != "adopted"),
                    actor.user_id,
                    stamp,
                    json.dumps(transport_facts, ensure_ascii=False, sort_keys=True),
                ),
            )
            for item in batch_rows:
                conn.execute(
                    "INSERT INTO tax_fee_batch_rows(id,batch_id,source_file_id,source_row_number,external_id,driver_id,vehicle_id,gross_cents,discount_cents,adjustment_cents,net_cents,status,reason,line_ordinal) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["id"],
                        batch_id,
                        item["source_file_id"],
                        item["source_row_number"],
                        item["external_id"],
                        item["driver_id"],
                        item["vehicle_id"],
                        item["gross_cents"],
                        item["discount_cents"],
                        item["adjustment_cents"],
                        item["net_cents"],
                        item["status"],
                        item["reason"],
                        ordinal_by_row.get(item["id"]),
                    ),
                )
            conn.execute(
                "UPDATE tax_fee_batches SET source_snapshot_json=? WHERE id=?",
                (json.dumps(sorted((s["id"], s["fingerprint"]) for s in sources)), batch_id),
            )
            return self._view(
                conn,
                conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch_id,)).fetchone(),
            )

    def list_batches(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE book_id=? ORDER BY created_at DESC", (book_id,)
            ).fetchall()
            return {"items": [self._view(conn, row) for row in rows]}

    def list_receivables(self, actor, book_id, period=None):
        """Reviewed fee rows as per-driver receivables. No bank allocation is implied."""
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            sql = (
                "SELECT r.id AS receivable_id,r.batch_id,b.period,r.batch_row_id,"
                "r.driver_id,r.driver_name,r.vehicle_name,r.amount_cents,r.status "
                "FROM fee_receivable_items r JOIN tax_fee_batches b ON b.id=r.batch_id "
                "WHERE r.book_id=? AND b.status='reviewed'"
            )
            params = [book_id]
            if period:
                sql += " AND b.period=?"
                params.append(period)
            sql += " ORDER BY b.period DESC,r.created_at"
            rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
            items = [
                {
                    "receivable_id": row["receivable_id"],
                    "batch_id": row["batch_id"],
                    "period": row["period"],
                    "batch_row_id": row["batch_row_id"],
                    "driver_id": row["driver_id"],
                    "driver_name": row["driver_name"],
                    "vehicle_name": row["vehicle_name"],
                    "amount_cents": int(row["amount_cents"]),
                    "settlement_status": "settled" if row["status"] == "settled" else "unsettled",
                }
                for row in rows
            ]
            return {
                "items": items,
                "row_count": len(items),
                "total_cents": sum(item["amount_cents"] for item in items),
                "period": period,
                "note": "仅形成驾驶员应收明细；银行到账核销尚未接入。",
            }

    def allocate_receivable(
        self,
        actor,
        book_id,
        receivable_id,
        *,
        bank_source_id,
        amount_cents,
        idempotency_key,
        payer_name=None,
        collection_basis=None,
        bank_statement_line_id=None,
    ):
        """Apply one bank receipt to one driver receivable, preserving partial balances."""
        amount_cents = int(amount_cents)
        if amount_cents <= 0:
            raise AccountingError("核销金额必须大于 0", 422)
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise AccountingError("核销必须提供幂等键", 422)
        payer_name = str(payer_name or "").strip()
        collection_basis = str(collection_basis or "").strip()
        digest = _digest(
            {
                "book_id": book_id,
                "receivable_id": receivable_id,
                "bank_source_id": bank_source_id,
                "amount_cents": amount_cents,
                "payer_name": payer_name,
                "collection_basis": collection_basis,
            }
        )
        with self.db.transaction(actor=actor) as conn:
            company_id = self.ledgers.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            prior = conn.execute(
                "SELECT * FROM fee_receivable_allocations WHERE created_by=? AND idempotency_key=?",
                (actor.user_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("核销幂等键已用于其它内容", 409)
                return self._allocation_view(conn, prior)
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
                    (f"fee-receivable:{receivable_id}",),
                )
            item = conn.execute(
                "SELECT * FROM fee_receivable_items WHERE id=? AND book_id=?",
                (receivable_id, book_id),
            ).fetchone()
            if not item:
                raise AccountingError("驾驶员应收对象不存在", 404)
            if item["status"] == "settled":
                raise AccountingError("该驾驶员应收已结清", 409)
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
                    (f"fee-bank:{bank_source_id}",),
                )
            bank = conn.execute(
                "SELECT s.*,d.id AS draft_id,d.status AS draft_status,d.version AS draft_version "
                "FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id "
                "JOIN periods p ON p.book_id=s.book_id AND p.status='open' "
                "WHERE s.id=? AND s.book_id=? AND s.source_kind='bank'",
                (bank_source_id, book_id),
            ).fetchone()
            if not bank:
                raise AccountingError("银行来源不存在或不属于当前账套", 404)
            if bank["draft_status"] != "draft":
                raise AccountingError("银行来源已审核，不能继续分配", 409)
            if bank["direction"] != "income":
                raise AccountingError("规费应收只能由收入方向银行流水核销", 409)
            if str(bank["date"])[:7] != str(item["period"]):
                raise AccountingError("银行流水不属于驾驶员应收期间", 409)
            statement_line_id = self._statement_line_for(conn, book, bank, bank_statement_line_id)
            effective_payer = payer_name or str(bank["counterparty"] or "").strip()
            driver_name = str(item["driver_name"] or "").strip()
            if not _payer_matches(effective_payer, driver_name) and len(collection_basis) < 5:
                raise AccountingError("付款人与驾驶员不一致，必须提供代收核定依据", 409)
            allocated_item = self._net_collected(conn, receivable_id)
            allocated_bank = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM fee_receivable_allocations "
                "WHERE bank_source_id=? AND reversed_at IS NULL",
                (bank_source_id,),
            ).fetchone()[0]
            if amount_cents + int(allocated_item) > int(item["amount_cents"]):
                raise AccountingError("核销金额超过驾驶员应收余额", 409)
            if amount_cents + int(allocated_bank) > int(bank["amount_cents"]):
                raise AccountingError("核销金额超过银行到账余额", 409)
            allocation_id = uuid4().hex
            conn.execute(
                "INSERT INTO fee_receivable_allocations(id,book_id,firm_id,company_id,receivable_id,"
                "bank_source_id,bank_statement_line_id,"
                "amount_cents,bank_draft_version,bank_amount_cents,created_by,created_at,idempotency_key,"
                "request_fingerprint,payer_name,collection_basis) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    allocation_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    receivable_id,
                    bank_source_id,
                    statement_line_id,
                    amount_cents,
                    int(bank["draft_version"]),
                    int(bank["amount_cents"]),
                    actor.user_id,
                    now(),
                    idempotency_key,
                    digest,
                    effective_payer or None,
                    collection_basis or None,
                ),
            )
            total = int(allocated_item) + amount_cents
            conn.execute(
                "UPDATE fee_receivable_items SET status=? WHERE id=? AND book_id=?",
                (
                    "settled" if total >= int(item["amount_cents"]) else "open",
                    receivable_id,
                    book_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM fee_receivable_allocations WHERE id=?", (allocation_id,)
            ).fetchone()
            return self._allocation_view(conn, row)

    def reverse_allocation(self, actor, book_id: str, allocation_id: str, reason: str) -> dict:
        reason = reason.strip()
        if len(reason) < 3:
            raise AccountingError("撤销原因至少 3 个字", 422)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.ledgers.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            self.ledgers.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM fee_receivable_allocations WHERE id=? AND book_id=?",
                (allocation_id, book_id),
            ).fetchone()
            if row is None:
                raise AccountingError("到账核销记录不存在", 404)
            if row["reversed_at"]:
                raise AccountingError("该到账核销已撤销，不能重复撤销", 409)
            net = self._net_collected(conn, row["receivable_id"])
            if net - int(row["amount_cents"]) < 0:
                raise AccountingError("该核销对应金额已部分退款，请先撤销退款再撤销核销", 409)
            conn.execute(
                "UPDATE fee_receivable_allocations SET reversed_at=?,reversed_by=?,reversal_reason=? WHERE id=?",
                (now(), actor.user_id, reason, allocation_id),
            )
            rec = conn.execute(
                "SELECT * FROM fee_receivable_items WHERE id=?", (row["receivable_id"],)
            ).fetchone()
            allocated = self._net_collected(conn, row["receivable_id"])
            status = "settled" if int(allocated) >= int(rec["amount_cents"]) else "open"
            conn.execute("UPDATE fee_receivable_items SET status=? WHERE id=?", (status, rec["id"]))
        return {
            "id": allocation_id,
            "receivable_id": row["receivable_id"],
            "reversed": True,
            "receivable_status": status,
            "driver_name": rec["driver_name"],
        }

    def list_allocations(self, actor, book_id, period=None):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            sql = (
                "SELECT a.*,r.driver_name,r.amount_cents AS receivable_amount_cents,r.status AS receivable_status,"
                "b.period FROM fee_receivable_allocations a "
                "JOIN fee_receivable_items r ON r.id=a.receivable_id "
                "JOIN tax_fee_batches b ON b.id=r.batch_id WHERE a.book_id=?"
            )
            params = [book_id]
            if period:
                sql += " AND b.period=?"
                params.append(period)
            sql += " ORDER BY a.created_at DESC"
            return {
                "items": [
                    self._allocation_view(conn, row)
                    for row in conn.execute(sql, tuple(params)).fetchall()
                ]
            }

    def list_bank_sources(self, actor, book_id, period=None, direction="income"):
        if direction not in {"income", "expense"}:
            raise AccountingError("银行来源方向必须为收入或支出", 422)
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            if direction == "income":
                used = (
                    "SELECT COALESCE(SUM(a.amount_cents),0) FROM fee_receivable_allocations a "
                    "WHERE a.bank_source_id=s.id AND a.reversed_at IS NULL"
                )
                remaining = f"(s.amount_cents-({used}))"
            else:
                used = "SELECT COALESCE(SUM(r.amount_cents),0) FROM fee_receivable_refunds r WHERE r.bank_source_id=s.id"
                remaining = f"(abs(s.amount_cents)-({used}))"
            sql = (
                "SELECT s.id,s.date,s.description,s.counterparty,s.amount_cents,d.version AS draft_version,"
                f"{remaining} AS remaining_cents "
                "FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id "
                "JOIN periods p ON p.book_id=s.book_id AND p.status='open' "
                "WHERE s.book_id=? AND s.source_kind='bank' AND s.direction=? AND d.status='draft'"
            )
            params = [book_id, direction]
            if period:
                sql += " AND substr(s.date,1,7)=?"
                params.append(period)
            sql += " ORDER BY s.date,s.id"
            items = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
            return {
                "items": [
                    {
                        "source_id": row["id"],
                        "date": row["date"],
                        "description": row["description"],
                        "counterparty": row["counterparty"],
                        "amount_cents": int(row["amount_cents"]),
                        "remaining_cents": int(row["remaining_cents"]),
                        "draft_version": int(row["draft_version"]),
                    }
                    for row in items
                    if int(row["remaining_cents"]) > 0
                ]
            }

    def _statement_line_for(self, conn, book, bank, explicit_id):
        """Resolve the bank statement line this receipt settles, or refuse to guess.

        显式 id 必须属于同一账套、同一期间，且流水方向金额与银行来源一致；
        没给 id 时只接受「已被这条银行来源绑定过」的那一行。同一笔到账在两份
        流水里出现两次时，宁可留空也不挑一条记账。
        """
        if explicit_id:
            line = conn.execute(
                "SELECT * FROM bank_statement_lines WHERE id=? AND book_id=?",
                (explicit_id, book["id"]),
            ).fetchone()
            if not line:
                raise AccountingError("银行流水行不存在或不属于当前账套", 404)
            if line["status"] == "excluded":
                raise AccountingError("已排除的银行流水行不能用于核销", 409)
            signed = (
                -int(bank["amount_cents"])
                if bank["direction"] == "expense"
                else int(bank["amount_cents"])
            )
            if int(line["amount_cents"]) != signed:
                raise AccountingError("银行流水行金额与银行来源不一致", 409)
            if str(line["line_date"])[:7] != str(bank["date"])[:7]:
                raise AccountingError("银行流水行日期与银行来源不同期", 409)
            return line["id"]
        linked = conn.execute(
            "SELECT id FROM bank_statement_lines WHERE book_id=? AND source_id=?",
            (book["id"], bank["id"]),
        ).fetchone()
        return linked["id"] if linked else None

    def _allocation_view(self, conn, row):
        return {
            "allocation_id": row["id"],
            "receivable_id": row["receivable_id"],
            "bank_source_id": row["bank_source_id"],
            "bank_statement_line_id": row["bank_statement_line_id"]
            if "bank_statement_line_id" in row.keys()
            else None,
            "amount_cents": int(row["amount_cents"]),
            "payer_name": row["payer_name"] if "payer_name" in row.keys() else None,
            "collection_basis": row["collection_basis"]
            if "collection_basis" in row.keys()
            else None,
            "bank_draft_version": int(row["bank_draft_version"]),
            "bank_amount_cents": int(row["bank_amount_cents"]),
            "created_at": row["created_at"],
            "reversed_at": row["reversed_at"],
            "reversal_reason": row["reversal_reason"],
            "receivable_status": row["receivable_status"]
            if "receivable_status" in row.keys()
            else None,
            "driver_name": row["driver_name"] if "driver_name" in row.keys() else None,
        }

    def _net_collected(self, conn, receivable_id):
        allocated = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM fee_receivable_allocations "
            "WHERE receivable_id=? AND reversed_at IS NULL",
            (receivable_id,),
        ).fetchone()[0]
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM fee_receivable_refunds WHERE receivable_id=?",
            (receivable_id,),
        ).fetchone()[0]
        return int(allocated) - int(refunded)

    def refund_receivable(
        self,
        actor,
        book_id,
        receivable_id,
        *,
        bank_source_id,
        amount_cents,
        reason,
        idempotency_key,
    ):
        """Record an outgoing refund against net collected fees; never rewrite the receipt."""
        amount_cents = int(amount_cents)
        reason = str(reason or "").strip()
        if amount_cents <= 0:
            raise AccountingError("退款金额必须大于 0", 422)
        if len(reason) < 3:
            raise AccountingError("退款原因至少 3 个字", 422)
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise AccountingError("退款必须提供幂等键", 422)
        digest = _digest(
            {
                "book_id": book_id,
                "receivable_id": receivable_id,
                "bank_source_id": bank_source_id,
                "amount_cents": amount_cents,
                "reason": reason,
            }
        )
        with self.db.transaction(actor=actor) as conn:
            company_id = self.ledgers.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            prior = conn.execute(
                "SELECT * FROM fee_receivable_refunds WHERE created_by=? AND idempotency_key=?",
                (actor.user_id, idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("退款幂等键已用于其它内容", 409)
                return self._refund_view(conn, self._refund_row(conn, prior["id"]))
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
                    (f"fee-receivable:{receivable_id}",),
                )
            item = conn.execute(
                "SELECT * FROM fee_receivable_items WHERE id=? AND book_id=?",
                (receivable_id, book_id),
            ).fetchone()
            if not item:
                raise AccountingError("驾驶员应收对象不存在", 404)
            if self.db.is_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
                    (f"fee-bank:{bank_source_id}",),
                )
            bank = conn.execute(
                "SELECT s.*,d.id AS draft_id,d.status AS draft_status,d.version AS draft_version "
                "FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id "
                "JOIN periods p ON p.book_id=s.book_id AND p.status='open' "
                "WHERE s.id=? AND s.book_id=? AND s.source_kind='bank'",
                (bank_source_id, book_id),
            ).fetchone()
            if not bank:
                raise AccountingError("退款银行来源不存在或不属于当前账套", 404)
            if bank["draft_status"] != "draft":
                raise AccountingError("退款银行来源已审核，不能继续退款", 409)
            if bank["direction"] != "expense":
                raise AccountingError("退款必须使用支出方向银行流水", 409)
            net = self._net_collected(conn, receivable_id)
            if amount_cents > net:
                raise AccountingError("退款金额超过已核销净额", 409)
            used_bank = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) FROM fee_receivable_refunds WHERE bank_source_id=?",
                (bank_source_id,),
            ).fetchone()[0]
            if amount_cents + int(used_bank) > abs(int(bank["amount_cents"])):
                raise AccountingError("退款金额超过银行付款余额", 409)
            refund_id = uuid4().hex
            conn.execute(
                "INSERT INTO fee_receivable_refunds(id,book_id,firm_id,company_id,receivable_id,bank_source_id,"
                "amount_cents,bank_draft_version,bank_amount_cents,reason,created_by,created_at,idempotency_key,request_fingerprint) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    refund_id,
                    book_id,
                    actor.firm_id,
                    book["company_id"],
                    receivable_id,
                    bank_source_id,
                    amount_cents,
                    int(bank["draft_version"]),
                    abs(int(bank["amount_cents"])),
                    reason,
                    actor.user_id,
                    now(),
                    idempotency_key,
                    digest,
                ),
            )
            remaining = net - amount_cents
            status = "settled" if remaining >= int(item["amount_cents"]) else "open"
            conn.execute(
                "UPDATE fee_receivable_items SET status=? WHERE id=? AND book_id=?",
                (status, receivable_id, book_id),
            )
            return self._refund_view(conn, self._refund_row(conn, refund_id))

    def list_refunds(self, actor, book_id, period=None):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            sql = (
                "SELECT f.*,r.driver_name,r.status AS receivable_status,b.period "
                "FROM fee_receivable_refunds f "
                "JOIN fee_receivable_items r ON r.id=f.receivable_id "
                "JOIN tax_fee_batches b ON b.id=r.batch_id WHERE f.book_id=?"
            )
            params = [book_id]
            if period:
                sql += " AND b.period=?"
                params.append(period)
            sql += " ORDER BY f.created_at DESC"
            return {
                "items": [
                    self._refund_view(conn, row)
                    for row in conn.execute(sql, tuple(params)).fetchall()
                ]
            }

    def _refund_row(self, conn, refund_id):
        return conn.execute(
            "SELECT f.*,r.driver_name,r.status AS receivable_status "
            "FROM fee_receivable_refunds f JOIN fee_receivable_items r ON r.id=f.receivable_id "
            "WHERE f.id=?",
            (refund_id,),
        ).fetchone()

    def _refund_view(self, conn, row):
        return {
            "refund_id": row["id"],
            "receivable_id": row["receivable_id"],
            "bank_source_id": row["bank_source_id"],
            "amount_cents": int(row["amount_cents"]),
            "payer_name": row["payer_name"] if "payer_name" in row.keys() else None,
            "collection_basis": row["collection_basis"]
            if "collection_basis" in row.keys()
            else None,
            "bank_draft_version": int(row["bank_draft_version"]),
            "reason": row["reason"],
            "created_at": row["created_at"],
            "receivable_status": row["receivable_status"]
            if "receivable_status" in row.keys()
            else None,
            "driver_name": row["driver_name"] if "driver_name" in row.keys() else None,
        }

    def get(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            row = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("规费批次不存在", 404)
            return self._view(conn, row)

    def review(self, actor, book_id, batch_id, *, expected_version, reason):
        if not str(reason or "").strip():
            raise AccountingError("独立复核必须填写理由", 422)
        accounting = AccountingService(self.db)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.ledgers.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            accounting._open(book)
            batch = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not batch:
                raise AccountingError("规费批次不存在", 404)
            if batch["status"] != "draft" or int(batch["version"]) != expected_version:
                raise AccountingError("规费批次已复核、失效或版本已变化", 409)
            if batch["created_by"] == actor.user_id:
                raise AccountingError("制单人与复核人必须不同，复核未执行", 409)
            frozen = json.loads(batch["policy_json"]).get("review_policy_version")
            current_policy = ReviewPolicyService.policy_in_transaction(conn, book_id)["version"]
            if frozen != current_policy:
                raise AccountingError("企业复核政策已变化，请重新生成规费批次后复核", 409)
            identity_issues = transport_issues(conn, batch)
            if identity_issues:
                raise AccountingError(identity_issues[0], 409)
            used = self._snapshot_sources(batch)
            current = []
            for item in used:
                source = conn.execute(
                    "SELECT status,fingerprint FROM tax_source_files WHERE id=? AND book_id=?",
                    (item["source_file_id"], book_id),
                ).fetchone()
                if not source or source["status"] != "active":
                    raise AccountingError("来源已过期，规费批次不能复核或导出", 409)
                current.append((item["source_file_id"], source["fingerprint"]))
            if _digest(sorted(current)) != batch["source_fingerprint"]:
                raise AccountingError("来源已变化，旧批次审批与导出已失效", 409)
            draft = accounting.get_draft(actor, book_id, batch["draft_id"])
            approved = accounting._approve_in_transaction(
                conn,
                actor,
                book_id,
                batch["draft_id"],
                draft["version"],
                frozen,
                reason,
            )
            changed = conn.execute(
                "UPDATE tax_fee_batches SET status='reviewed',version=version+1,reviewed_by=?,reviewed_at=? WHERE id=? AND status='draft' AND version=?",
                (actor.user_id, now(), batch_id, expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("规费批次状态已变化", 409)
            self._materialize_receivables(conn, batch_id)
            return self._view(
                conn,
                conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch_id,)).fetchone(),
            ) | {"draft": approved}

    def _materialize_receivables(self, conn, batch_id):
        rows = conn.execute(
            "SELECT r.id,r.source_row_number,r.driver_id,r.vehicle_id,r.net_cents,b.period,d.display_name AS driver_name,v.display_name AS vehicle_name "
            "FROM tax_fee_batch_rows r JOIN tax_fee_batches b ON b.id=r.batch_id "
            "LEFT JOIN transport_subjects d ON d.id=r.driver_id "
            "LEFT JOIN transport_subjects v ON v.id=r.vehicle_id "
            "WHERE r.batch_id=? AND r.status='adopted'",
            (batch_id,),
        ).fetchall()
        for row in rows:
            if conn.execute(
                "SELECT 1 FROM fee_receivable_items WHERE batch_row_id=?", (row["id"],)
            ).fetchone():
                continue
            conn.execute(
                "INSERT INTO fee_receivable_items(id,book_id,batch_id,batch_row_id,period,driver_id,driver_name,vehicle_name,amount_cents,status,created_at) "
                "SELECT ?,b.book_id,b.id,?,?,?,?,?,?,'open',? FROM tax_fee_batches b WHERE b.id=?",
                (
                    uuid4().hex,
                    row["id"],
                    row["period"],
                    row["driver_id"],
                    row["driver_name"] or "未匹配驾驶员",
                    row["vehicle_name"] or "未匹配车辆",
                    int(row["net_cents"]),
                    now(),
                    batch_id,
                ),
            )

    def validate(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            batch = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not batch:
                raise AccountingError("规费批次不存在", 404)
            if batch["status"] == "invalidated":
                raise AccountingError("规费批次已失效", 409)
            identity_issues = transport_issues(conn, batch)
            if identity_issues:
                raise AccountingError(identity_issues[0], 409)
            used = self._snapshot_sources(batch)
            current = []
            for item in used:
                source = conn.execute(
                    "SELECT status,fingerprint FROM tax_source_files WHERE id=? AND book_id=?",
                    (item["source_file_id"], book_id),
                ).fetchone()
                if not source or source["status"] != "active":
                    raise AccountingError("来源已过期，规费批次不能复核或导出", 409)
                current.append((item["source_file_id"], source["fingerprint"]))
            if _digest(sorted(current)) != batch["source_fingerprint"]:
                raise AccountingError("来源已变化，旧批次审批与导出已失效", 409)
            return self._view(conn, batch)

    def invalidate(self, actor, book_id, batch_id, *, expected_version, reason):
        if not str(reason or "").strip():
            raise AccountingError("失效必须填写理由", 422)
        with self.db.transaction(actor=actor) as conn:
            company_id = self.ledgers.access.book(conn, actor, book_id, "review")["company_id"]
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            AccountingService(self.db)._open(book)
            batch = conn.execute(
                "SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not batch:
                raise AccountingError("规费批次不存在", 404)
            if int(batch["version"]) != expected_version:
                raise AccountingError("规费批次版本已变化", 409)
            if batch["status"] == "invalidated":
                return self._view(conn, batch)
            conn.execute(
                "UPDATE tax_fee_batches SET status='invalidated',version=version+1 WHERE id=?",
                (batch_id,),
            )
            book = conn.execute(
                "SELECT * FROM books WHERE id=? AND firm_id=?", (book_id, actor.firm_id)
            ).fetchone()
            self.ledgers._history(
                conn,
                actor,
                book,
                batch_id,
                "tax.fee.invalidate",
                {"reason": reason, "before": dict(batch)},
            )
            return self._view(
                conn,
                conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch_id,)).fetchone(),
            )

    @staticmethod
    def _snapshot_sources(batch):
        return [{"source_file_id": item[0]} for item in json.loads(batch["source_snapshot_json"])]

    def _view(self, conn, batch):
        rows = conn.execute(
            "SELECT * FROM tax_fee_batch_rows WHERE batch_id=? ORDER BY source_file_id,source_row_number",
            (batch["id"],),
        ).fetchall()
        used = self._snapshot_sources(batch)
        current = []
        for item in used:
            source = conn.execute(
                "SELECT status,fingerprint FROM tax_source_files WHERE id=? AND book_id=?",
                (item["source_file_id"], batch["book_id"]),
            ).fetchone()
            if not source or source["status"] != "active":
                current = None
                break
            current.append((item["source_file_id"], source["fingerprint"]))
        identity_issues = transport_issues(conn, batch)
        stale = (
            current is None
            or _digest(sorted(current)) != batch["source_fingerprint"]
            or bool(identity_issues)
        )
        source_files = []
        for item in used:
            source = conn.execute(
                "SELECT id,filename,external_id,version,sha256 FROM tax_source_files WHERE id=? AND book_id=?",
                (item["source_file_id"], batch["book_id"]),
            ).fetchone()
            if source:
                source_files.append(dict(source))
        names = {
            str(subject["id"]): subject["display_name"]
            for subject in conn.execute(
                "SELECT s.id,s.display_name FROM transport_subjects s JOIN books b ON b.company_id=s.company_id AND b.firm_id=s.firm_id WHERE b.id=?",
                (batch["book_id"],),
            ).fetchall()
        }

        def grouped(key):
            totals = {}
            for row in rows:
                if row["status"] != "adopted" or not row[key]:
                    continue
                item = totals.setdefault(
                    str(row[key]),
                    {
                        "id": str(row[key]),
                        "name": names.get(str(row[key]), ""),
                        "row_count": 0,
                        "net_cents": 0,
                    },
                )
                item["row_count"] += 1
                item["net_cents"] += int(row["net_cents"])
            return sorted(
                totals.values(), key=lambda item: (item["name"] or item["id"], item["id"])
            )

        draft_row = conn.execute(
            "SELECT id,status,version FROM drafts WHERE id=? AND book_id=?",
            (batch["draft_id"], batch["book_id"]),
        ).fetchone()
        # 记账回执不在 drafts 上：post_draft 只写 voucher_sources。界面要判断
        # 「这张草稿是否已经生成过正式凭证」，只能从这张关联表读。
        posted = conn.execute(
            "SELECT v.id,v.voucher_no FROM voucher_sources s JOIN vouchers v ON v.id=s.voucher_id "
            "WHERE s.source_kind='draft' AND s.source_id=?",
            (batch["draft_id"],),
        ).fetchone()
        return {
            "id": batch["id"],
            "book_id": batch["book_id"],
            "period": batch["period"],
            "status": batch["status"],
            "version": int(batch["version"]),
            "draft_id": batch["draft_id"],
            "draft": {
                "id": draft_row["id"],
                "status": draft_row["status"],
                "version": int(draft_row["version"]),
                "posted_voucher_id": posted["id"] if posted else None,
                "voucher_no": int(posted["voucher_no"]) if posted else None,
            }
            if draft_row
            else None,
            "total_cents": int(batch["total_cents"]),
            "row_count": int(batch["row_count"]),
            "skipped_count": int(batch["skipped_count"]),
            "policy": json.loads(batch["policy_json"]),
            "policy_fingerprint": batch["policy_fingerprint"],
            "source_fingerprint": batch["source_fingerprint"],
            "batch_fingerprint": batch["batch_fingerprint"],
            "stale": stale,
            "stale_reason": (
                identity_issues[0]
                if identity_issues
                else "来源已更新或过期，旧审批与导出已失效，请失效后重新生成。"
            )
            if stale
            else "",
            "source_files": source_files,
            "rows": [dict(r) for r in rows],
            "exceptions": [dict(r) for r in rows if r["status"] != "adopted"],
            "by_driver": grouped("driver_id"),
            "by_vehicle": grouped("vehicle_id"),
        }
