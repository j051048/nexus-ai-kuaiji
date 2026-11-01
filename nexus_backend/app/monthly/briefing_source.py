"""简报来源契约：只把已发布冻结报表当作客户可见的正式成果。

本模块只读：不生成报表、不改状态。任何一项校验失败都 fail-closed，宁可显示
「待会计复核 / 发布」，也不拿草稿或旧口径金额顶替正式成果。
"""

import json
import re

from ..closing.queries import receipt_valid_sql

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_PERIOD = re.compile(r"\A20[0-9]{2}-(0[1-9]|1[0-2])\Z")

REPORT_STATUSES = ("published",)
CLOSE_STATUSES = ("locked", "published")

AXIS_KEYS = ("internal_close", "external_receipt", "tax_filing", "notification", "client_sign")
AXIS_LABELS = {
    "internal_close": "内部记账与锁期",
    "external_receipt": "外部软件回执",
    "tax_filing": "税务申报",
    "notification": "通知送达",
    "client_sign": "客户签收",
}
AXIS_BASES = {
    "internal_close": "账套月结锁定或历史期间关闭",
    "external_receipt": "导出批次的客户回执核验记录",
    "tax_filing": "目前没有申报回执台账，只能用已复核底稿做内部准备证据",
    "notification": "交付通知的实际外发记录",
    "client_sign": "客户对冻结资料包的回执",
}


def _get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _bad(reason):
    return {"ok": False, "reason": reason}


def _sha(value):
    return isinstance(value, str) and bool(_SHA256.match(value))


def _parse_report(row):
    if row is None:
        return _bad("missing_report")
    if not _sha(row["content_hash"]):
        return _bad("bad_report_hash")
    if not _sha(row["inputs_fingerprint"]):
        return _bad("bad_report_fingerprint")
    try:
        version = int(row["version"])
    except (TypeError, ValueError):
        return _bad("bad_report_version")
    if version <= 0:
        return _bad("bad_report_version")
    content = row["content_json"]
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return _bad("bad_report_content")
    if not isinstance(parsed, dict):
        return _bad("bad_report_content")
    return {
        "ok": True,
        "value": {
            "report_id": row["id"],
            "version": version,
            "template_id": row["template_id"],
            "template_version": row["template_version"],
            "status": row["status"],
            "content_hash": row["content_hash"],
            "inputs_fingerprint": row["inputs_fingerprint"],
            "published_at": row["published_at"],
            "published_by": row["published_by"],
            "supersedes_id": row["supersedes_id"],
            "content": parsed,
        },
    }


def _parse_close(row, report_id):
    if row is None:
        return None
    if row["status"] not in CLOSE_STATUSES:
        return {"ok": False, "reason": "close_not_locked"}
    if _get(row, "report_run_id") and _get(row, "report_run_id") != report_id:
        return {"ok": False, "reason": "close_report_mismatch"}
    return {
        "ok": True,
        "value": {
            "close_run_id": row["id"],
            "version": row["version"],
            "status": row["status"],
            "report_run_id": _get(row, "report_run_id"),
            "locked_at": _get(row, "locked_at"),
            "inputs_fingerprint": _get(row, "inputs_fingerprint"),
        },
    }


def _parse_package(row, report):
    if row is None:
        return None
    if report is None:
        return {"ok": False, "reason": "package_without_report"}
    if _get(row, "report_id") != report["report_id"] or int(_get(row, "report_version") or 0) != report["version"]:
        return {"ok": False, "reason": "package_report_mismatch"}
    if not _sha(_get(row, "manifest_sha256")):
        return {"ok": False, "reason": "bad_package_manifest"}
    return {
        "ok": True,
        "value": {
            "package_id": row["id"],
            "version": row["version"],
            "report_id": _get(row, "report_id"),
            "report_version": _get(row, "report_version"),
            "manifest_sha256": _get(row, "manifest_sha256"),
            "created_at": _get(row, "created_at"),
        },
    }


def _amounts(content):
    profit = content.get("profit_statement")
    sheet = content.get("balance_sheet")
    if not isinstance(profit, dict) or not isinstance(sheet, dict):
        return _bad("bad_report_content")
    items = sheet.get("items")
    if not isinstance(items, list):
        return _bad("bad_report_content")

    def total(prefixes):
        return sum(
            int(item.get("amount_cents", 0))
            for item in items
            if isinstance(item, dict)
            and str(item.get("code", "")).startswith(tuple(prefixes))
        )

    def profit_items(prefixes):
        return [
            item
            for item in (profit.get("items") or [])
            if isinstance(item, dict) and str(item.get("code", "")).startswith(tuple(prefixes))
        ]

    for key in ("revenue_cents", "expense_cents", "profit_cents"):
        if not isinstance(profit.get(key), int):
            return _bad("bad_report_amounts")
    return {
        "ok": True,
        "value": {
            "flows": {
                "income": profit["revenue_cents"],
                "expense": profit["expense_cents"],
                "net": profit["profit_cents"],
            },
            "receivable": total(("1122", "1221")),
            "payable": total(("2202", "2203")),
            "tax": total(("2221",)),
            "expenses": profit_items(("5", "6")),
        },
    }


