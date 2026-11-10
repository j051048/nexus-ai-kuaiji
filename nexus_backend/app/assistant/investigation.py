"""有限只读调查：三个类型化域工具，服务端注入身份与范围。

The model never chooses a company, a SQL string or a URL. It may only ask for
one of the three named facts about the book already in scope; every step
re-checks authorization before reading, and the loop stops at the first
refusal instead of retrying or widening the scope.
"""

import json
import hashlib
import os
from datetime import datetime, timezone

from ..accounting.domain import AccountingError

TOOLS = ("voucher_status", "close_blockers", "delivery_status")
MAX_READS = 3
ENV_FLAG = "ACCOUNTING_ASSISTANT_INVESTIGATION"


def evidence_fingerprint(result):
    """Bind displayed scope, coverage and observed facts; exclude wall-clock time."""
    payload = {
        key: result[key]
        for key in ("book_id", "period", "status", "findings", "missing", "coverage")
    }
    payload["scope"] = {key: result["evidence"][key] for key in ("firm_id", "company_id")}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def enabled():
    return os.getenv(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, actor, book_id, action, details):
    conn.execute(
        "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            actor.firm_id,
            book_id,
            actor.user_id,
            action,
            book_id,
            json.dumps(details, ensure_ascii=False),
            _now(),
        ),
    )


def _voucher_status(conn, book_id, period):
    posted = conn.execute(
        "SELECT COUNT(*) AS n FROM vouchers WHERE book_id=? AND period=?", (book_id, period)
    ).fetchone()["n"]
    drafts = conn.execute(
        "SELECT COUNT(*) AS n FROM drafts WHERE book_id=? AND status='draft'", (book_id,)
    ).fetchone()["n"]
    return {"posted_vouchers": int(posted), "draft_rows": int(drafts)}


def _close_blockers(conn, book_id, period):
    row = conn.execute(
        "SELECT status,version,checks_json FROM internal_close_runs WHERE book_id=? AND period=? "
        "ORDER BY version DESC LIMIT 1",
        (book_id, period),
    ).fetchone()
    if not row:
        return {"status": "no_run", "blockers": [], "version": None}
    try:
        checks = json.loads(row["checks_json"])
    except (TypeError, ValueError):
        return {"status": row["status"], "blockers": [], "version": row["version"], "unknown": True}
    blocked = [
        {"code": check.get("code"), "next_action": check.get("next_action")}
        for check in checks
        if isinstance(check, dict) and check.get("status") == "blocked"
    ]
    return {"status": row["status"], "blockers": blocked, "version": int(row["version"])}


def _delivery_status(conn, book_id, period):
    package = conn.execute(
        "SELECT id,version,created_at FROM monthly_delivery_packages WHERE book_id=? AND period=? "
        "ORDER BY version DESC LIMIT 1",
        (book_id, period),
    ).fetchone()
    if not package:
        return {"package": None, "opened": False, "notifications": {}}
    opened = conn.execute(
        "SELECT 1 FROM monthly_delivery_opens WHERE package_id=?", (package["id"],)
    ).fetchone()
    states = {
        row["state"]: int(row["n"])
        for row in conn.execute(
            "SELECT state,COUNT(*) AS n FROM delivery_notifications WHERE package_id=? GROUP BY state",
            (package["id"],),
        ).fetchall()
    }
    return {
        "package": {"version": int(package["version"]), "created_at": package["created_at"]},
        "opened": bool(opened),
        "notifications": states,
    }


READERS = {
    "voucher_status": _voucher_status,
    "close_blockers": _close_blockers,
    "delivery_status": _delivery_status,
}


