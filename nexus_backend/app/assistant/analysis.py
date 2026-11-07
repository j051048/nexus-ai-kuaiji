"""Finite, read-only analyses behind the accounting assistant.

The model cannot supply SQL, company ids, account versions or URLs. Company and
firm are resolved from the authenticated actor and the book; every reader runs
inside a transaction that re-checks book access. Results carry explicit coverage
and a version fingerprint, and never publish a claim that is not in the facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace
import hashlib
import json
import re

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError


KINDS = ("expense_bridge", "invoice_voucher_diff")
MAX_ROWS = 100
ACCOUNT_RE = re.compile(r"^\d{4}(?:\.\d{2})?$")
PERIOD_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")


def _money(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _href(book_id, period, *, voucher=None, source=None):
    base = f"/workspace/accounting?book={book_id}&period={period}"
    if voucher:
        base += f"&voucher={voucher}"
    if source:
        base += f"&source={source}"
    return base


def _database(service):
    return getattr(service, "db", service)


def _recheck_book(service, conn, actor, book_id):
    (getattr(service, "access", None) or AccessControl()).book(conn, actor, book_id, "read")


def _book(service, actor, book_id, period):
    if not isinstance(book_id, str) or not book_id or not PERIOD_RE.fullmatch(str(period)):
        raise AccountingError("分析参数无效", 422)
    with _database(service).transaction(actor=actor) as conn:
        book = (getattr(service, "access", None) or AccessControl()).book(
            conn, actor, book_id, "read"
        )
        if book["period"] != period:
            raise AccountingError("分析月份与账套不一致", 422)
        return dict(book)


def _sibling_book(service, actor, book, *, compare_period=None):
    # Physical books in the same company need not be the same accounting entity.
    with _database(service).transaction(actor=actor) as conn:
        if compare_period and (
            not PERIOD_RE.fullmatch(str(compare_period)) or compare_period >= book["period"]
        ):
            raise AccountingError("对比月份无效", 422)
        if not compare_period:
            year, month = map(int, book["period"].split("-"))
            compare_period = f"{year - (month == 1):04d}-{12 if month == 1 else month - 1:02d}"
        rows = conn.execute(
            "SELECT b.id,b.period,b.company_id FROM books b JOIN ledger_periods p ON p.book_id=b.id "
            "JOIN ledger_periods current ON current.ledger_id=p.ledger_id "
            "WHERE current.book_id=? AND b.company_id=? AND b.period<? "
            + ("AND b.period=? " if compare_period else "")
            + "ORDER BY b.period DESC",
            (book["id"], book["company_id"], book["period"])
            + ((compare_period,) if compare_period else ()),
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1 and rows[0]["period"] == rows[1]["period"]:
            raise AccountingError("上期核算主体不唯一，请先核对映射", 409)
        row = rows[0]
        _recheck_book(service, conn, actor, row["id"])
        return dict(row)


def _expense_rows(conn, book_id, account_code, limit, offset):
    total = conn.execute(
        "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) AS amount, COUNT(*) AS n "
        "FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id "
        "WHERE v.book_id=? AND l.code_snapshot=?",
        (book_id, account_code),
    ).fetchone()
    rows = conn.execute(
        "SELECT v.id AS voucher_id,v.voucher_no,v.voucher_date,v.description,"
        "l.debit_cents,l.credit_cents,l.account_version FROM voucher_lines l "
        "JOIN vouchers v ON v.id=l.voucher_id "
        "WHERE v.book_id=? AND l.code_snapshot=? "
        "ORDER BY v.voucher_date,v.voucher_no,l.line_no LIMIT ? OFFSET ?",
        (book_id, account_code, limit + 1, offset),
    ).fetchall()
    return int(total["amount"]), int(total["n"]), [dict(row) for row in rows]


def _expense_bridge(
    service, actor, book_id, period, *, account_code, compare_period=None, limit=50, offset=0
):
    if not isinstance(account_code, str) or not ACCOUNT_RE.fullmatch(account_code):
        raise AccountingError("费用科目格式无效", 422)
    if not isinstance(limit, int) or limit < 1 or limit > MAX_ROWS or offset < 0:
        raise AccountingError("分页参数无效", 422)
    book = _book(service, actor, book_id, period)
    previous = _sibling_book(service, actor, book, compare_period=compare_period)
    with _database(service).transaction(actor=actor) as conn:
        _recheck_book(service, conn, actor, book_id)
        current_total, current_rows, current_page = _expense_rows(
            conn, book_id, account_code, limit, offset
        )
        _recheck_book(service, conn, actor, book_id)
        name = conn.execute(
            "SELECT name FROM accounts WHERE book_id=? AND code=?", (book_id, account_code)
        ).fetchone()
    current_details = [
        {
            "voucher_id": row["voucher_id"],
            "voucher_no": row["voucher_no"],
            "date": row["voucher_date"],
            "description": row["description"],
            "amount_cents": int(row["debit_cents"]) - int(row["credit_cents"]),
            "evidence_href": _href(book_id, period, voucher=row["voucher_id"]),
        }
        for row in current_page[:limit]
    ]
    details = current_details
    missing = []
    previous_total = None
    previous_details = []
    previous_page = []
    previous_n = 0
    if previous:
        with _database(service).transaction(actor=actor) as conn:
            mappings = conn.execute(
                "SELECT book_id,account_id,account_version FROM ledger_account_mappings WHERE book_id IN (?,?) AND book_account_code=? ORDER BY book_id",
                (book_id, previous["id"], account_code),
            ).fetchall()
            if len(mappings) != 2 or (
                mappings[0]["account_id"],
                mappings[0]["account_version"],
            ) != (mappings[1]["account_id"], mappings[1]["account_version"]):
                previous = None
                missing.append("account_identity")
    if previous is None:
        missing.append("previous_period")
        status = "partial"
        if current_rows == 0:
            summary = f"本期 {account_code} 无记录，且缺少可比上期账套，不能计算跨期差额。"
        else:
            summary = f"本期 {account_code} 有记录，但缺少可比上期账套，不能计算跨期差额。"
    else:
        with _database(service).transaction(actor=actor) as conn:
            _recheck_book(service, conn, actor, previous["id"])
            previous_total, previous_n, previous_page = _expense_rows(
                conn, previous["id"], account_code, limit, offset
            )
        previous_details = [
            {
                "voucher_id": row["voucher_id"],
                "voucher_no": row["voucher_no"],
                "date": row["voucher_date"],
                "description": row["description"],
                "amount_cents": int(row["debit_cents"]) - int(row["credit_cents"]),
                "evidence_href": _href(
                    previous["id"], previous["period"], voucher=row["voucher_id"]
                ),
            }
            for row in previous_page[:limit]
        ]
        status = "complete"
        delta = current_total - previous_total
        if current_rows == 0 and previous_n == 0:
            summary = f"{account_code} {name['name'] if name else ''}：本期与上期均无该科目记录。"
        elif current_rows == 0:
            summary = (
                f"{account_code} {name['name'] if name else ''}：本期无该科目记录，"
                f"上期 {previous_total} 分；差额 {delta} 分。"
            )
        else:
            summary = (
                f"{account_code} {name['name'] if name else ''}：本期 {current_total} 分，"
                f"上期 {previous_total} 分，差额 {delta} 分；明细见引用记录。"
            )
        if offset > 0 or len(current_page) > limit or len(previous_page) > limit:
            status = "partial"
            summary += " 明细分页未一次显示完，不能把当前页当作全部明细。"
    facts = {
        "account_code": account_code,
        "account_name": name["name"] if name else None,
        "current_period": period,
        "current_book_id": book_id,
        "current_total_cents": current_total,
        "current_total": _money(current_total),
        "previous_period": previous["period"] if previous else None,
        "previous_book_id": previous["id"] if previous else None,
        "previous_total_cents": previous_total,
        "previous_total": _money(previous_total) if previous else None,
        "difference_cents": (current_total - previous_total) if previous else None,
        "difference": _money(current_total - previous_total) if previous else None,
        "currency": "CNY",
        "currency_source": "posted_ledger_CNY_only",
        "current_rows": details,
        "previous_rows": previous_details,
    }
    detail_rows = len(current_details) + len(previous_details)
    has_more = len(current_page) > limit or len(previous_page) > limit
    coverage = {
        "scope": "current_and_previous" if previous else "current_only",
        "rows_read": detail_rows,
        "detail_rows": detail_rows,
        "total_rows": current_rows + previous_n,
        "has_more": has_more,
        "detail_complete": offset == 0 and not has_more,
        "total_complete": True,
        "offset": offset,
        "pages": 1,
        "account_versions": sorted(
            {row["account_version"] for row in current_page[:limit]}
            | ({row["account_version"] for row in previous_page[:limit]} if previous else set())
        ),
    }
    return _result("expense_bridge", book, status, summary, facts, coverage, missing, limit, offset)


def _invoice_voucher_diff(
    service, actor, book_id, period, *, target_voucher_id, limit=50, offset=0
):
    if not isinstance(target_voucher_id, str) or not target_voucher_id:
        raise AccountingError("请指定目标凭证", 422)
    if not isinstance(limit, int) or limit < 1 or limit > MAX_ROWS or offset < 0:
        raise AccountingError("分页参数无效", 422)
    book = _book(service, actor, book_id, period)
    with _database(service).transaction(actor=actor) as conn:
        _recheck_book(service, conn, actor, book_id)
        voucher = conn.execute(
            "SELECT id,voucher_no,voucher_date,description,content_fingerprint FROM vouchers "
            "WHERE id=? AND book_id=? AND period=?",
            (target_voucher_id, book_id, period),
        ).fetchone()
        if not voucher:
            raise AccountingError("目标凭证不在当前账套", 404)
        debit = conn.execute(
            "SELECT COALESCE(SUM(debit_cents),0) AS n FROM voucher_lines WHERE voucher_id=?",
            (target_voucher_id,),
        ).fetchone()["n"]
        credit = conn.execute(
            "SELECT COALESCE(SUM(credit_cents),0) AS n FROM voucher_lines WHERE voucher_id=?",
            (target_voucher_id,),
        ).fetchone()["n"]
        stats = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS total,COUNT(*) AS n FROM voucher_sources WHERE voucher_id=?",
            (target_voucher_id,),
        ).fetchone()
        sources = conn.execute(
            "SELECT source_kind,source_id,source_version,amount_cents,evidence_ref FROM voucher_sources "
            "WHERE voucher_id=? ORDER BY id LIMIT ? OFFSET ?",
            (target_voucher_id, limit + 1, offset),
        ).fetchall()
    source_rows = int(stats["n"])
    source_total = int(stats["total"])
    source_detail_total = sum(int(row["amount_cents"]) for row in sources[:limit])
    imbalanced = int(debit) != int(credit)
    missing = []
    if source_rows == 0:
        status = "partial"
        summary = "该凭证没有绑定来源金额，不能计算票据与凭证差额；请会计核对来源。"
        difference = None
        missing.append("voucher_source")
    else:
        difference = int(debit) - source_total
        status = "complete"
        summary = (
            f"凭证 {voucher['voucher_no']} 借方 {int(debit)} 分，来源金额 {source_total} 分，"
            f"差额 {difference} 分。差额不是舞弊或浪费结论。"
        )
        if offset > 0 or len(sources) > limit:
            status = "partial"
            summary += " 来源明细当前页未显示完，总额按全部来源计算。"
        if imbalanced:
            status = "partial"
            missing.append("voucher_imbalance")
            summary += " 凭证借贷不平，核对结果不完整。"
    facts = {
        "voucher_id": voucher["id"],
        "voucher_no": int(voucher["voucher_no"]),
        "voucher_date": voucher["voucher_date"],
        "description": voucher["description"],
        "voucher_total_cents": int(debit),
        "voucher_total": _money(int(debit)),
        "source_total_cents": source_total if source_rows else None,
        "source_total": _money(source_total) if source_rows else None,
        "source_detail_total_cents": source_detail_total,
        "source_detail_total": _money(source_detail_total),
        "voucher_credit_cents": int(credit),
        "voucher_imbalanced": imbalanced,
        "difference_cents": difference,
        "difference": _money(difference) if difference is not None else None,
        "currency": "CNY",
        "sources": [
            {
                "kind": row["source_kind"],
                "id": row["source_id"],
                "version": row["source_version"],
                "amount_cents": int(row["amount_cents"]),
                "evidence_ref": row["evidence_ref"],
                "evidence_href": _href(
                    book_id, period, voucher=voucher["id"], source=row["source_id"]
                ),
            }
            for row in sources[:limit]
        ],
    }
    coverage = {
        "scope": "voucher_and_linked_sources",
        "rows_read": len(sources[:limit]),
        "detail_rows": len(sources[:limit]),
        "total_rows": source_rows,
        "has_more": len(sources) > limit,
        "detail_complete": offset == 0 and len(sources) <= limit,
        "total_complete": True,
        "offset": offset,
        "pages": 1,
        "source_versions": [row["source_version"] for row in sources[:limit]],
    }
    return _result(
        "invoice_voucher_diff",
        book,
        status,
        summary,
        facts,
        coverage,
        missing,
        limit,
        offset,
        target=target_voucher_id,
    )


def _result(kind, book, status, summary, facts, coverage, missing, limit, offset, target=None):
    period = book["period"]
    facts = dict(facts)
    facts["fact_ids"] = [
        f"{kind}:{key}"
        for key in (
            "current_total_cents",
            "difference_cents",
            "voucher_total_cents",
            "source_total_cents",
        )
        if facts.get(key) is not None
    ]
    payload = {
        "kind": kind,
        "book_id": book["id"],
        "company_id": book["company_id"],
        "period": book["period"],
        "status": status,
        "summary": summary,
        "facts": facts,
        "coverage": coverage,
        "missing": missing,
        "handoff": (
            None
            if status == "complete"
            else {
                "reason_code": missing[0] if missing else "coverage_incomplete",
                "reason": "分析覆盖不完整，需要会计在业务入口核对",
                "owner_role": "accountant",
                "source_ref": f"{kind}:{book['id']}:{period}:{target or facts.get('account_code') or '-'}",
                "source_version": ",".join(
                    [
                        str(v)
                        for v in (
                            coverage.get("account_versions")
                            or coverage.get("source_versions")
                            or []
                        )
                    ]
                )
                or f"{book['id']}:{period}",
                "action_target": (
                    f"/workspace/accounting?book={book['id']}&period={period}"
                    f"&company={book['company_id']}"
                ),
                "deadline": None,
                "status": "open",
            }
        ),
        "limits": {"max_rows": MAX_ROWS, "limit": limit, "offset": offset},
        "model_calls": 0,
        "real_model_evaluated": False,
        "target": target,
    }
    payload["fingerprint"] = _fingerprint(
        {
            key: payload[key]
            for key in ("kind", "book_id", "period", "status", "facts", "coverage", "missing")
        }
    )
    return payload


class _SnapshotDatabase:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def transaction(self, **kwargs):
        yield self.conn


def _dependencies(conn, books):
    values = []
    for book_id in sorted(set(books)):
        mapping = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book_id,)
        ).fetchone()
        ledger = mapping["ledger_id"] if mapping else None
        revisions = conn.execute(
            "SELECT scope_kind,scope_id,revision FROM close_dependency_revisions WHERE (scope_kind='book' AND scope_id=?) OR (scope_kind='ledger' AND scope_id=?) ORDER BY scope_kind,scope_id",
            (book_id, ledger or ""),
        ).fetchall()
        values.append(
            {"book_id": book_id, "ledger_id": ledger, "revisions": [dict(row) for row in revisions]}
        )
    return values


def analyze(service, actor, book_id, period, kind, **params):
    if kind not in KINDS:
        raise AccountingError("不支持的分析类型", 422)
    expected_version = params.pop("expected_source_version", None)
    required = "account_code" if kind == "expense_bridge" else "target_voucher_id"
    allowed = (
        {"account_code", "compare_period"} if kind == "expense_bridge" else {"target_voucher_id"}
    ) | {"limit", "offset"}
    if (
        set(params) - allowed
        or not isinstance(params.get(required), str)
        or not params[required].strip()
    ):
        raise AccountingError("请补齐当前核对类型的必要参数", 422)
    limit, offset = params.get("limit", 50), params.get("offset", 0)
    if (
        type(limit) is not int
        or type(offset) is not int
        or not 1 <= limit <= MAX_ROWS
        or not 0 <= offset <= 1_000_000
    ):
        raise AccountingError("分页参数无效", 422)
    if expected_version is not None and (
        not isinstance(expected_version, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_version)
    ):
        raise AccountingError("来源版本格式无效", 422)
    db = _database(service)
    access = getattr(service, "access", None) or AccessControl()
    with db.transaction(actor=actor) as conn:
        bound = SimpleNamespace(db=_SnapshotDatabase(conn), access=access)
        reader = _expense_bridge if kind == "expense_bridge" else _invoice_voucher_diff
        result = reader(bound, actor, book_id, period, **params)
        books = [book_id]
        if result["facts"].get("previous_book_id"):
            books.append(result["facts"]["previous_book_id"])
        dependencies = _dependencies(conn, books)
    # New snapshot is the publication linearization point: revoke/source change
    # committed during reading must not publish the old evidence. No auth cache.
    with db.transaction(actor=actor) as conn:
        for identity in books:
            _recheck_book(service, conn, actor, identity)
        if _dependencies(conn, books) != dependencies:
            raise AccountingError("核对来源已变化，请重新核对", 409)
    normalized = {
        "account_code": params.get("account_code"),
        "compare_period": params.get("compare_period"),
        "target_voucher_id": params.get("target_voucher_id"),
        "limit": limit,
        "offset": offset,
    }
    source_version = _fingerprint(
        {
            "scope": [actor.firm_id, result["company_id"], book_id, period],
            "kind": kind,
            "target": {
                key: value for key, value in normalized.items() if key not in {"limit", "offset"}
            },
            "dependencies": dependencies,
        }
    )
    if expected_version is not None and source_version != expected_version:
        raise AccountingError("分页来源已变化，请从第一页重新核对", 409)
    result.update(firm_id=actor.firm_id, request=normalized, source_version=source_version)
    if result["handoff"]:
        result["handoff"]["source_version"] = source_version
    result["fingerprint"] = _fingerprint({k: v for k, v in result.items() if k != "fingerprint"})
    return result


def validate_claim(result, claim):
    """Reject prose that is not bound to the current facts.

    Only amount and subject can be asserted. A causal label such as waste/fraud
    is structurally unsupported, regardless of how plausible the prose sounds.
    """
    if not isinstance(result, dict) or not isinstance(claim, dict):
        raise AccountingError("分析结论格式无效", 422)
    if any(
        claim.get(key) != result.get(key)
        for key in ("company_id", "book_id", "period", "fingerprint")
    ):
        raise AccountingError("分析结论范围或来源版本不一致", 422)
    if claim.get("causal") or claim.get("cause"):
        raise AccountingError("只读分析不支持因果归因", 422)
    ids = claim.get("fact_ids")
    if not isinstance(ids, list) or len(ids) != 1 or ids[0] not in result["facts"]["fact_ids"]:
        raise AccountingError("每条金额结论必须绑定一个具体事实", 422)
    field = ids[0].split(":", 1)[1]
    amount = claim.get("amount_cents")
    if (
        type(amount) is not int
        or amount != result["facts"].get(field)
        or claim.get("currency") != result["facts"].get("currency")
    ):
        raise AccountingError("分析结论金额或币种与引用事实不一致", 422)
    return True
