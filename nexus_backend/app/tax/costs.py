"""Deterministic vehicle repair, insurance and approved-OA cost batches.

The service never invents an industry rule: repair invoices carry approved
evidence, insurance policies carry an explicit allocation algorithm, and OA
costs come from an approved snapshot with its business id and version. Every
batch still needs an independent accounting review before export.
"""

import hashlib
import json
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money, money_to_cents, validate_period
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _hash64(value, name):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value.lower())
    ):
        raise AccountingError(f"{name} 必须是 64 位十六进制 hash", 422)
    return value.lower()


def _positive_cents(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AccountingError(f"{name} 必须是正整数分", 422)
    return value


def _month_index(period):
    year, month = map(int, period.split("-"))
    return year * 12 + month - 1


class TaxCostSourceWrite(InputModel):
    kind: StrictStr
    source_key: StrictStr = Field(min_length=1, max_length=120)
    source_version: int = Field(strict=True, ge=1)
    period: StrictStr
    subject_label: StrictStr = Field(default="", max_length=200)
    original_ref: StrictStr = Field(min_length=1, max_length=500)
    sha256: StrictStr
    amount: StrictStr
    payload: dict = Field(default_factory=dict)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value):
        if value not in {"repair", "insurance", "oa"}:
            raise ValueError("类型必须是 repair、insurance 或 oa")
        return value


class TaxCostPolicyWrite(InputModel):
    version: StrictStr = Field(min_length=1, max_length=80)
    expense_account_code: StrictStr = Field(min_length=1, max_length=40)
    credit_account_code: StrictStr = Field(min_length=1, max_length=40)


