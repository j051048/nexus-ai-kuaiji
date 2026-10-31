"""Read pages of a completed private diagnosis, bound to its entire snapshot.

Transport pagination only: domain reconciliation still reads the full authorized
book. Cursors are integrity/version bindings, never authorization tokens. No
model calls, writes, auto-restart or partial merge of old and new facts.
"""

import base64
import json
from copy import deepcopy

from app.accounting.access import FIRM_EDITORS
from app.accounting.domain import AccountingError
from app.accounting.service import fingerprint
from .delivery_details import collect_details, detail_issues, validate_details

SCHEMA = "assistant-delivery-page-v1"
ROW_CODES = {
    "control": {"control_account_difference"},
    "bank": {"bank_coverage_missing", "bank_account_difference", "bank_adjustments"},
}


def _cursor(binding, offset):
    raw = json.dumps(
        {"v": 1, "binding": binding, "offset": offset}, sort_keys=True, separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _offset(after, binding):
    if after is None:
        return 0
    try:
        if not isinstance(after, str) or not 1 <= len(after) <= 2048:
            raise ValueError
        value = json.loads(
            base64.b64decode(after + "=" * (-len(after) % 4), altchars=b"-_", validate=True)
        )
        offset = value["offset"]
        if (
            type(offset) is not int
            or not 0 <= offset <= 1_000_000
            or after != _cursor(binding, offset)
        ):
            raise ValueError
        return offset
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AccountingError("明细游标已失效，请从本次诊断第一页重新读取", 409) from None


def _rows(source, kind):
    result = []
    for row in source[kind]["rows"]:
        single = deepcopy(source)
        single[kind]["rows"] = [row]
        if kind == "bank":
            single["bank"]["unresolved_adjustment_count"] = row["adjustment_count"]
        issues = [i for i in detail_issues(single) if i["code"] in ROW_CODES[kind]]
        identity = {
            "id": row["control_account_id"] if kind == "control" else row["account_id"],
            "kind": row.get("kind", "bank"),
            "aux": row["aux"],
        }
        result.append(
            {
                "key": fingerprint(identity),
                "label": (row["code"] + " " + row["name"]) if kind == "control" else row["name"],
                "passed": row["passed"],
                "issues": issues,
            }
        )
    return result


def read_page(service, actor, conversation_id, turn_id, *, kind="bank", after=None, limit=10):
    with service.db.transaction(actor=actor) as conn:
        conversation = service._conversation(conn, actor, conversation_id)
        row = conn.execute(
            "SELECT status,reply_json,snapshot_json FROM assistant_turns WHERE id=? AND conversation_id=?",
            (turn_id, conversation_id),
        ).fetchone()
        if not row:
            raise AccountingError("对话任务不存在", 404)
        if service.access.firm_role(conn, actor) not in FIRM_EDITORS:
            raise AccountingError("内部月结明细需由有账套权限的服务会计核对", 403)
        if kind not in ROW_CODES or type(limit) is not int or not 1 <= limit <= 20:
            raise AccountingError("明细分页参数无效", 422)
        if row["status"] != "succeeded" or not row["reply_json"]:
            raise AccountingError("本次诊断尚未完成或已失效，请重新核对；不会自动重发", 409)
        frozen = json.loads(row["snapshot_json"])
        view = frozen.get("delivery_task", {})
        sources = [s for s in view.get("sources", []) if s["kind"] == "reconciliation"]
        if view.get("coverage") != "selected_book" or len(sources) != 1:
            raise AccountingError("本次诊断不包含可分页的内部勾稽明细", 409)
        original = sources[0]
        book = service.access.book(conn, actor, view["book_id"])
        if (
            book["company_id"] != conversation["company_id"]
            or book["period"] != view["period"]
            or not (book["permissions"]["edit"] or book["permissions"]["review"])
        ):
            raise AccountingError("本次明细范围已失去授权", 404)
        current = service.snapshot_in_transaction(conn, actor, conversation, delivery_task=True)
        snapshot_fingerprint = fingerprint(frozen)
        if fingerprint(current) != snapshot_fingerprint:
            raise AccountingError("本次诊断依据已变化，请重新发起诊断；旧页不会合并到新资料", 409)
        validate_details(original, conversation["company_id"], book["id"], book["period"])
        if original["status"] != "current":
            raise AccountingError("当前正式期初或核算依据尚不完整，请按诊断提示处理", 409)
        binding = fingerprint(
            {
                "actor": [actor.firm_id, actor.user_id],
                "conversation": conversation_id,
                "turn": turn_id,
                "snapshot": snapshot_fingerprint,
                "source": original["inputs_fingerprint"],
                "kind": kind,
                "limit": limit,
            }
        )
        offset = _offset(after, binding)
        # Same private read transaction. Never add this larger page to model context.
        source = collect_details(
            service.db, conn, actor, book, page_kind=kind, offset=offset, limit=limit
        )
        if (
            source["status"] != "current"
            or source["inputs_fingerprint"] != original["inputs_fingerprint"]
        ):
            raise AccountingError("本次勾稽依据已变化，请重新诊断", 409)
        rows = _rows(source, kind)
        total = source[kind]["omitted_rows"] + len(rows)
        if offset > total:
            raise AccountingError("明细游标超出本次诊断范围，请从第一页读取", 409)
        end = offset + len(rows)
        result = {
            "schema": SCHEMA,
            "company_id": book["company_id"],
            "book_id": book["id"],
            "period": book["period"],
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "inputs_fingerprint": original["inputs_fingerprint"],
            "snapshot_fingerprint": snapshot_fingerprint,
            "kind": kind,
            "limit": limit,
            "start": offset,
            "end": end,
            "total": total,
            "has_more": end < total,
            "cursor": _cursor(binding, end),
            "rows": rows,
            "accounting_approved": False,
        }
        result["page_fingerprint"] = fingerprint(result)
        return result
