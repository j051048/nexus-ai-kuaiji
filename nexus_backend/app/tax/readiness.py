"""月度申报前清单：只说已经能证明的事。

Readiness never invents a tax result: it reports whether a reviewed workpaper
exists for the period, whether that workpaper still matches the current source
snapshot, and which materials are still missing. Unsupported situations are
blocked with the reason the workpaper generator itself would raise.
"""

from ..accounting.domain import AccountingError
from .filings import list_for as filing_list
from .filings import summary as filing_summary
from .workpaper import _period, _snapshot

LABELS = {"vat": "增值税", "surtax": "附加税费"}

READY = "ready"
UNREVIEWED = "unreviewed"
STALE = "stale"
MISSING = "missing"
BLOCKED = "blocked"


def _latest(conn, book_id, period):
    return conn.execute(
        "SELECT * FROM tax_workpapers WHERE book_id=? AND period=? ORDER BY version DESC LIMIT 1",
        (book_id, period),
    ).fetchone()


def _kind(kind, readiness, *, next_action, owner_role="账套会计", workpaper=None, reason=""):
    return {
        "kind": kind,
        "label": LABELS[kind],
        "readiness": readiness,
        "reason": reason,
        "next_action": next_action,
        "owner_role": owner_role,
        "workpaper_id": workpaper["id"] if workpaper else None,
        "workpaper_version": int(workpaper["version"]) if workpaper else None,
        "reviewed": bool(workpaper and workpaper["status"] == "reviewed"),
        "current": readiness in (UNREVIEWED, READY),
        "action_target": {"kind": "tax", "book_id": None, "period": None},
    }


def _readiness(service, actor, book_id, period=None):
    scope = service._scope(actor, book_id, "read")
    period = _period(period or scope["period"])
    with service.db.transaction(actor=actor) as conn:
        book = service.access.book(conn, actor, book_id, "read")
        profile = service._profile(conn, book, period)
        payload = {
            "book_id": book_id,
            "company_id": book["company_id"],
            "period": period,
            "policy": profile,
            "supported": False,
            "blocked_reason": "",
            "kinds": [],
            "external_result": "none",
            "source": "workpaper",
        }
        if not profile:
            payload["blocked_reason"] = f"缺少{period}生效的纳税人档案，无法确定适用税种与税率"
            payload["kinds"] = [
                _kind("vat", BLOCKED, reason=payload["blocked_reason"], next_action="登记税务档案")
            ]
            return payload

        latest = _latest(conn, book_id, period)
        try:
            snapshot = _snapshot(conn, book, period, profile)
            digest = snapshot["digest"]
        except AccountingError as error:  # the generator's own supported-scope message
            reason = error.detail
            payload["blocked_reason"] = reason
            payload["kinds"] = [
                _kind("vat", BLOCKED, reason=reason, next_action="人工核验后登记，系统不估算")
            ]
            return payload

        payload["supported"] = True
        if latest is None:
            payload["kinds"] = [
                _kind("vat", MISSING, next_action="生成增值税底稿"),
                _kind("surtax", MISSING, next_action="生成附加税费底稿"),
            ]
            return payload

        current = latest["source_digest"] == digest
        if not current:
            payload["kinds"] = [
                _kind(
                    "vat",
                    STALE,
                    reason="底稿生成后来源（发票、正式账或档案）已变化",
                    next_action="按当前来源重新生成底稿",
                    workpaper=latest,
                ),
                _kind(
                    "surtax",
                    STALE,
                    reason="附加税费随增值税底稿重算",
                    next_action="按当前来源重新生成底稿",
                    workpaper=latest,
                ),
            ]
            return payload

        readiness = READY if latest["status"] == "reviewed" else UNREVIEWED
        next_action = "可直接办理申报" if readiness == READY else "由另一位复核人独立复核"
        payload["kinds"] = [
            _kind("vat", readiness, next_action=next_action, workpaper=latest),
            _kind("surtax", readiness, next_action=next_action, workpaper=latest),
        ]
        for kind in payload["kinds"]:
            kind["variance_cents"] = int(latest["variance_cents"])
            kind["variance_reason"] = latest["variance_reason"] or ""
            kind["action_target"] = {
                "kind": "tax",
                "book_id": book_id,
                "period": period,
            }
        return payload


def filing_readiness(service, actor, book_id, period=None):
    """Workpaper state plus the externally registered result, kept separate."""
    payload = _readiness(service, actor, book_id, period)
    rows = filing_list(service, actor, book_id, payload["period"])
    state = filing_summary(rows)
    payload["external_result"] = state["external_result"]
    payload["filing_state"] = state["filing_state"]
    payload["filings"] = rows
    for kind in payload["kinds"]:
        latest = state["by_kind"].get(kind["kind"])
        kind["filing"] = latest
        kind["external_result"] = "registered" if latest else "none"
    return payload