def resolve_source(conn, book, period, dialect):
    """读取简报来源事实，返回带 axes 的契约对象。"""
    report_rows = conn.execute(
        "SELECT * FROM report_runs WHERE book_id=? AND period=? AND status='published' "
        "ORDER BY version DESC",
        (book["id"], period),
    ).fetchall()
    parsed = [_parse_report(row) for row in report_rows]
    invalid = next((item for item in parsed if not item["ok"]), None)
    report = parsed[0]["value"] if parsed and parsed[0]["ok"] else None

    close_row = conn.execute(
        "SELECT * FROM internal_close_runs WHERE book_id=? AND period=? ORDER BY version DESC LIMIT 1",
        (book["id"], period),
    ).fetchone()
    close = _parse_close(close_row, report["report_id"] if report else None)
    legacy_closed = conn.execute(
        "SELECT status FROM periods WHERE book_id=?", (book["id"],)
    ).fetchone()
    legacy_ok = bool(legacy_closed and legacy_closed["status"] == "closed")

    package_row = conn.execute(
        "SELECT * FROM monthly_delivery_packages WHERE book_id=? AND period=? ORDER BY version DESC LIMIT 1",
        (book["id"], period),
    ).fetchone()
    package = _parse_package(package_row, report)

    if report and isinstance(close, dict) and not close["ok"]:
        state, reason = "inconsistent", close["reason"]
    elif report and close is None and not legacy_ok:
        state, reason = "inconsistent", "close_not_locked"
    elif report and isinstance(package, dict) and not package["ok"]:
        state, reason = "inconsistent", package["reason"]
    elif report:
        state, reason = "published", ""
    elif legacy_ok:
        state, reason = "legacy_close", "legacy_period_close"
    elif invalid is not None:
        state, reason = "inconsistent", invalid["reason"]
    else:
        state, reason = "pending_report", "no_published_report"

    amounts = _amounts(report["content"]) if report else None
    if amounts is not None and not amounts["ok"]:
        state, reason, amounts = "inconsistent", amounts["reason"], None
    if state != "published":
        amounts = None

    axes = _axes(conn, book, period, dialect, close, package, legacy_ok)
    return {
        "company_id": book["company_id"],
        "book_id": book["id"],
        "period": period,
        "state": state,
        "reason": reason,
        "coverage": {"period": period, "from": f"{period}-01", "to": f"{period}-28"},
        "report": None
        if report is None
        else {key: value for key, value in report.items() if key != "content"},
        "superseded_report_ids": [
            item["value"]["report_id"] for item in parsed[1:] if item["ok"]
        ],
        "close": close["value"] if isinstance(close, dict) and close["ok"] else None,
        "package": package["value"] if isinstance(package, dict) and package["ok"] else None,
        "amounts": None if amounts is None else amounts["value"],
        "axes": axes,
        "axis_labels": AXIS_LABELS,
        "axis_bases": AXIS_BASES,
        "client_visible": state == "published",
    }


def _count(conn, sql, params):
    row = conn.execute(sql, params).fetchone()
    return int(_get(row, "total") or 0) if row else 0


def _axes(conn, book, period, dialect, close, package, legacy_ok):
    exports = _count(
        conn,
        "SELECT COUNT(*) AS total FROM exports WHERE book_id=?",
        (book["id"],),
    )
    unverified = _count(
        conn,
        "SELECT COUNT(*) AS total FROM exports x WHERE x.book_id=? AND NOT "
        + receipt_valid_sql(dialect),
        (book["id"],),
    )
    workpapers = _count(
        conn,
        "SELECT COUNT(*) AS total FROM tax_workpapers WHERE book_id=? AND period=? AND status='reviewed'",
        (book["id"], period),
    )
    notifications = 0
    acknowledgements = 0
    if isinstance(package, dict) and package["ok"]:
        package_id = package["value"]["package_id"]
        notifications = _count(
            conn,
            "SELECT COUNT(*) AS total FROM delivery_notifications WHERE package_id=? AND state='sink_written'",
            (package_id,),
        )
        acknowledgements = _count(
            conn,
            "SELECT COUNT(*) AS total FROM monthly_delivery_acknowledgements WHERE package_id=?",
            (package_id,),
        )

    internal_close = bool(legacy_ok or (isinstance(close, dict) and close["ok"]))
    return {
        "internal_close": {"done": internal_close, "evidence": "period_closed" if legacy_ok else "close_locked"},
        "external_receipt": {
            "done": bool(exports) and not unverified,
            "evidence": f"exports={exports},unverified={unverified}",
        },
        "tax_filing": {
            "done": False,
            "evidence": f"reviewed_workpapers={workpapers};no_filing_receipt",
        },
        "notification": {"done": bool(notifications), "evidence": f"sink_written={notifications}"},
        "client_sign": {"done": bool(acknowledgements), "evidence": f"acknowledgements={acknowledgements}"},
    }
