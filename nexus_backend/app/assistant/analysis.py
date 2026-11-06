"""Finite, read-only analyses behind the accounting assistant.

The model cannot supply SQL, company ids, account versions or URLs. Company and
firm are resolved from the authenticated actor and the book; every reader runs
inside a transaction that re-checks book access. Results carry explicit coverage
and a version fingerprint, and never publish a claim that is not in the facts.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
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


def _book(service, actor, book_id, period):
    if not isinstance(book_id, str) or not book_id or not PERIOD_RE.fullmatch(str(period)):
        raise AccountingError("分析参数无效", 422)
    with _database(service).transaction(actor=actor) as conn:
        book = (getattr(service, "access", None) or AccessControl()).book(conn, actor, book_id, "read")
        if book["period"] != period:
            raise AccountingError("分析月份与账套不一致", 422)
        return dict(book)


def _sibling_book(service, actor, book, *, compare_period=None):
    with _database(service).transaction(actor=actor) as conn:
        if compare_period:
            if not PERIOD_RE.fullmatch(str(compare_period)):
                raise AccountingError("对比月份无效", 422)
            row = conn.execute(
                "SELECT id,period,company_id FROM books WHERE company_id=? AND period=?",
                (book["company_id"], compare_period),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id,period,company_id FROM books WHERE company_id=? AND period<? "
                "ORDER BY period DESC LIMIT 1",
                (book["company_id"], book["period"]),
            ).fetchone()
        if not row:
            return None
        (getattr(service, "access", None) or AccessControl()).book(conn, actor, row["id"], "read")
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


def _expense_bridge(service, actor, book_id, period, *, account_code, compare_period=None, limit=50, offset=0):
    if not isinstance(account_code, str) or not ACCOUNT_RE.fullmatch(account_code):
        raise AccountingError("费用科目格式无效", 422)
    if not isinstance(limit, int) or limit < 1 or limit > MAX_ROWS or offset < 0:
        raise AccountingError("分页参数无效", 422)
    book = _book(service, actor, book_id, period)
    previous = _sibling_book(service, actor, book, compare_period=compare_period)
    with _database(service).transaction(actor=actor) as conn:
        current_total, current_rows, current_page = _expense_rows(
            conn, book_id, account_code, limit, offset
        )
        name = conn.execute(
            "SELECT name FROM accounts WHERE book_id=? AND code=?", (book_id, account_code)
        ).fetchone()
    details = [
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
    missing = []
    previous_total = None
    previous_details = []
    if previous is None:
        missing.append("previous_period")
        status = "partial"
        summary = f"本期 {account_code} 有记录，但缺少可比上期账套，不能计算跨期差额。"
    else:
        with _database(service).transaction(actor=actor) as conn:
            previous_total, _, previous_page = _expense_rows(
                conn, previous["id"], account_code, limit, offset
            )
        previous_details = [
            {
                "voucher_id": row["voucher_id"],
                "voucher_no": row["voucher_no"],
                "date": row["voucher_date"],
                "description": row["description"],
                "amount_cents": int(row["debit_cents"]) - int(row["credit_cents"]),
                "evidence_href": _href(previous["id"], previous["period"], voucher=row["voucher_id"]),
            }
            for row in previous_page[:limit]
        ]
        status = "complete"
        delta = current_total - previous_total
        summary = (
            f"{account_code} {name['name'] if name else ''}：本期 {current_total} 分，"
            f"上期 {previous_total} 分，差额 {delta} 分；明细见引用记录。"
        )
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
        "currency_source": "book_default",
        "current_rows": details,
        "previous_rows": previous_details,
    }
    coverage = {
        "scope": "current_and_previous" if previous else "current_only",
        "rows_read": len(current_page[:limit]) + len(previous_details),
        "has_more": len(current_page) > limit,
        "pages": 1,
        "account_versions": sorted({row["account_version"] for row in current_page[:limit]}),
    }
    return _result("expense_bridge", book, status, summary, facts, coverage, missing, limit, offset)


def _invoice_voucher_diff(service, actor, book_id, period, *, target_voucher_id, limit=50, offset=0):
    if not isinstance(target_voucher_id, str) or not target_voucher_id:
        raise AccountingError("请指定目标凭证", 422)
    if not isinstance(limit, int) or limit < 1 or limit > MAX_ROWS or offset < 0:
        raise AccountingError("分页参数无效", 422)
    book = _book(service, actor, book_id, period)
    with _database(service).transaction(actor=actor) as conn:
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
        sources = conn.execute(
            "SELECT source_kind,source_id,source_version,amount_cents,evidence_ref FROM voucher_sources "
            "WHERE voucher_id=? ORDER BY id LIMIT ? OFFSET ?",
            (target_voucher_id, limit + 1, offset),
        ).fetchall()
    source_total = sum(int(row["amount_cents"]) for row in sources[:limit])
    missing = []
    if not sources:
        status = "partial"
        summary = "该凭证没有绑定来源金额，不能计算票据与凭证差额；请会计核对来源。"
        difference = None
        missing.append("voucher_source")
    else:
        difference = int(debit) - source_total
        status = "complete" if len(sources) <= limit else "partial"
        summary = (
            f"凭证 {voucher['voucher_no']} 借方 {int(debit)} 分，来源金额 {source_total} 分，"
            f"差额 {difference} 分。差额不是舞弊或浪费结论。"
        )
    facts = {
        "voucher_id": voucher["id"],
        "voucher_no": int(voucher["voucher_no"]),
        "voucher_date": voucher["voucher_date"],
        "description": voucher["description"],
        "voucher_total_cents": int(debit),
        "voucher_total": _money(int(debit)),
        "source_total_cents": source_total if sources else None,
        "source_total": _money(source_total) if sources else None,
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
                "evidence_href": _href(book_id, period, voucher=voucher["id"], source=row["source_id"]),
            }
            for row in sources[:limit]
        ],
    }
    coverage = {
        "scope": "voucher_and_linked_sources",
        "rows_read": len(sources[:limit]),
        "has_more": len(sources) > limit,
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
    facts = dict(facts)
    facts["fact_ids"] = [
        f"{kind}:{key}" for key in ("current_total_cents", "difference_cents", "voucher_total_cents", "source_total_cents")
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
            else {"role": "accountant", "reason": "分析覆盖不完整，需要会计在业务入口核对"}
        ),
        "limits": {"max_rows": MAX_ROWS, "limit": limit, "offset": offset},
        "model_calls": 0,
        "real_model_evaluated": False,
        "target": target,
    }
    payload["fingerprint"] = _fingerprint({key: payload[key] for key in ("kind", "book_id", "period", "status", "facts", "coverage", "missing")})
    return payload


def analyze(service, actor, book_id, period, kind, **params):
    if kind not in KINDS:
        raise AccountingError("不支持的分析类型", 422)
    allowed = {
        "expense_bridge": {"account_code", "compare_period", "limit", "offset"},
        "invoice_voucher_diff": {"target_voucher_id", "limit", "offset"},
    }[kind]
    unexpected = set(params) - allowed
    if unexpected:
        raise AccountingError("分析参数不支持", 422)
    if kind == "expense_bridge":
        return _expense_bridge(service, actor, book_id, period, **params)
    return _invoice_voucher_diff(service, actor, book_id, period, **params)


def validate_claim(result, claim):
    """Reject prose that is not bound to the current facts.

    Only amount and subject can be asserted. A causal label such as waste/fraud
    is structurally unsupported, regardless of how plausible the prose sounds.
    """
    if not isinstance(result, dict) or not isinstance(claim, dict):
        raise AccountingError("分析结论格式无效", 422)
    if claim.get("company_id") not in (None, result.get("company_id")):
        raise AccountingError("分析结论跨企业", 422)
    amount = claim.get("amount_cents")
    allowed = {
        result["facts"].get("difference_cents"),
        result["facts"].get("current_total_cents"),
        result["facts"].get("voucher_total_cents"),
    }
    if amount is not None and amount not in allowed:
        raise AccountingError("分析结论金额与事实不一致", 422)
    if claim.get("causal") or claim.get("cause"):
        raise AccountingError("只读分析不支持因果归因", 422)
    fact_ids = claim.get("fact_ids")
    known = set(result["facts"].get("fact_ids") or ())
    if fact_ids is not None:
        if not isinstance(fact_ids, list) or not fact_ids:
            raise AccountingError("叙述必须绑定事实", 422)
        if any(item not in known for item in fact_ids):
            raise AccountingError("叙述引用了未知事实", 422)
    if claim.get("text") and fact_ids is None:
        raise AccountingError("叙述必须绑定事实", 422)
    return True