class TaxCostGenerate(InputModel):
    period: StrictStr
    kind: StrictStr
    source_ids: list[StrictStr] = Field(min_length=1, max_length=500)
    policy: TaxCostPolicyWrite
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("period")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class TaxCostReview(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    review_reason: StrictStr = Field(min_length=3, max_length=1000)


class TaxCostInvalidate(InputModel):
    expected_version: int = Field(strict=True, ge=1)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class TaxCostService:
    MAX_SOURCES = 500

    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _book(self, conn, actor, book_id, write=False):
        return self.accounting.access.book(conn, actor, book_id, "review" if write else "read")

    @staticmethod
    def _source_fingerprint(kind, key, version, period, sha256, amount_cents, payload):
        return _fingerprint(
            {
                "kind": kind,
                "source_key": key,
                "source_version": version,
                "period": period,
                "sha256": sha256,
                "amount_cents": amount_cents,
                "payload": payload,
            }
        )

    @staticmethod
    def _validate_payload(kind, period, amount_cents, payload):
        if not isinstance(payload, dict):
            raise AccountingError("payload 必须是对象", 422)
        if kind == "repair":
            invoice_ref = str(payload.get("invoice_ref") or "").strip()
            if not invoice_ref:
                raise AccountingError("维修来源必须保留发票或结算单编号", 422)
            vehicle = str(payload.get("vehicle_external_id") or "").strip()
            if not vehicle:
                raise AccountingError("维修来源必须绑定车辆", 422)
            rate = payload.get("rate_cents")
            quantity = payload.get("quantity_milli")
            if rate is not None:
                rate = _positive_cents(rate, "费率")
                quantity = _positive_cents(quantity, "数量")
                computed = rate * quantity // 1000
                if computed != amount_cents:
                    raise AccountingError("费率×数量与维修金额不一致，请按原件核定", 422)
            elif quantity is not None:
                _positive_cents(quantity, "数量")
                if not str(payload.get("rate_card_id") or "").strip():
                    raise AccountingError("只给数量时必须引用有效费率卡", 422)
            coverage = payload.get("coverage")
            if coverage not in {"package", "per_vehicle", "actual"}:
                raise AccountingError("维修覆盖方式必须显式选择包干、按车或实际结算", 422)
            if coverage == "package" and not str(payload.get("coverage_scope") or "").strip():
                raise AccountingError("包干维修必须写明覆盖范围，不能只写“包干”", 422)
        elif kind == "insurance":
            premium = _positive_cents(payload.get("premium_cents"), "保费")
            try:
                start = validate_period(str(payload.get("start_period") or ""))
                end = validate_period(str(payload.get("end_period") or ""))
            except (TypeError, ValueError):
                raise AccountingError("保险起止期间格式应为 YYYY-MM", 422) from None
            total = payload.get("total_months")
            if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
                raise AccountingError("保险期间月数必须显式登记", 422)
            if _month_index(end) - _month_index(start) + 1 != total:
                raise AccountingError("保险起止期间与总月数不一致", 422)
            if _month_index(period) < _month_index(start) or _month_index(period) > _month_index(
                end
            ):
                raise AccountingError("当前期间不在保单有效期内", 422)
            algorithm = payload.get("algorithm")
            allocation = payload.get("allocation_cents")
            base, remainder = divmod(premium, total)
            if algorithm == "straight_line":
                expected = base + (remainder if period == end else 0)
                if amount_cents != expected:
                    raise AccountingError("保险月摊金额与直线法应得金额不一致", 422)
            elif algorithm == "manual":
                if not isinstance(allocation, dict) or str(amount_cents) != str(
                    allocation.get(period)
                ):
                    raise AccountingError("人工保险分摊必须逐期列明当前期间金额", 422)
                try:
                    allocated = sum(int(value) for value in allocation.values())
                except (TypeError, ValueError):
                    raise AccountingError("人工保险分摊金额必须是整数分", 422) from None
                if allocated != premium:
                    raise AccountingError("人工保险分摊合计必须等于保费", 422)
            else:
                raise AccountingError("保险分摊算法必须由客户明确为 straight_line 或 manual", 422)
            if not str(payload.get("policy_no") or "").strip():
                raise AccountingError("保险来源必须保留保单号", 422)
        elif kind == "oa":
            if not str(payload.get("oa_event_id") or "").strip():
                raise AccountingError("OA 来源必须保留业务事件 ID", 422)
            version = payload.get("oa_version")
            if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
                raise AccountingError("OA 审批快照版本必须显式登记", 422)
            if not str(payload.get("approved_by") or "").strip():
                raise AccountingError("OA 来源必须保留核定人", 422)
            if payload.get("approved_period") != period:
                raise AccountingError("OA 核定期间与当前批次期间不一致", 422)
            lines = payload.get("lines")
            if not isinstance(lines, list) or not lines:
                raise AccountingError("OA 核定明细不能为空", 422)
            total = 0
            seen = set()
            for line in lines:
                code = str(line.get("account_code") or "").strip()
                if not code:
                    raise AccountingError("OA 明细必须写明科目", 422)
                if code in seen:
                    raise AccountingError("OA 明细科目重复，请按核定快照合并", 422)
                seen.add(code)
                value = _positive_cents(line.get("amount_cents"), f"OA 科目 {code} 金额")
                total += value
            if total != amount_cents:
                raise AccountingError("OA 汇总金额与明细合计不一致", 422)
        else:
            raise AccountingError("不支持的成本类型", 422)

    @staticmethod
    def _repair_coverage_guard(conn, book_id, period, payload):
        incoming = payload.get("coverage")
        vehicle = str(payload.get("vehicle_external_id") or "").strip()
        if incoming not in {"package", "actual"}:
            return
        rows = conn.execute(
            "SELECT payload_json FROM tax_cost_sources "
            "WHERE book_id=? AND kind='repair' AND period=? AND status='active'",
            (book_id, period),
        ).fetchall()
        for row in rows:
            try:
                existing = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if str(existing.get("vehicle_external_id") or "").strip() != vehicle:
                continue
            previous = existing.get("coverage")
            if previous == "package" and incoming == "actual":
                reason = str(payload.get("package_exclusion_reason") or "").strip()
                if len(reason) < 3:
                    raise AccountingError(
                        "该车本期已有包干维修，实际结算必须注明包干未覆盖的项目和原因", 409
                    )
            if previous == "actual" and incoming == "package":
                reason = str(payload.get("package_switch_reason") or "").strip()
                if len(reason) < 3:
                    raise AccountingError(
                        "该车本期已有实际维修，改用包干必须注明切换生效点和原因", 409
                    )

    @staticmethod
    def _rate_card_guard(conn, book_id, period, payload, amount_cents):
        rate_id = str(payload.get("rate_card_id") or "").strip()
        if not rate_id:
            return
        vehicle_type = str(payload.get("vehicle_type") or "").strip()
        quantity = payload.get("quantity_milli")
        if not vehicle_type or not isinstance(quantity, int) or quantity <= 0:
            raise AccountingError("引用费率卡时必须提供车型和数量", 422)
        card = conn.execute(
            "SELECT * FROM repair_rate_cards WHERE id=? AND book_id=? AND vehicle_type=? "
            "AND status IN ('active','superseded') AND valid_from<=? AND valid_to>=?",
            (rate_id, book_id, vehicle_type, period, period),
        ).fetchone()
        if not card:
            raise AccountingError("费率卡不存在、车型不符或不在本期间有效", 409)
        used_version = payload.get("rate_version")
        if used_version is not None and int(used_version) != int(card["version"]):
            raise AccountingError("费率卡版本已变化，请重新生成成本批次", 409)
        expected = int(card["rate_cents"]) * quantity // 1000
        if expected != amount_cents:
            raise AccountingError("维修金额与费率卡×数量不一致", 409)

    def add_source(self, actor, book_id, request: TaxCostSourceWrite):
        amount = money_to_cents(request.amount)
        if amount <= 0:
            raise AccountingError("金额必须大于零", 422)
        sha = _hash64(request.sha256, "原件 hash")
        self._validate_payload(request.kind, request.period, amount, request.payload)
        if request.kind == "repair" and request.source_key != str(
            request.payload.get("invoice_ref") or ""
        ):
            raise AccountingError("维修来源键必须是发票或结算单编号，防止同一单据重复入账", 422)
        if request.kind == "oa":
            if request.source_key != str(request.payload.get("oa_event_id") or ""):
                raise AccountingError("OA 来源键必须是业务事件 ID", 422)
            if request.source_version != request.payload.get("oa_version"):
                raise AccountingError("OA 来源版本必须等于审批快照版本", 422)
        fingerprint = self._source_fingerprint(
            request.kind,
            request.source_key,
            request.source_version,
            request.period,
            sha,
            amount,
            request.payload,
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            if request.period != book["period"]:
                raise AccountingError("来源期间与当前账套期间不一致", 422)
            existing = conn.execute(
                "SELECT * FROM tax_cost_sources WHERE book_id=? AND kind=? AND source_key=? AND source_version=?",
                (book_id, request.kind, request.source_key, request.source_version),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise AccountingError("同一来源版本的内容已变化，请提升版本", 409)
                return self._source_view(existing, replayed=True)
            if request.kind == "repair":
                self._repair_coverage_guard(conn, book_id, request.period, request.payload)
                self._rate_card_guard(conn, book_id, request.period, request.payload, amount)
            if request.kind == "insurance":
                policy_no = str(request.payload.get("policy_no") or "").strip()
                for row in conn.execute(
                    "SELECT source_key,payload_json FROM tax_cost_sources "
                    "WHERE book_id=? AND kind='insurance' AND period=? AND status='active'",
                    (book_id, request.period),
                ).fetchall():
                    if row["source_key"] == request.source_key:
                        continue
                    try:
                        other = json.loads(row["payload_json"])
                    except (TypeError, ValueError):
                        continue
                    if str(other.get("policy_no") or "").strip() == policy_no:
                        raise AccountingError(
                            "同一保单同一期间已有保险分摊来源，请按原来源更正版本，不能重复计提",
                            409,
                        )
            latest = conn.execute(
                "SELECT MAX(source_version) FROM tax_cost_sources WHERE book_id=? AND kind=? AND source_key=?",
                (book_id, request.kind, request.source_key),
            ).fetchone()[0]
            if latest and request.source_version <= int(latest):
                raise AccountingError("来源版本必须高于已登记版本", 409)
            conn.execute(
                "UPDATE tax_cost_sources SET status='superseded' "
                "WHERE book_id=? AND kind=? AND source_key=? AND status='active'",
                (book_id, request.kind, request.source_key),
            )
            source_id = uuid4().hex
            conn.execute(
                "INSERT INTO tax_cost_sources(id,firm_id,company_id,book_id,kind,source_key,source_version,"
                "period,subject_label,original_ref,sha256,amount_cents,payload_json,status,fingerprint,"
                "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                (
                    source_id,
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    request.kind,
                    request.source_key,
                    request.source_version,
                    request.period,
                    request.subject_label,
                    request.original_ref,
                    sha,
                    amount,
                    json.dumps(request.payload, ensure_ascii=False, sort_keys=True),
                    fingerprint,
                    actor.user_id,
                    now(),
                ),
            )
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.cost.source",
                source_id,
                {
                    "kind": request.kind,
                    "version": request.source_version,
                    "fingerprint": fingerprint,
                },
            )
            row = conn.execute("SELECT * FROM tax_cost_sources WHERE id=?", (source_id,)).fetchone()
            return self._source_view(row)

    @staticmethod
    def _source_view(row, replayed=False):
        return {
            "id": row["id"],
            "kind": row["kind"],
            "source_key": row["source_key"],
            "source_version": int(row["source_version"]),
            "period": row["period"],
            "subject_label": row["subject_label"],
            "original_ref": row["original_ref"],
            "sha256": row["sha256"],
            "amount": cents_to_money(int(row["amount_cents"])),
            "amount_cents": int(row["amount_cents"]),
            "payload": json.loads(row["payload_json"]),
            "status": row["status"],
            "fingerprint": row["fingerprint"],
            "replayed": replayed,
        }

    def list_sources(self, actor, book_id, kind=None, period=None):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            sql = "SELECT * FROM tax_cost_sources WHERE book_id=?"
            args = [book_id]
            if kind:
                sql += " AND kind=?"
                args.append(kind)
            if period:
                sql += " AND period=?"
                args.append(period)
            sql += " ORDER BY created_at,id"
            return {"items": [self._source_view(row) for row in conn.execute(sql, args)]}

    def _policy(self, conn, book_id, request: TaxCostPolicyWrite):
        accounts = {
            row["code"]
            for row in conn.execute(
                "SELECT code FROM accounts WHERE book_id=? AND active=1", (book_id,)
            )
        }
        for code in (request.expense_account_code, request.credit_account_code):
            if code not in accounts:
                raise AccountingError(f"科目 {code} 不存在或已停用", 409)
        if request.expense_account_code == request.credit_account_code:
            raise AccountingError("费用与贷方科目不能相同", 422)
        value = request.model_dump()
        return value, _fingerprint(value)

    def generate(self, actor, book_id, request: TaxCostGenerate):
        with self.db.transaction(write=True, actor=actor) as conn:
            book = self._book(conn, actor, book_id, write=True)
            if request.period != book["period"]:
                raise AccountingError("批次期间与当前账套期间不一致", 422)
            if request.kind not in {"repair", "insurance", "oa"}:
                raise AccountingError("不支持的成本类型", 422)
            ids = list(dict.fromkeys(request.source_ids))
            if len(ids) > self.MAX_SOURCES:
                raise AccountingError("单批来源数量超出上限", 413)
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT * FROM tax_cost_sources WHERE book_id=? AND kind=? AND id IN ({placeholders}) ORDER BY id",
                (book_id, request.kind, *ids),
            ).fetchall()
            if len(rows) != len(ids):
                raise AccountingError("来源不存在、已过期或不属于本账套", 409)
            for row in rows:
                if row["status"] != "active" or row["period"] != request.period:
                    raise AccountingError("来源已更新或不属于当前期间，请重新核定", 409)
            policy, policy_fp = self._policy(conn, book_id, request.policy)
            source_fp = _fingerprint(
                [{"id": row["id"], "fingerprint": row["fingerprint"]} for row in rows]
            )
            batch_fp = _fingerprint(
                {
                    "kind": request.kind,
                    "period": request.period,
                    "source": source_fp,
                    "policy": policy_fp,
                }
            )
            prior = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE book_id=? AND batch_fingerprint=?",
                (book_id, batch_fp),
            ).fetchone()
            if prior:
                return self._batch_view(conn, actor, prior, replayed=True)
            total = sum(int(row["amount_cents"]) for row in rows)
            debit_total = 0
            lines = []
            row_links = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                if request.kind == "oa":
                    for line in payload["lines"]:
                        amount = int(line["amount_cents"])
                        lines.append((line["account_code"], amount, 0))
                        debit_total += amount
                    row_links.append((row["id"], len(lines)))
                else:
                    amount = int(row["amount_cents"])
                    lines.append((policy["expense_account_code"], amount, 0))
                    debit_total += amount
                    row_links.append((row["id"], len(lines)))
            lines.append((policy["credit_account_code"], 0, total))
            if debit_total != total:
                raise AccountingError("成本批次借贷合计不一致", 409)
            batch_id, source_id, draft_id = uuid4().hex, uuid4().hex, uuid4().hex
            stamp = now()
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,"
                "amount_cents,direction,evidence_ref,fingerprint,source_kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    book_id,
                    f"tax-cost:{batch_id}",
                    "出租车成本批次",
                    f"{request.period}-01",
                    f"成本批次 {batch_id}",
                    "车辆维修/保险/核定费用",
                    total,
                    "expense",
                    f"tax-cost-batch:{batch_id}",
                    batch_fp,
                    "bank",
                ),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,explanation) VALUES(?,?,?,?,?)",
                (
                    draft_id,
                    book_id,
                    source_id,
                    f"{request.kind} 成本 {request.period}",
                    f"按显式政策 {policy['version']} 与已核定来源生成；批次 {batch_id}；须独立复核。",
                ),
            )
            for ordinal, (code, debit, credit) in enumerate(lines, start=1):
                conn.execute(
                    "INSERT INTO draft_lines VALUES(?,?,?,?,?,?)",
                    (draft_id, book_id, ordinal, code, debit, credit),
                )
            conn.execute(
                "INSERT INTO tax_cost_batches(id,firm_id,company_id,book_id,kind,period,status,version,"
                "draft_id,policy_json,policy_fingerprint,source_fingerprint,batch_fingerprint,amount_cents,"
                "row_count,created_by,created_at) VALUES(?,?,?,?,?,?,'draft',1,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id,
                    book["firm_id"],
                    book["company_id"],
                    book_id,
                    request.kind,
                    request.period,
                    draft_id,
                    json.dumps(policy, ensure_ascii=False, sort_keys=True),
                    policy_fp,
                    source_fp,
                    batch_fp,
                    total,
                    len(rows),
                    actor.user_id,
                    stamp,
                ),
            )
            for index, row in enumerate(rows, start=1):
                link_ordinal = next(value for source, value in row_links if source == row["id"])
                conn.execute(
                    "INSERT INTO tax_cost_batch_rows(id,batch_id,source_id,row_number,amount_cents,"
                    "status,reason,line_ordinal) VALUES(?,?,?,?,?,'adopted','',?)",
                    (
                        uuid4().hex,
                        batch_id,
                        row["id"],
                        index,
                        int(row["amount_cents"]),
                        link_ordinal,
                    ),
                )
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.cost.generate",
                batch_id,
                {"kind": request.kind, "amount_cents": total, "sources": len(rows)},
            )
            batch = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=?", (batch_id,)
            ).fetchone()
            return self._batch_view(conn, actor, batch)

    def list_batches(self, actor, book_id, kind=None):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            sql = "SELECT * FROM tax_cost_batches WHERE book_id=?"
            args = [book_id]
            if kind:
                sql += " AND kind=?"
                args.append(kind)
            sql += " ORDER BY created_at DESC"
            return {
                "items": [self._batch_view(conn, actor, row) for row in conn.execute(sql, args)]
            }

    def get_batch(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("成本批次不存在或无权访问", 404)
            return self._batch_view(conn, actor, row)

    def _sources_current(self, conn, row):
        sources = conn.execute(
            "SELECT s.* FROM tax_cost_batch_rows r JOIN tax_cost_sources s ON s.id=r.source_id "
            "WHERE r.batch_id=?",
            (row["id"],),
        ).fetchall()
        current = (
            conn.execute(
                "SELECT * FROM tax_cost_sources WHERE id IN ("
                + ",".join("?" for _ in sources)
                + ")",
                tuple(source["id"] for source in sources),
            ).fetchall()
            if sources
            else []
        )
        current_by_id = {source["id"]: source for source in current}
        if len(current) != len(sources):
            return False
        for source in sources:
            now_source = current_by_id.get(source["id"])
            if (
                now_source is None
                or now_source["status"] != "active"
                or now_source["period"] != row["period"]
                or now_source["fingerprint"] != source["fingerprint"]
            ):
                return False
            try:
                payload = json.loads(now_source["payload_json"] or "{}")
                self._rate_card_guard(
                    conn,
                    row["book_id"],
                    row["period"],
                    payload,
                    int(now_source["amount_cents"]),
                )
            except (AccountingError, TypeError, ValueError):
                return False
        return True

    def _batch_view(self, conn, actor, row, replayed=False):
        sources = conn.execute(
            "SELECT s.* FROM tax_cost_batch_rows r JOIN tax_cost_sources s ON s.id=r.source_id "
            "WHERE r.batch_id=? ORDER BY r.row_number",
            (row["id"],),
        ).fetchall()
        links = conn.execute(
            "SELECT * FROM tax_cost_batch_rows WHERE batch_id=? ORDER BY row_number", (row["id"],)
        ).fetchall()
        draft = conn.execute("SELECT * FROM drafts WHERE id=?", (row["draft_id"],)).fetchone()
        current = (
            conn.execute(
                "SELECT s.* FROM tax_cost_sources s WHERE s.id IN ("
                + ",".join("?" for _ in sources)
                + ")",
                tuple(source["id"] for source in sources),
            ).fetchall()
            if sources
            else []
        )
        current_by_id = {source["id"]: source for source in current}
        stale = len(current) != len(sources)
        for source in sources:
            now_source = current_by_id.get(source["id"])
            if (
                now_source is None
                or now_source["status"] != "active"
                or now_source["period"] != row["period"]
                or now_source["fingerprint"] != source["fingerprint"]
            ):
                stale = True
                break
        return {
            "id": row["id"],
            "kind": row["kind"],
            "period": row["period"],
            "status": row["status"],
            "version": int(row["version"]),
            "amount": cents_to_money(int(row["amount_cents"])),
            "amount_cents": int(row["amount_cents"]),
            "row_count": int(row["row_count"]),
            "draft_id": row["draft_id"],
            "draft_status": draft["status"] if draft else None,
            "draft_version": int(draft["version"]) if draft else None,
            "policy": json.loads(row["policy_json"]),
            "source_fingerprint": row["source_fingerprint"],
            "batch_fingerprint": row["batch_fingerprint"],
            "stale": stale,
            "stale_reason": "来源已更新或过期，旧复核与导出已失效" if stale else "",
            "sources": [self._source_view(source) for source in sources],
            "rows": [
                {
                    "source_id": link["source_id"],
                    "row_number": int(link["row_number"]),
                    "amount": cents_to_money(int(link["amount_cents"])),
                    "status": link["status"],
                    "reason": link["reason"],
                    "line_ordinal": link["line_ordinal"],
                }
                for link in links
            ],
            "replayed": replayed,
        }

    def validate(self, actor, book_id, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            row = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("成本批次不存在或无权访问", 404)
            if row["status"] == "invalidated":
                raise AccountingError("批次已失效，请重新生成", 409)
            if not self._sources_current(conn, row):
                raise AccountingError("来源或费率卡已变化，请重新生成成本批次", 409)
            return {"valid": True, "batch_id": batch_id, "status": row["status"]}

    def review(self, actor, book_id, batch_id, request: TaxCostReview):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            row = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("成本批次不存在或无权访问", 404)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("批次版本已变化，复核未提交", 409)
            if row["status"] != "draft":
                raise AccountingError("只有待复核批次可以复核", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("制单人与复核人必须不同，复核未执行", 409)
            if not self._sources_current(conn, row):
                raise AccountingError("来源已变化，请重新生成成本批次", 409)
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (row["draft_id"],)).fetchone()
            if not draft:
                raise AccountingError("批次关联凭证不存在", 409)
            self.accounting._approve_in_transaction(
                conn,
                actor,
                book_id,
                draft["id"],
                int(draft["version"]),
                None,
                request.review_reason,
            )
            changed = conn.execute(
                "UPDATE tax_cost_batches SET status='reviewed',version=version+1,reviewed_by=?,reviewed_at=? "
                "WHERE id=? AND book_id=? AND version=? AND status='draft'",
                (actor.user_id, now(), batch_id, book_id, request.expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("批次版本已变化，复核未提交", 409)
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.cost.review",
                batch_id,
                {
                    "expected_version": request.expected_version,
                    "review_reason": request.review_reason,
                },
            )
            updated = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=?", (batch_id,)
            ).fetchone()
            return self._batch_view(conn, actor, updated)

    def invalidate(self, actor, book_id, batch_id, request: TaxCostInvalidate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            row = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=? AND book_id=?", (batch_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("成本批次不存在或无权访问", 404)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("批次版本已变化，失效未提交", 409)
            if row["status"] == "invalidated":
                raise AccountingError("批次已失效", 409)
            changed = conn.execute(
                "UPDATE tax_cost_batches SET status='invalidated',version=version+1 "
                "WHERE id=? AND book_id=? AND version=? AND status<>'invalidated'",
                (batch_id, book_id, request.expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("批次版本已变化，失效未提交", 409)
            self.accounting._audit(
                conn, actor, book_id, "tax.cost.invalidate", batch_id, {"reason": request.reason}
            )
            updated = conn.execute(
                "SELECT * FROM tax_cost_batches WHERE id=?", (batch_id,)
            ).fetchone()
            return self._batch_view(conn, actor, updated)


class RepairRateWrite(InputModel):
    vehicle_type: StrictStr = Field(min_length=1, max_length=40)
    rate_cents: int = Field(gt=0)
    valid_from: StrictStr
    valid_to: StrictStr
    coverage_scope: StrictStr = Field(min_length=3, max_length=120)
    source_ref: StrictStr = Field(min_length=3, max_length=200)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def valid_period(cls, value):
        return validate_period(value)


class RepairRateDraft(RepairRateWrite):
    unit: StrictStr = Field(min_length=1, max_length=40)
    currency: StrictStr = Field(min_length=1, max_length=8)
    tax_included: bool
    logical_id: StrictStr | None = None
    reason: StrictStr = Field(min_length=3, max_length=500)

    @field_validator("unit")
    @classmethod
    def supported_unit(cls, value):
        if value != "per_vehicle":
            raise ValueError("当前只支持按车辆计量（per_vehicle）")
        return value

    @field_validator("currency")
    @classmethod
    def supported_currency(cls, value):
        if value != "CNY":
            raise ValueError("当前只支持人民币（CNY）")
        return value


class RepairRateDeactivate(InputModel):
    expected_version: int = Field(ge=1)
    reason: StrictStr = Field(min_length=5, max_length=500)


class RepairRateService:
    """Vehicle-type repair rate cards. Rates never apply themselves."""

    def __init__(self, db):
        self.db = db
        self.accounting = TaxCostService(db).accounting

    def add(self, actor, book_id, request: RepairRateWrite):
        if request.valid_from > request.valid_to:
            raise AccountingError("费率生效起始月不能晚于结束月", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            overlap = conn.execute(
                "SELECT id FROM repair_rate_cards WHERE book_id=? AND vehicle_type=? "
                "AND valid_from<=? AND valid_to>=? LIMIT 1",
                (book_id, request.vehicle_type, request.valid_to, request.valid_from),
            ).fetchone()
            if overlap:
                raise AccountingError("同车型费率有效期不能重叠", 409)
            row_id = uuid4().hex
            stamp = now()
            fingerprint = _fingerprint(
                {
                    "book_id": book_id,
                    "vehicle_type": request.vehicle_type,
                    "rate_cents": request.rate_cents,
                    "valid_from": request.valid_from,
                    "valid_to": request.valid_to,
                    "coverage_scope": request.coverage_scope,
                    "source_ref": request.source_ref,
                    "version": 1,
                }
            )
            conn.execute(
                "INSERT INTO repair_rate_cards(id,book_id,vehicle_type,rate_cents,valid_from,valid_to,"
                "coverage_scope,source_ref,created_by,created_at,version,status,confirmed_by,confirmed_at,"
                "activated_at,logical_id,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,1,'active',?,?,?,?,?)",
                (
                    row_id,
                    book_id,
                    request.vehicle_type,
                    request.rate_cents,
                    request.valid_from,
                    request.valid_to,
                    request.coverage_scope,
                    request.source_ref,
                    actor.user_id,
                    stamp,
                    actor.user_id,
                    stamp,
                    stamp,
                    row_id,
                    fingerprint,
                ),
            )
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.rate.legacy_active",
                row_id,
                {"version": 1, "source_ref": request.source_ref},
            )
            return self._view(
                conn.execute("SELECT * FROM repair_rate_cards WHERE id=?", (row_id,)).fetchone()
            )

    def create_draft(self, actor, book_id, request: RepairRateDraft):
        if request.valid_from > request.valid_to:
            raise AccountingError("费率生效起始月不能晚于结束月", 422)
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            logical_id = request.logical_id or uuid4().hex
            if request.logical_id:
                root = conn.execute(
                    "SELECT * FROM repair_rate_cards WHERE book_id=? "
                    "AND (id=? OR logical_id=?) ORDER BY version DESC LIMIT 1",
                    (book_id, request.logical_id, request.logical_id),
                ).fetchone()
                if not root:
                    raise AccountingError("原费率卡不存在或无权访问", 404)
                if root["vehicle_type"] != request.vehicle_type:
                    raise AccountingError("续期或替代不能改变车型", 409)
                logical_id = root["logical_id"] or root["id"]
                version = (
                    int(
                        conn.execute(
                            "SELECT MAX(version) FROM repair_rate_cards "
                            "WHERE book_id=? AND logical_id=?",
                            (book_id, logical_id),
                        ).fetchone()[0]
                    )
                    + 1
                )
            else:
                version = 1
            row_id = uuid4().hex
            stamp = now()
            fingerprint = _fingerprint(
                {
                    "book_id": book_id,
                    "vehicle_type": request.vehicle_type,
                    "rate_cents": request.rate_cents,
                    "valid_from": request.valid_from,
                    "valid_to": request.valid_to,
                    "coverage_scope": request.coverage_scope,
                    "source_ref": request.source_ref,
                    "unit": request.unit,
                    "currency": request.currency,
                    "tax_included": request.tax_included,
                    "logical_id": logical_id,
                    "version": version,
                }
            )
            conn.execute(
                "INSERT INTO repair_rate_cards(id,book_id,vehicle_type,rate_cents,valid_from,valid_to,"
                "coverage_scope,source_ref,created_by,created_at,version,status,reason,unit,currency,"
                "tax_included,logical_id,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?,?)",
                (
                    row_id,
                    book_id,
                    request.vehicle_type,
                    request.rate_cents,
                    request.valid_from,
                    request.valid_to,
                    request.coverage_scope,
                    request.source_ref,
                    actor.user_id,
                    stamp,
                    version,
                    request.reason,
                    request.unit,
                    request.currency,
                    bool(request.tax_included),
                    logical_id,
                    fingerprint,
                ),
            )
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.rate.draft",
                row_id,
                {"version": version, "logical_id": logical_id, "source_ref": request.source_ref},
            )
            return self._view(
                conn.execute("SELECT * FROM repair_rate_cards WHERE id=?", (row_id,)).fetchone()
            )

    def confirm(self, actor, book_id, card_id, expected_version):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            row = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE id=? AND book_id=?", (card_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("费率卡不存在或无权访问", 404)
            if int(row["version"]) != expected_version:
                raise AccountingError("费率卡版本已变化，确认未提交", 409)
            if row["status"] != "draft":
                raise AccountingError("只有草稿费率卡可以确认", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("费率卡拟定人与确认人必须不同", 409)
            stamp = now()
            changed = conn.execute(
                "UPDATE repair_rate_cards SET status='confirmed',confirmed_by=?,confirmed_at=? "
                "WHERE id=? AND book_id=? AND version=? AND status='draft'",
                (actor.user_id, stamp, card_id, book_id, expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("费率卡版本已变化，确认未提交", 409)
            self.accounting._audit(
                conn, actor, book_id, "tax.rate.confirm", card_id, {"version": expected_version}
            )
            return self._view(
                conn.execute("SELECT * FROM repair_rate_cards WHERE id=?", (card_id,)).fetchone()
            )

    def activate(self, actor, book_id, card_id, expected_version):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            row = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE id=? AND book_id=?", (card_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("费率卡不存在或无权访问", 404)
            if int(row["version"]) != expected_version:
                raise AccountingError("费率卡版本已变化，生效未提交", 409)
            if row["status"] != "confirmed":
                raise AccountingError("费率卡未独立确认，不能生效", 409)
            overlap = conn.execute(
                "SELECT id FROM repair_rate_cards WHERE book_id=? AND vehicle_type=? "
                "AND status IN ('active','superseded') AND logical_id<>? "
                "AND valid_from<=? AND valid_to>=? LIMIT 1",
                (
                    book_id,
                    row["vehicle_type"],
                    row["logical_id"],
                    row["valid_to"],
                    row["valid_from"],
                ),
            ).fetchone()
            if overlap:
                raise AccountingError("同车型费率有效期与已生效版本重叠", 409)
            current = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE book_id=? AND logical_id=? "
                "AND status='active' ORDER BY version DESC LIMIT 1",
                (book_id, row["logical_id"]),
            ).fetchone()
            if current and row["valid_from"] <= current["valid_to"]:
                raise AccountingError(
                    "后续版本必须从当前版本结束月之后生效；覆盖同期请先停用旧版本", 409
                )
            stamp = now()
            if current:
                conn.execute(
                    "UPDATE repair_rate_cards SET status='superseded',superseded_at=? WHERE id=?",
                    (stamp, current["id"]),
                )
            changed = conn.execute(
                "UPDATE repair_rate_cards SET status='active',activated_at=? "
                "WHERE id=? AND book_id=? AND version=? AND status='confirmed'",
                (stamp, card_id, book_id, expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("费率卡版本已变化，生效未提交", 409)
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.rate.activate",
                card_id,
                {"version": expected_version, "superseded": current["id"] if current else None},
            )
            return self._view(
                conn.execute("SELECT * FROM repair_rate_cards WHERE id=?", (card_id,)).fetchone()
            )

    def renew(self, actor, book_id, card_id, request: RepairRateDraft):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            current = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE id=? AND book_id=?", (card_id, book_id)
            ).fetchone()
            if not current:
                raise AccountingError("费率卡不存在或无权访问", 404)
            if current["status"] != "active":
                raise AccountingError("只有生效费率卡可以续期", 409)
            if request.valid_from <= current["valid_to"]:
                raise AccountingError("续期起始月必须晚于当前版本结束月", 409)
            payload = request.model_copy(update={"logical_id": current["logical_id"]})
        return self.create_draft(actor, book_id, payload)

    def deactivate(self, actor, book_id, card_id, request: RepairRateDeactivate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._book(conn, actor, book_id, write=True)
            row = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE id=? AND book_id=?", (card_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("费率卡不存在或无权访问", 404)
            if int(row["version"]) != request.expected_version:
                raise AccountingError("费率卡版本已变化，停用未提交", 409)
            if row["status"] not in {"active", "confirmed"}:
                raise AccountingError("当前费率版本不可停用", 409)
            stamp = now()
            changed = conn.execute(
                "UPDATE repair_rate_cards SET status='inactive',superseded_at=?,reason=? "
                "WHERE id=? AND book_id=? AND version=? AND status IN ('active','confirmed')",
                (
                    stamp,
                    request.reason,
                    card_id,
                    book_id,
                    request.expected_version,
                ),
            )
            if changed.rowcount != 1:
                raise AccountingError("费率卡版本已变化，停用未提交", 409)
            self.accounting._audit(
                conn,
                actor,
                book_id,
                "tax.rate.deactivate",
                card_id,
                {"version": request.expected_version, "reason": request.reason},
            )
            return self._view(
                conn.execute("SELECT * FROM repair_rate_cards WHERE id=?", (card_id,)).fetchone()
            )

    def history(self, actor, book_id, logical_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE book_id=? AND logical_id=? ORDER BY version",
                (book_id, logical_id),
            ).fetchall()
            if not rows:
                raise AccountingError("费率卡历史不存在或无权访问", 404)
            return {"items": [self._view(row) for row in rows]}

    def impact(self, actor, book_id, card_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            if not conn.execute(
                "SELECT 1 FROM repair_rate_cards WHERE id=? AND book_id=?", (card_id, book_id)
            ).fetchone():
                raise AccountingError("费率卡不存在或无权访问", 404)
            sources = conn.execute(
                "SELECT id,source_key,period,status FROM tax_cost_sources "
                "WHERE book_id=? AND kind='repair' AND payload_json LIKE ?",
                (book_id, f'%"{card_id}"%'),
            ).fetchall()
            return {
                "items": [
                    {
                        "source_id": row["id"],
                        "source_key": row["source_key"],
                        "period": row["period"],
                        "status": row["status"],
                    }
                    for row in sources
                ],
                "note": "只读影响预览；停用后待复核批次会在复核时被阻断。",
            }

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM repair_rate_cards WHERE book_id=? ORDER BY vehicle_type,valid_from",
                (book_id,),
            ).fetchall()
            return {"items": [self._view(row) for row in rows]}

    def resolve(self, actor, book_id, vehicle_type, period):
        with self.db.transaction(actor=actor) as conn:
            self._book(conn, actor, book_id)
            row = self._resolve_row(conn, book_id, vehicle_type, period)
            if not row:
                raise AccountingError("本期间没有该车型的有效费率卡", 404)
            return self._view(row)

    def _book(self, conn, actor, book_id, write=False):
        return TaxCostService(self.db)._book(conn, actor, book_id, write=write)

    @staticmethod
    def _resolve_row(conn, book_id, vehicle_type, period):
        return conn.execute(
            "SELECT * FROM repair_rate_cards WHERE book_id=? AND vehicle_type=? "
            "AND status IN ('active','superseded') AND valid_from<=? AND valid_to>=? "
            "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, valid_from DESC LIMIT 1",
            (book_id, vehicle_type, period, period),
        ).fetchone()

    @staticmethod
    def _view(row):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "vehicle_type": row["vehicle_type"],
            "rate_cents": int(row["rate_cents"]),
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "coverage_scope": row["coverage_scope"],
            "source_ref": row["source_ref"],
            "created_at": row["created_at"],
            "version": int(row["version"]),
            "status": row["status"],
            "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "activated_at": row["activated_at"],
            "superseded_at": row["superseded_at"],
            "reason": row["reason"],
            "unit": row["unit"],
            "currency": row["currency"],
            "tax_included": bool(row["tax_included"]),
            "logical_id": row["logical_id"],
            "fingerprint": row["fingerprint"],
        }
