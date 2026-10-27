"""增值税底稿：确定性计算、账表差异核对与独立复核。

本模块只读发票导入行与已审核凭证，写底稿与审计事件；不写凭证、不改应交税费
科目、不影响财务报表，也不申报、不缴款、不外发。税率只能来自企业生效的税务
档案，缺少档案时明确拒绝，不猜。
"""

import calendar
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.invariants import cents_to_money, signed_cents_to_money, validate_period
from ..accounting.service import fingerprint, now

DRAFT = "draft"
REVIEWED = "reviewed"
TAXPAYER_TYPES = ("small_scale", "general")
FILING_CYCLES = ("monthly", "quarterly")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ratio(cents, rate_bp):
    """Half-up integer rounding of cents x basis points, no floats involved."""
    return (int(cents) * int(rate_bp) + 5000) // 10000


def _bounds(period):
    year, month = int(period[:4]), int(period[5:])
    last = calendar.monthrange(year, month)[1]
    return f"{period}-01", f"{period}-{last:02d}"


def _invoice_rows(conn, book_id, period):
    """Normal invoice rows whose issue date falls inside the period."""
    first, last = _bounds(period)
    rows = []
    for batch in conn.execute(
        "SELECT id,rows_json FROM invoice_import_batches WHERE book_id=? ORDER BY created_at,id",
        (book_id,),
    ):
        for row in json.loads(batch["rows_json"]):
            issued = str(row.get("date") or "")
            if not (first <= issued <= last):
                continue
            if row.get("void") or row.get("red") or row.get("duplicate"):
                continue
            if row.get("direction") not in ("input", "output"):
                continue
            amount, tax, total = (
                row.get("amount_cents"),
                row.get("tax_cents"),
                row.get("total_cents"),
            )
            if not all(type(value) is int and value >= 0 for value in (amount, tax, total)):
                continue
            if total <= 0 or amount + tax != total:
                continue
            rows.append(
                {
                    "batch_id": batch["id"],
                    "direction": row["direction"],
                    "issued_on": issued,
                    "counterparty": str(row.get("counterparty") or ""),
                    "amount_cents": amount,
                    "tax_cents": tax,
                    "total_cents": total,
                }
            )
    rows.sort(key=lambda item: (item["issued_on"], item["direction"], item["batch_id"]))
    return rows


def _ledger_vat(conn, book_id, period):
    """应交税费类的账面净变动（贷方为正），与月结简报同口径。"""
    first, last = _bounds(period)
    total = conn.execute(
        "SELECT COALESCE(SUM(l.credit_cents-l.debit_cents),0) AS total "
        "FROM draft_lines l JOIN drafts d ON d.id=l.draft_id AND d.book_id=l.book_id "
        "JOIN sources s ON s.id=d.source_id AND s.book_id=d.book_id "
        "JOIN accounts a ON a.book_id=l.book_id AND a.code=l.account_code "
        "WHERE l.book_id=? AND s.date BETWEEN ? AND ? AND d.status IN ('approved','exported') "
        "AND a.code LIKE ?",
        (book_id, first, last, "2221%"),
    ).fetchone()["total"]
    return int(total)


