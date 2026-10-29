"""增值税底稿：确定性计算、账表差异核对与独立复核。

本模块只读发票导入行与正式凭证，写底稿与审计事件；不写凭证、不改应交税费
科目、不影响财务报表，也不申报、不缴款、不外发。税率只能来自企业生效的税务
档案，缺少档案时明确拒绝，不猜。
"""

import calendar
import hashlib
import json
import re
from datetime import date
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.invariants import (
    MAX_CENTS,
    cents_to_money,
    signed_cents_to_money,
    validate_period,
)
from ..accounting.service import fingerprint, now

DRAFT = "draft"
REVIEWED = "reviewed"
TAXPAYER_TYPES = ("small_scale", "general")
FILING_CYCLES = ("monthly", "quarterly")
SOURCE_SCHEMA = "n02-tax-v2"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ratio(cents, rate_bp):
    """Half-up integer rounding of cents x basis points, no floats involved."""
    return (int(cents) * int(rate_bp) + 5000) // 10000


def _bounds(period):
    year, month = int(period[:4]), int(period[5:])
    last = calendar.monthrange(year, month)[1]
    return f"{period}-01", f"{period}-{last:02d}"


def _period(value):
    try:
        return validate_period(value)
    except ValueError as failure:
        raise AccountingError("期间必须为2000至2099年内的有效YYYY-MM月份", 422) from failure


def _invoice_rows(conn, book_id, period):
    """Return (usable rows, rejected reason counts) for the period.

    Rows that the current support boundary cannot handle are counted per reason
    instead of being silently dropped, so a workpaper can never claim to be
    complete while ignoring red-letter, void or malformed invoices.
    """
    first, last = _bounds(period)
    rows = []
    rejected = {}

    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1

    for batch in conn.execute(
        "SELECT id,sha256,rows_json FROM invoice_import_batches WHERE book_id=? ORDER BY id",
        (book_id,),
    ):
        try:
            imported_rows = json.loads(batch["rows_json"])
        except (TypeError, ValueError) as failure:
            raise AccountingError("发票原始行数据损坏，请核对原件并重新导入", 409) from failure
        if not isinstance(imported_rows, list) or any(
            not isinstance(r, dict) for r in imported_rows
        ):
            raise AccountingError("发票原始行结构非法，请核对原件并重新导入", 409)
        for ordinal, row in enumerate(imported_rows, 1):
            issued = str(row.get("date") or "")
            try:
                if date.fromisoformat(issued).isoformat() != issued:
                    raise ValueError
            except ValueError:
                reject("开票日期缺失或非法")
                continue
            if not (first <= issued <= last):
                reject("跨期发票")
                continue
            if row.get("red"):
                reject("红字发票")
                continue
            if row.get("void"):
                reject("作废发票")
                continue
            if row.get("duplicate"):
                reject("重复发票")
                continue
            if row.get("direction") not in ("input", "output"):
                reject("方向未知")
                continue
            amount, tax, total = (
                row.get("amount_cents"),
                row.get("tax_cents"),
                row.get("total_cents"),
            )
            if not all(
                type(value) is int and 0 <= value <= MAX_CENTS for value in (amount, tax, total)
            ):
                reject("金额缺失或非法")
                continue
            if total <= 0 or amount + tax != total:
                reject("价税分离不成立")
                continue
            rows.append(
                {
                    "batch_id": batch["id"],
                    "original_sha256": batch["sha256"],
                    "row_index": ordinal,
                    "row_fingerprint": fingerprint(row),
                    "original": row,
                    "direction": row["direction"],
                    "issued_on": issued,
                    "counterparty": str(
                        row.get("buyer" if row["direction"] == "output" else "seller")
                        or row.get("counterparty")
                        or ""
                    ),
                    "amount_cents": amount,
                    "tax_cents": tax,
                    "total_cents": total,
                }
            )
    rows.sort(key=lambda item: (item["issued_on"], item["direction"], item["batch_id"]))
    return rows, rejected


