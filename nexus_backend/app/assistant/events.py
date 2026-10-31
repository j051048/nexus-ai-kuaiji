"""Replayable milestones projected from durable turn facts, not model prose.

The cursor binds an observed prefix; it is not an authorization token. Callers
must recheck the conversation scope before invoking this module. There are at
most three milestones, with intentional gaps for tasks cancelled before send.
"""

import base64
import hashlib
import json

from app.accounting.domain import AccountingError
from app.execution.records import execution_record

SCHEMA = "assistant-turn-events-v1"
TERMINAL = {"succeeded", "failed", "interrupted", "stale", "cancelled"}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _milestones(row):
    events = [
        {
            "seq": 1,
            "kind": "created",
            "at": row["created_at"],
            "message": "任务已保存，等待处理。",
            "accounting_approved": False,
        }
    ]
    if row["sent_at"]:
        events.append(
            {
                "seq": 2,
                "kind": "send_marked",
                "at": row["sent_at"],
                "message": "已登记请求发送标记；供应商结果尚未确认。",
                "accounting_approved": False,
            }
        )
    if row["status"] in TERMINAL:
        record = execution_record("chat", row)
        failure = record["failure"]
        unknown = bool(failure and failure["outcome_unknown"]) or bool(
            row["sent_at"] and row["status"] == "cancelled"
        )
        message = (
            "请求结果未知，请联系管理员核对执行与费用记录；不会自动重发。"
            if unknown
            else "回答已生成；账务审核与导出仍需会计办理。"
            if row["status"] == "succeeded"
            else "任务已取消，不会自动重发。"
            if row["status"] == "cancelled"
            else failure["message"]
            if failure
            else "任务已结束，请核对执行记录。"
        )
        events.append(
            {
                "seq": 3,
                "kind": "finished",
                "at": row["finished_at"],
                "message": message,
                "accounting_approved": False,
                "status": row["status"],
                "outcome_unknown": unknown,
                "automatic_retry": False,
                "next_action": "inspect_execution" if unknown else record["handoff"]["action"],
            }
        )
    return events


def turn_events(row, conversation, *, after=None, limit=3):
    row = dict(row)
    if type(limit) is not int or not 1 <= limit <= 3:
        raise AccountingError("执行记录分页参数无效", 422)
    events = _milestones(row)
    binding = _digest(
        [row[key] for key in ("firm_id", "company_id", "conversation_id", "id", "created_by")]
    )

    def cursor(seq):
        payload = {
            "v": 1,
            "binding": binding,
            "seq": seq,
            "prefix": _digest([e for e in events if e["seq"] <= seq]),
        }
        return (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )

    seq = 0
    if after is not None:
        try:
            if not isinstance(after, str) or not 1 <= len(after) <= 2048:
                raise ValueError
            payload = json.loads(
                base64.b64decode(after + "=" * (-len(after) % 4), altchars=b"-_", validate=True)
            )
            seq = payload["seq"]
            if (
                type(seq) is not int
                or seq not in {e["seq"] for e in events}
                or after != cursor(seq)
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise AccountingError("执行记录游标已失效，请重新读取", 409) from None
    remaining = [e for e in events if e["seq"] > seq]
    page = remaining[:limit]
    return {
        "schema": SCHEMA,
        "company_id": row["company_id"],
        "conversation_id": row["conversation_id"],
        "turn_id": row["id"],
        "scope_version": conversation["scope_version"],
        "events": page,
        "cursor": cursor(page[-1]["seq"] if page else seq),
        "has_more": len(remaining) > limit,
        "terminal": row["status"] in TERMINAL,
        "status": row["status"],
    }