class TaxWorkpaperService:
    def __init__(self, db):
        self.db = db
        self.access = AccessControl()

    def _scope(self, actor, book_id, capability):
        """Resolve the book and its company before opening a company-scoped write."""
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, capability)
        return book

    # ---- profiles -------------------------------------------------------
    def set_profile(self, actor, book_id, payload):
        if payload.get("taxpayer_type") not in TAXPAYER_TYPES:
            raise AccountingError("纳税人类型只能是 small_scale 或 general", 422)
        if payload.get("filing_cycle", "monthly") not in FILING_CYCLES:
            raise AccountingError("申报周期只能是 monthly 或 quarterly", 422)
        rate = payload.get("vat_rate_bp")
        surtax = payload.get("surtax_rate_bp", 0)
        if type(rate) is not int or not 0 <= rate <= 10000:
            raise AccountingError("增值税率必须以万分之一为单位的整数给出", 422)
        if type(surtax) is not int or not 0 <= surtax <= 10000:
            raise AccountingError("附加税率必须以万分之一为单位的整数给出", 422)
        effective_from = validate_period(payload.get("effective_from"))
        reason = str(payload.get("reason") or "").strip()
        if not 3 <= len(reason) <= 1000:
            raise AccountingError("必须说明税率来源与适用依据（3-1000 字）", 422)
        book = self._scope(actor, book_id, "manage")
        with self.db.transaction(
            write=True, actor=actor, company_id=book["company_id"], write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "manage")
            exists = conn.execute(
                "SELECT 1 FROM tax_profiles WHERE company_id=? AND effective_from=?",
                (book["company_id"], effective_from),
            ).fetchone()
            if exists:
                raise AccountingError("该企业在这一个月已有税务档案，请新增更晚的生效月份", 409)
            profile_id = str(uuid4())
            conn.execute(
                "INSERT INTO tax_profiles(id,firm_id,company_id,effective_from,taxpayer_type,"
                "vat_rate_bp,surtax_rate_bp,filing_cycle,reason,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    profile_id,
                    actor.firm_id,
                    book["company_id"],
                    effective_from,
                    payload["taxpayer_type"],
                    rate,
                    surtax,
                    payload.get("filing_cycle", "monthly"),
                    reason,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book,
                "tax.profile.create",
                profile_id,
                {
                    "effective_from": effective_from,
                    "taxpayer_type": payload["taxpayer_type"],
                    "vat_rate_bp": rate,
                    "surtax_rate_bp": surtax,
                },
            )
        return self.profile(actor, book_id, effective_from)

    def profile(self, actor, book_id, period=None):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "read")
            return self._profile(conn, book, period or book["period"])

    def _profile(self, conn, book, period):
        row = conn.execute(
            "SELECT p.* FROM tax_profiles p WHERE p.company_id=? AND p.effective_from<=? "
            "ORDER BY p.effective_from DESC LIMIT 1",
            (book["company_id"], period),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "effective_from": row["effective_from"],
            "taxpayer_type": row["taxpayer_type"],
            "vat_rate_bp": int(row["vat_rate_bp"]),
            "surtax_rate_bp": int(row["surtax_rate_bp"]),
            "filing_cycle": row["filing_cycle"],
            "reason": row["reason"],
        }

    # ---- workpapers -----------------------------------------------------
    def generate(self, actor, book_id, period=None, reason=""):
        period = validate_period(period)
        scope = self._scope(actor, book_id, "edit")
        with self.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "edit")
            self._guard_period(conn, book, period)
            profile = self._profile(conn, book, period)
            if not profile:
                raise AccountingError(
                    f"缺少{period}生效的纳税人档案，无法确定税率；请先登记税务档案", 409
                )
            rows = _invoice_rows(conn, book_id, period)
            if not rows:
                raise AccountingError(
                    f"{period}没有可用的发票导入行，无法生成增值税底稿；请先导入销项与进项发票", 409
                )
            output = [row for row in rows if row["direction"] == "output"]
            incoming = [row for row in rows if row["direction"] == "input"]
            output_net = sum(row["amount_cents"] for row in output)
            output_tax = sum(row["tax_cents"] for row in output)
            input_net = sum(row["amount_cents"] for row in incoming)
            input_tax = sum(row["tax_cents"] for row in incoming)
            if profile["taxpayer_type"] == "small_scale":
                tax = _ratio(output_net, profile["vat_rate_bp"])
                credit_carry = 0
            else:
                gross = output_tax - input_tax
                tax = max(gross, 0)
                credit_carry = max(-gross, 0)
            surtax = _ratio(tax, profile["surtax_rate_bp"])
            ledger = _ledger_vat(conn, book_id, period)
            variance = tax - ledger
            digest = fingerprint(
                {
                    "period": period,
                    "profile": profile,
                    "rows": rows,
                    "totals": [output_net, output_tax, input_net, input_tax, tax, surtax],
                }
            )
            version = (
                int(
                    conn.execute(
                        "SELECT COALESCE(MAX(version),0) AS v FROM tax_workpapers "
                        "WHERE book_id=? AND period=?",
                        (book_id, period),
                    ).fetchone()["v"]
                )
                + 1
            )
            workpaper_id = str(uuid4())
            conn.execute(
                "INSERT INTO tax_workpapers(id,firm_id,company_id,book_id,period,version,status,"
                "profile_id,output_net_cents,output_tax_cents,input_net_cents,input_tax_cents,"
                "tax_cents,surtax_cents,credit_carry_cents,ledger_cents,variance_cents,"
                "variance_reason,lines_json,source_digest,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    workpaper_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    period,
                    version,
                    DRAFT,
                    profile["id"],
                    output_net,
                    output_tax,
                    input_net,
                    input_tax,
                    tax,
                    surtax,
                    credit_carry,
                    ledger,
                    variance,
                    "",
                    encode(rows),
                    digest,
                    actor.user_id,
                    now(),
                ),
            )
            self._audit(
                conn,
                actor,
                book,
                "tax.workpaper.generate",
                workpaper_id,
                {
                    "period": period,
                    "version": version,
                    "tax_cents": tax,
                    "variance_cents": variance,
                    "source_digest": digest,
                    "note": str(reason or "")[:200],
                },
            )
        return self.get(actor, book_id, workpaper_id)

    def review(self, actor, book_id, workpaper_id, reason, expected_version):
        reason = str(reason or "").strip()
        if not 3 <= len(reason) <= 1000:
            raise AccountingError("复核意见需 3-1000 字", 422)
        scope = self._scope(actor, book_id, "review")
        with self.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            row = conn.execute(
                "SELECT * FROM tax_workpapers WHERE id=? AND book_id=?",
                (workpaper_id, book_id),
            ).fetchone()
            if not row:
                raise AccountingError("底稿不存在或无权访问", 404)
            if int(row["version"]) != int(expected_version):
                raise AccountingError("底稿已被更新，请刷新后重试", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("制单人不能复核自己生成的税务底稿", 403)
            if int(row["variance_cents"]) != 0 and not reason:
                raise AccountingError("账表存在差异，复核意见必须说明差异处理", 422)
            changed = conn.execute(
                "UPDATE tax_workpapers SET status='reviewed',reviewed_by=?,reviewed_at=?,"
                "review_reason=?,variance_reason=? WHERE id=? AND book_id=? "
                "AND version=? AND status='draft'",
                (actor.user_id, now(), reason, reason, workpaper_id, book_id, expected_version),
            ).rowcount
            if changed != 1:
                raise AccountingError("底稿状态已变化，请刷新后重试", 409)
            self._audit(
                conn,
                actor,
                book,
                "tax.workpaper.review",
                workpaper_id,
                {
                    "version": int(expected_version),
                    "variance_cents": int(row["variance_cents"]),
                },
            )
        return self.get(actor, book_id, workpaper_id)

    def get(self, actor, book_id, workpaper_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            row = conn.execute(
                "SELECT * FROM tax_workpapers WHERE id=? AND book_id=?",
                (workpaper_id, book_id),
            ).fetchone()
        if not row:
            raise AccountingError("底稿不存在或无权访问", 404)
        return self._present(row)

    def list(self, actor, book_id, period=None):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            sql = "SELECT * FROM tax_workpapers WHERE book_id=?"
            params = [book_id]
            if period:
                sql += " AND period=?"
                params.append(validate_period(period))
            rows = conn.execute(
                sql + " ORDER BY period DESC,version DESC LIMIT 100", params
            ).fetchall()
        return {"items": [self._present(row) for row in rows]}

    def csv(self, actor, book_id, workpaper_id):
        """Deterministic CSV of a reviewed workpaper; drafts are refused."""
        data = self.get(actor, book_id, workpaper_id)
        if data["status"] != REVIEWED:
            raise AccountingError("只能导出已复核的底稿", 409)
        lines = ["期间,方向,开票日期,对方,不含税金额,税额,价税合计"]
        for row in data["lines"]:
            lines.append(
                ",".join(
                    [
                        data["period"],
                        "销项" if row["direction"] == "output" else "进项",
                        row["issued_on"],
                        _csv(str(row.get("counterparty") or "")),
                        cents_to_money(row["amount_cents"]),
                        cents_to_money(row["tax_cents"]),
                        cents_to_money(row["total_cents"]),
                    ]
                )
            )
        lines.append(f"{data['period']},应纳税额,,,{data['tax']}")
        return "\ufeff" + "\n".join(lines) + "\n"

    # ---- internals ------------------------------------------------------
    def _guard_period(self, conn, book, period):
        if book["period_status"] != "open":
            raise AccountingError("该账套当前期间已关闭，不能在关闭期间生成税务底稿", 409)
        locked = conn.execute(
            "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
            (book["id"], period),
        ).fetchone()
        if locked:
            raise AccountingError("该期间已被内部关账锁定，不能生成税务底稿", 409)

    def _present(self, row):
        return {
            "id": row["id"],
            "book_id": row["book_id"],
            "period": row["period"],
            "version": int(row["version"]),
            "status": row["status"],
            "profile_id": row["profile_id"],
            "output_net": cents_to_money(row["output_net_cents"]),
            "output_tax": cents_to_money(row["output_tax_cents"]),
            "input_net": cents_to_money(row["input_net_cents"]),
            "input_tax": cents_to_money(row["input_tax_cents"]),
            "tax": cents_to_money(row["tax_cents"]),
            "surtax": cents_to_money(row["surtax_cents"]),
            "credit_carry": cents_to_money(row["credit_carry_cents"]),
            "ledger": signed_cents_to_money(row["ledger_cents"]),
            "variance": signed_cents_to_money(row["variance_cents"]),
            "variance_reason": row["variance_reason"],
            "source_digest": row["source_digest"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": row["reviewed_at"],
            "review_reason": row["review_reason"],
            "filing": "not_filed",
            "lines": json.loads(row["lines_json"]),
        }

    def _audit(self, conn, actor, book, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book["id"],
                actor.user_id,
                action,
                object_id,
                encode(details),
                now(),
            ),
        )


def _csv(value):
    text = value.replace('"', '""')
    return f'"{text}"'