def _ledger_vat(conn, book, period, vat_accounts):
    """Freeze all posted facts; tax roles are explicit, never guessed from codes."""
    binding = conn.execute(
        "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
    ).fetchone()
    if not binding:
        raise AccountingError("账套缺少长期账套绑定，请先补齐核算配置", 409)
    if not isinstance(vat_accounts, list) or len(vat_accounts) > 1000:
        raise AccountingError("税务科目用途必须为至多1000项的明确配置", 422)
    mappings, roles = [], {}
    for item in vat_accounts:
        if not isinstance(item, dict) or set(item) != {"account_id", "account_version", "role"}:
            raise AccountingError("税务科目配置须包含稳定科目ID、版本和用途", 422)
        identifier, version, role = item["account_id"], item["account_version"], item["role"]
        if (
            not isinstance(identifier, str)
            or not identifier
            or type(version) is not int
            or version < 1
            or role not in ("vat_payable", "excluded")
        ):
            raise AccountingError("税务科目配置的身份、版本或用途非法", 422)
        key = (identifier, version)
        if key in roles:
            raise AccountingError("税务科目配置不能重复", 422)
        account = conn.execute(
            "SELECT v.*,a.status AS account_status,a.current_version FROM ledger_account_versions v "
            "JOIN ledger_accounts a ON a.id=v.account_id WHERE v.account_id=? AND v.version=? "
            "AND v.ledger_id=? AND v.firm_id=? AND v.company_id=?",
            (identifier, version, binding["ledger_id"], book["firm_id"], book["company_id"]),
        ).fetchone()
        if not account:
            raise AccountingError("税务科目版本不存在或不属于本账套", 409)
        if (
            account["account_status"] != "active"
            or account["status"] != "active"
            or account["effective_from"] > period
            or (account["effective_to"] and account["effective_to"] < period)
        ):
            raise AccountingError("税务科目版本未在本期间生效", 409)
        roles[key] = role
        mappings.append({**item, "definition": dict(account)})
    vouchers = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM vouchers WHERE book_id=? AND period=? AND status='posted' ORDER BY id",
            (book["id"], period),
        )
    ]
    lines = [
        dict(r)
        for r in conn.execute(
            "SELECT l.* FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
            "WHERE v.book_id=? AND v.period=? AND v.status='posted' ORDER BY l.voucher_id,l.line_no,l.id",
            (book["id"], period),
        )
    ]
    missing, total = {}, 0
    for line in lines:
        key = (line["account_id"], int(line["account_version"]))
        movement = int(line["credit_cents"]) - int(line["debit_cents"])
        if key not in roles:
            missing.setdefault(key, {"code": line["code_snapshot"], "amount": 0})["amount"] += (
                movement
            )
        elif roles[key] == "vat_payable":
            total += movement
    if missing:
        detail = "、".join(
            f"{v['code']} {signed_cents_to_money(v['amount']) if abs(v['amount']) <= MAX_CENTS else '汇总超出金额上限'}"
            for v in missing.values()
        )
        raise AccountingError(
            f"正式凭证科目尚未明确税务用途（{detail}）；请逐科目声明增值税或排除，并交独立会计复核",
            409,
        )
    return (
        total,
        {"vouchers": vouchers, "lines": lines},
        sorted(mappings, key=lambda item: (item["account_id"], item["account_version"])),
        binding["ledger_id"],
    )