def investigate(service, actor, book_id, period, *, on_step=None):
    """Run the fixed three-read investigation. Refusal stops the loop."""
    if not enabled():
        raise AccountingError("有限域调查尚未启用", 403)
    if not isinstance(book_id, str) or not isinstance(period, str):
        raise AccountingError("调查参数无效", 422)

    parallelism = os.getenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "1")
    if parallelism != "1":
        from .parallel_reads import run

        return run(service, actor, book_id, period, parallelism=parallelism, on_step=on_step)

    steps = []
    findings = {}
    missing = []
    status = "complete"
    started = _now()
    for index, name in enumerate(TOOLS):
        if on_step is not None:
            on_step(name)
        if index >= MAX_READS:  # defensive: limits are server-side constants
            missing = list(TOOLS[index:])
            status = "partial"
            break
        try:
            with service.db.transaction(actor=actor) as conn:
                # Re-authorize at read time: revocation mid-loop stops here.
                book = service.access.book(conn, actor, book_id, "read")
                if book["period"] != period:
                    raise AccountingError("调查月份与账套不一致", 422)
                facts = READERS[name](conn, book_id, period)
            findings[name] = facts
            steps.append({"tool": name, "ok": True})
        except AccountingError as error:
            status = "blocked"
            missing = list(TOOLS[index:])
            steps.append({"tool": name, "ok": False, "code": "refused", "message": str(error)})
            break
        except Exception:  # a reader bug must not pretend the fact is known
            status = "blocked"
            missing = list(TOOLS[index:])
            steps.append({"tool": name, "ok": False, "code": "failed"})
            break

    return finish(service, actor, book_id, period, findings, missing, steps, status, started)


def finish(
    service,
    actor,
    book_id,
    period,
    findings,
    missing,
    steps,
    status,
    started,
    *,
    deadline=None,
    metrics=None,
):
    # A new transaction observes revocation committed during earlier reads.
    # Audit writes never turn the evidence readers into writable transactions.
    try:
        if deadline is not None:
            import time

            if time.monotonic() >= deadline:
                raise AccountingError("只读调查预算已耗尽", 409)
        with service.db.transaction(actor=actor) as conn:
            scope = service.access.book(conn, actor, book_id, "read")
        with service.db.transaction(
            write=True, actor=actor, company_id=scope["company_id"], write_scope="company"
        ) as conn:
            book = service.access.book(conn, actor, book_id, "read")
            if book["period"] != period:
                raise AccountingError("调查月份与账套不一致", 422)
            for index, (name, facts) in enumerate(findings.items()):
                if metrics is not None:
                    metrics["validation_reads"] += 1
                if READERS[name](conn, book_id, period) != facts:
                    raise AccountingError("调查依据已变化，请重新核对", 409)
                _audit(
                    conn,
                    actor,
                    book_id,
                    "assistant.investigation.step",
                    {"tool": name, "period": period, "index": index},
                )
            if deadline is not None and time.monotonic() >= deadline:
                raise AccountingError("只读调查预算已耗尽", 409)
            _audit(
                conn,
                actor,
                book_id,
                "assistant.investigation.finish",
                {"period": period, "status": status, "reads": len(findings)},
            )
    except Exception:
        # A final read/audit failure cannot publish the earlier, now unverified facts.
        status, findings, missing = "blocked", {}, list(TOOLS)
    if status != "complete":
        handoff = {
            "role": "reviewer",
            "reason": "调查在权限或数据边界处停止，未覆盖的事实不由系统猜测",
        }
    else:
        handoff = None
    result = {
        "book_id": book_id,
        "period": period,
        "status": status,
        "started_at": started,
        "coverage": {"read": len(findings), "of": len(TOOLS)},
        "findings": findings,
        "missing": missing,
        "steps": steps,
        "handoff": handoff,
        "limits": {"max_reads": MAX_READS, "reads_used": len(steps)},
        "model_calls": 0,
        "evidence": None,
    }
    if findings:
        result["evidence"] = {"firm_id": actor.firm_id, "company_id": book["company_id"]}
        result["evidence"]["fingerprint"] = evidence_fingerprint(result)
    return result
