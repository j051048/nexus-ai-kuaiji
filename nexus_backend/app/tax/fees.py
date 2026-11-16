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
from ..accounting.models import ApprovalRequest
from ..accounting.service import AccountingService, now
from ..ledgers.service import LedgersService
from .fee_guard import transport_issues, transport_snapshot

MAX_LINES = 2400


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cents(value, *, field):
    text = str(value if value is not None else "").strip().replace(",", "").replace("，", "")
    if text in {"", "-", "—"}:
        return 0
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise AccountingError(f"{field}金额格式无效", 422) from error
    if not number.is_finite() or abs(number) > Decimal('999999999999.99') or number.as_tuple().exponent < -2:
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
    return {"version": version, "driver_receivable_account": receivable,
            "vehicle_revenue_account": revenue}


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
                row = conn.execute("SELECT code,category,normal_balance FROM accounts WHERE book_id=? AND code=? AND active=1", (book_id, code)).fetchone()
                if not row:
                    raise AccountingError(f"规费科目 {code} 不存在或已停用", 409)
                category, normal = ("asset", "debit") if code == policy["driver_receivable_account"] else ("income", "credit")
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
                    if not reason and not conn.execute(
                        "SELECT 1 FROM transport_assignments WHERE firm_id=? AND company_id=? AND vehicle_id=? AND driver_id=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>=?)",
                        (actor.firm_id, book["company_id"], vehicle["id"], driver["id"], day, day),
                    ).fetchone():
                        reason = "当日无有效的车辆-驾驶员班次核定"
                    if not reason:
                        key = (day, vehicle_ext, driver_ext)
                        try:
                            if key not in transport_cache:
                                transport_cache[key] = self.ledgers.resolve_transport_in_transaction(
                                    conn, actor, book_id, day,
                                    vehicle_external_id=vehicle_ext, driver_external_id=driver_ext,
                                )
                            identity = transport_snapshot(transport_cache[key], source["id"], row_number)
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
                    batch_rows.append({
                        "id": row_id, "source_file_id": source["id"], "source_row_number": row_number,
                        "external_id": external_id, "driver_id": driver["id"] if driver else None,
                        "vehicle_id": vehicle["id"] if vehicle else None, "gross_cents": gross,
                        "discount_cents": discount, "adjustment_cents": adjustment, "net_cents": net,
                        "status": status, "reason": reason,
                    })
                    if status == "adopted" and adjustment:
                        adjustment_reason = _raw(row, "adjustment_reason", "调整原因")
                        if not adjustment_reason:
                            batch_rows[-1].update(status="rejected", reason="调整缺少源行理由", net_cents=0)
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
                (identities[(item["source_file_id"], item["source_row_number"])] for item in adopted),
                key=lambda item: (item["source_file_id"], item["source_row_number"]),
            )
            batch_fingerprint = _digest({"period": period, "sources": source_fingerprint,
                                         "policy": policy, "transport": transport_facts})
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
                (source_id, book_id, f"tax-fee:{batch_id}", "出租车规费批次", f"{period}-01",
                 f"规费批次 {batch_id}", "驾驶员/车辆规费", total, "income",
                 f"tax-fee-batch:{batch_id}", batch_fingerprint, "bank"),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (draft_id, book_id, source_id, f"出租车规费 {period}",
                 f"按显式策略 {policy['version']} 和已采用来源生成；批次 {batch_id}；须独立复核。"),
            )
            ordinal = 0
            ordinal_by_row = {}
            auxiliaries = []
            for item in adopted:
                for role in ("receivable", "revenue"):
                    code = policy["driver_receivable_account"] if role == "receivable" else policy["vehicle_revenue_account"]
                    debit, credit = (item["net_cents"], 0) if role == "receivable" else (0, item["net_cents"])
                    conn.execute("INSERT INTO draft_lines VALUES(?,?,?,?,?,?)", (draft_id, book_id, ordinal, code, debit, credit))
                    conn.execute(
                        "INSERT INTO draft_line_sources(draft_id,book_id,ordinal,source_file_id,source_row_number,driver_id,vehicle_id,role,amount_cents) VALUES(?,?,?,?,?,?,?,?,?)",
                        (draft_id, book_id, ordinal, item["source_file_id"], item["source_row_number"],
                         item["driver_id"], item["vehicle_id"], role, item["net_cents"]),
                    )
                    ordinal_by_row.setdefault(item["id"], ordinal)
                    dimension = "counterparty" if role == "receivable" else "project"
                    auxiliaries.append({dimension: identities[(item["source_file_id"], item["source_row_number"])][dimension]["id"]})
                    ordinal += 1
            CatalogService(self.db).save_auxiliaries_in_transaction(
                conn, actor, book_id, draft_id, auxiliaries,
            )
            conn.execute(
                "INSERT INTO tax_fee_batches(id,firm_id,company_id,book_id,period,version,draft_id,policy_json,policy_fingerprint,source_fingerprint,batch_fingerprint,total_cents,row_count,skipped_count,created_by,created_at,transport_snapshot_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch_id, actor.firm_id, book["company_id"], book_id, period, 1, draft_id,
                 json.dumps(policy, ensure_ascii=False, sort_keys=True), _digest(policy), source_fingerprint,
                 batch_fingerprint, total, len(batch_rows), sum(1 for r in batch_rows if r["status"] != "adopted"), actor.user_id, stamp,
                 json.dumps(transport_facts, ensure_ascii=False, sort_keys=True)),
            )
            for item in batch_rows:
                conn.execute(
                    "INSERT INTO tax_fee_batch_rows(id,batch_id,source_file_id,source_row_number,external_id,driver_id,vehicle_id,gross_cents,discount_cents,adjustment_cents,net_cents,status,reason,line_ordinal) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], batch_id, item["source_file_id"], item["source_row_number"], item["external_id"],
                     item["driver_id"], item["vehicle_id"], item["gross_cents"], item["discount_cents"],
                     item["adjustment_cents"], item["net_cents"], item["status"], item["reason"], ordinal_by_row.get(item["id"])),
                )
            conn.execute("UPDATE tax_fee_batches SET source_snapshot_json=? WHERE id=?",
                         (json.dumps(sorted((s["id"], s["fingerprint"]) for s in sources)), batch_id))
            return self._view(conn, conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch_id,)).fetchone())

    def list_batches(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            rows = conn.execute("SELECT * FROM tax_fee_batches WHERE book_id=? ORDER BY created_at DESC", (book_id,)).fetchall()
            return {"items": [self._view(conn, row) for row in rows]}

    def get(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            row = conn.execute("SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)).fetchone()
            if not row:
                raise AccountingError("规费批次不存在", 404)
            return self._view(conn, row)

    def review(self, actor, book_id, batch_id, *, expected_version, reason):
        if not str(reason or "").strip():
            raise AccountingError("独立复核必须填写理由", 422)
        accounting = AccountingService(self.db)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            accounting._open(book)
            batch = conn.execute("SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)).fetchone()
            if not batch:
                raise AccountingError("规费批次不存在", 404)
            if batch["status"] != "draft" or int(batch["version"]) != expected_version:
                raise AccountingError("规费批次已复核、失效或版本已变化", 409)
            if batch["created_by"] == actor.user_id:
                raise AccountingError("制单人与复核人必须不同，复核未执行", 409)
            identity_issues = transport_issues(conn, batch)
            if identity_issues:
                raise AccountingError(identity_issues[0], 409)
            used = self._snapshot_sources(batch)
            current = []
            for item in used:
                source = conn.execute("SELECT status,fingerprint FROM tax_source_files WHERE id=? AND book_id=?", (item["source_file_id"], book_id)).fetchone()
                if not source or source["status"] != "active":
                    raise AccountingError("来源已过期，规费批次不能复核或导出", 409)
                current.append((item["source_file_id"], source["fingerprint"]))
            if _digest(sorted(current)) != batch["source_fingerprint"]:
                raise AccountingError("来源已变化，旧批次审批与导出已失效", 409)
            draft = accounting.get_draft(actor, book_id, batch["draft_id"])
            approved = accounting._approve_in_transaction(
                conn, actor, book_id, batch["draft_id"], draft["version"], None, reason,
            )
            changed = conn.execute(
                "UPDATE tax_fee_batches SET status='reviewed',version=version+1,reviewed_by=?,reviewed_at=? WHERE id=? AND status='draft' AND version=?",
                (actor.user_id, now(), batch_id, expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("规费批次状态已变化", 409)
            return self._view(conn, conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch_id,)).fetchone()) | {"draft": approved}

    def validate(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.ledgers.access.book(conn, actor, book_id, "read")
            batch = conn.execute("SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)).fetchone()
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
                source = conn.execute("SELECT status,fingerprint FROM tax_source_files WHERE id=? AND book_id=?", (item["source_file_id"], book_id)).fetchone()
                if not source or source["status"] != "active":
                    raise AccountingError("来源已过期，规费批次不能复核或导出", 409)
                current.append((item["source_file_id"], source["fingerprint"]))
            if _digest(sorted(current)) != batch["source_fingerprint"]:
                raise AccountingError("来源已变化，旧批次审批与导出已失效", 409)
            return self._view(conn, batch)

    def invalidate(self, actor, book_id, batch_id, *, expected_version, reason):
        if not str(reason or "").strip():
            raise AccountingError("失效必须填写理由", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self.ledgers.access.book(conn, actor, book_id, "review")
            self.ledgers.access.company(conn, actor, book["company_id"], manage=True)
            AccountingService(self.db)._open(book)
            batch = conn.execute("SELECT * FROM tax_fee_batches WHERE id=? AND book_id=?", (batch_id, book_id)).fetchone()
            if not batch:
                raise AccountingError("规费批次不存在", 404)
            if int(batch["version"]) != expected_version:
                raise AccountingError("规费批次版本已变化", 409)
            if batch["status"] == "invalidated":
                return self._view(conn, batch)
            conn.execute("UPDATE tax_fee_batches SET status='invalidated',version=version+1 WHERE id=?", (batch_id,))
            book = conn.execute("SELECT * FROM books WHERE id=? AND firm_id=?", (book_id, actor.firm_id)).fetchone()
            self.ledgers._history(conn, actor, book, batch_id, "tax.fee.invalidate", {"reason": reason, "before": dict(batch)})
            return self._view(conn, conn.execute("SELECT * FROM tax_fee_batches WHERE id=?", (batch_id,)).fetchone())

    @staticmethod
    def _snapshot_sources(batch):
        return [{"source_file_id": item[0]} for item in json.loads(batch["source_snapshot_json"])]

    def _view(self, conn, batch):
        rows = conn.execute("SELECT * FROM tax_fee_batch_rows WHERE batch_id=? ORDER BY source_file_id,source_row_number", (batch["id"],)).fetchall()
        used = self._snapshot_sources(batch)
        current = []
        for item in used:
            source = conn.execute("SELECT status,fingerprint FROM tax_source_files WHERE id=? AND book_id=?", (item["source_file_id"], batch["book_id"])).fetchone()
            if not source or source["status"] != "active":
                current = None
                break
            current.append((item["source_file_id"], source["fingerprint"]))
        identity_issues = transport_issues(conn, batch)
        stale = current is None or _digest(sorted(current)) != batch["source_fingerprint"] or bool(identity_issues)
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
                    str(row[key]), {"id": str(row[key]), "name": names.get(str(row[key]), ""), "row_count": 0, "net_cents": 0}
                )
                item["row_count"] += 1
                item["net_cents"] += int(row["net_cents"])
            return sorted(totals.values(), key=lambda item: (item["name"] or item["id"], item["id"]))

        return {
            "id": batch["id"], "book_id": batch["book_id"], "period": batch["period"], "status": batch["status"],
            "version": int(batch["version"]), "draft_id": batch["draft_id"], "total_cents": int(batch["total_cents"]),
            "row_count": int(batch["row_count"]), "skipped_count": int(batch["skipped_count"]),
            "policy": json.loads(batch["policy_json"]), "policy_fingerprint": batch["policy_fingerprint"],
            "source_fingerprint": batch["source_fingerprint"], "batch_fingerprint": batch["batch_fingerprint"],
            "stale": stale,
            "stale_reason": (identity_issues[0] if identity_issues else "来源已更新或过期，旧审批与导出已失效，请失效后重新生成。") if stale else "",
            "source_files": source_files,
            "rows": [dict(r) for r in rows], "exceptions": [dict(r) for r in rows if r["status"] != "adopted"],
            "by_driver": grouped("driver_id"), "by_vehicle": grouped("vehicle_id"),
        }