def _snapshot(conn, book, period, profile, vat_accounts=None):
    """One deterministic source snapshot used by both generate and review."""
    if profile["filing_cycle"] != "monthly":
        raise AccountingError("当前仅支持按月底稿，季度申报须完整季度核验；请交人工处理", 409)
    if profile["taxpayer_type"] != "small_scale":
        raise AccountingError(
            "一般纳税人的进项抵扣资格、采用记录和期初留抵尚未接通，请交人工处理", 409
        )
    rows, rejected = _invoice_rows(conn, book["id"], period)
    if rejected:
        detail = "、".join(f"{reason} {count} 张" for reason, count in sorted(rejected.items()))
        raise AccountingError(
            f"{period} 的发票存在暂不支持的情形（{detail}），请先人工核验并处理后再生成底稿", 409
        )
    ledger, formal, mappings, ledger_id = _ledger_vat(
        conn, book, period, [] if vat_accounts is None else vat_accounts
    )
    output = [row for row in rows if row["direction"] == "output"]
    incoming = [row for row in rows if row["direction"] == "input"]
    output_net = sum(row["amount_cents"] for row in output)
    output_tax = sum(row["tax_cents"] for row in output)
    input_net = sum(row["amount_cents"] for row in incoming)
    input_tax = sum(row["tax_cents"] for row in incoming)
    tax = _ratio(output_net, profile["vat_rate_bp"])
    credit_carry = 0
    surtax = _ratio(tax, profile["surtax_rate_bp"])
    totals = [output_net, output_tax, input_net, input_tax, tax, surtax, credit_carry, ledger]
    if any(abs(value) > MAX_CENTS for value in [*totals, tax - ledger]):
        raise AccountingError("底稿汇总金额超出允许范围，请分清业务范围后人工核验", 409)
    source = {
        "schema": SOURCE_SCHEMA,
        "scope": {
            "firm_id": book["firm_id"],
            "company_id": book["company_id"],
            "ledger_id": ledger_id,
            "book_id": book["id"],
            "period": period,
        },
        "period": period,
        "profile": profile,
        "vat_accounts": mappings,
        "invoices": rows,
        "ledger": formal,
        "totals": totals,
    }
    digest = fingerprint(source)
    return {
        "rows": rows,
        "output_net": output_net,
        "output_tax": output_tax,
        "input_net": input_net,
        "input_tax": input_tax,
        "tax": tax,
        "surtax": surtax,
        "credit_carry": credit_carry,
        "ledger": ledger,
        "digest": digest,
        "source": source,
    }


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
        effective_from = _period(payload.get("effective_from"))
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
            return self._profile(conn, book, _period(period or book["period"]))

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
    def generate(self, actor, book_id, period=None, reason="", vat_accounts=None):
        scope = self._scope(actor, book_id, "edit")
        period = _period(period or scope["period"])
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
            snapshot = _snapshot(conn, book, period, profile, vat_accounts)
            rows = snapshot["rows"]
            if not rows:
                raise AccountingError(
                    f"{period}没有可用的发票导入行，无法生成增值税底稿；请先导入销项与进项发票", 409
                )
            ledger = snapshot["ledger"]
            tax = snapshot["tax"]
            surtax = snapshot["surtax"]
            digest = snapshot["digest"]
            variance = tax - ledger
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
                "variance_reason,lines_json,source_digest,created_by,created_at,source_snapshot_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    workpaper_id,
                    actor.firm_id,
                    book["company_id"],
                    book_id,
                    period,
                    version,
                    DRAFT,
                    profile["id"],
                    snapshot["output_net"],
                    snapshot["output_tax"],
                    snapshot["input_net"],
                    snapshot["input_tax"],
                    tax,
                    surtax,
                    snapshot["credit_carry"],
                    ledger,
                    variance,
                    "",
                    encode(rows),
                    digest,
                    actor.user_id,
                    now(),
                    encode(snapshot["source"]),
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
        if type(expected_version) is not int or expected_version < 1:
            raise AccountingError("必须提供正整数底稿版本", 422)
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
            self._guard_period(conn, book, row["period"])
            if int(row["version"]) != int(expected_version):
                raise AccountingError("底稿已被更新，请刷新后重试", 409)
            if row["created_by"] == actor.user_id:
                raise AccountingError("制单人不能复核自己生成的税务底稿", 403)
            self._assert_current(conn, book, row)
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
                params.append(_period(period))
            rows = conn.execute(
                sql + " ORDER BY period DESC,version DESC LIMIT 100", params
            ).fetchall()
        return {"items": [self._present(row) for row in rows]}

    def csv(self, actor, book_id, workpaper_id, expected_version=None, source_digest=None):
        """Export the exact reviewed, still-current version in an authorized transaction."""
        if type(expected_version) is not int or expected_version < 1:
            raise AccountingError("导出必须明确指定已复核底稿版本", 422)
        if not isinstance(source_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", source_digest):
            raise AccountingError("导出必须明确指定已复核来源摘要", 422)
        scope = self._scope(actor, book_id, "export")
        with self.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            book = self.access.book(conn, actor, book_id, "export")
            row = conn.execute(
                "SELECT * FROM tax_workpapers WHERE id=? AND book_id=?", (workpaper_id, book_id)
            ).fetchone()
            if not row:
                raise AccountingError("底稿不存在或无权访问", 404)
            if row["version"] != expected_version or row["source_digest"] != source_digest:
                raise AccountingError("导出版本或来源摘要不匹配，请刷新核对后重试", 409)
            if row["status"] != REVIEWED:
                raise AccountingError("只能导出已复核的底稿", 409)
            self._assert_current(conn, book, row)
            data = self._present(row)
            body = self._render_csv(data)
            self._audit(
                conn,
                actor,
                book,
                "tax.workpaper.export",
                workpaper_id,
                {
                    "version": expected_version,
                    "source_digest": source_digest,
                    "csv_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "reviewed_by": row["reviewed_by"],
                    "reviewed_at": row["reviewed_at"],
                },
            )
        return body

    @staticmethod
    def _render_csv(data):
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
        lines.append(f"{data['period']},应纳税额,,,,{data['tax']},")
        lines.extend(["", "底稿元数据（以下不是发票明细）"])
        lines.append(f"底稿版本,{data['version']},底稿ID,{_csv(data['id'])}")
        lines.append(f"来源摘要,{data['source_digest']},申报状态,not_filed")
        lines.append(f"账套ID,{_csv(data['book_id'])},独立复核人,{_csv(data['reviewed_by'])}")
        return "\ufeff" + "\n".join(lines) + "\n"

    # ---- internals ------------------------------------------------------
    def _assert_current(self, conn, book, row):
        if row["period"] != book["period"]:
            raise AccountingError("底稿期间与当前账套不一致", 409)
        latest = conn.execute(
            "SELECT MAX(version) AS version FROM tax_workpapers WHERE book_id=? AND period=?",
            (book["id"], row["period"]),
        ).fetchone()["version"]
        if latest != row["version"]:
            raise AccountingError("已有更新底稿版本；旧稿保留只读，请核对最新版本", 409)
        try:
            source = json.loads(row["source_snapshot_json"])
        except (TypeError, ValueError):
            source = None
        if (
            not isinstance(source, dict)
            or source.get("schema") != SOURCE_SCHEMA
            or fingerprint(source) != row["source_digest"]
        ):
            raise AccountingError("底稿缺少有效的版本化来源快照，请重新生成并独立复核", 409)
        profile = self._profile(conn, book, row["period"])
        if not profile or profile["id"] != row["profile_id"]:
            raise AccountingError("来源税务档案已变化，请重新生成并复核", 409)
        try:
            mappings = [
                {k: item[k] for k in ("account_id", "account_version", "role")}
                for item in source["vat_accounts"]
            ]
            current = _snapshot(conn, book, row["period"], profile, mappings)
        except (KeyError, TypeError) as failure:
            raise AccountingError("来源快照配置不完整，请重新生成并复核", 409) from failure
        except AccountingError as failure:
            raise AccountingError(f"底稿来源已变化：{failure.detail}", 409) from failure
        if current["digest"] != row["source_digest"]:
            raise AccountingError("底稿来源已变化（发票、科目版本或正式账），请重新生成并复核", 409)
        amounts = {
            f"{key}_cents": current[key]
            for key in (
                "output_net",
                "output_tax",
                "input_net",
                "input_tax",
                "tax",
                "surtax",
                "credit_carry",
                "ledger",
            )
        }
        amounts["variance_cents"] = current["tax"] - current["ledger"]
        try:
            lines_match = encode(json.loads(row["lines_json"])) == encode(current["rows"])
        except (TypeError, ValueError):
            lines_match = False
        if not lines_match or any(
            type(row[k]) is not int or row[k] != v for k, v in amounts.items()
        ):
            raise AccountingError("底稿金额或明细与已绑定的来源不一致，请重新生成并复核", 409)

    def _guard_period(self, conn, book, period):
        if period != book["period"]:
            raise AccountingError("底稿期间必须与当前账套月份一致，跨期处理请交人工核验", 409)
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
            "firm_id": row["firm_id"],
            "company_id": row["company_id"],
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
            "source_snapshot": json.loads(row["source_snapshot_json"] or "null"),
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
    if value.lstrip().startswith(("=", "+", "-", "@")):
        value = "'" + value
    text = value.replace('"', '""')
    return f'"{text}"'
