"""Deterministic AI routing guard and immutable decision trace.

Levels are intentionally narrow:
- blocked: an explicit request to make an accounting decision/write is refused;
- model: read-only advisory question forwarded to the configured provider.

The trace never stores the raw question, only its SHA-256 and the fingerprint.
"""

from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint, now

# Only explicit "do it for me" accounting actions are blocked. Questions such as
# "为什么不能导出" remain read-only and must not be caught here.
_ACTION = r"(?:记账|记到账上|过账|审核|复核|签收|提交|发(?:送|给|到)|导出|删除|修改|改凭证)"
_FOLLOW_UP = re.compile(r"^(?:那|那么|这|它|上述|刚才)|这家企业|这个月|该企业")

_WRITE_REQUEST = re.compile(
    rf"(?:帮我|替我|帮(?!忙))[^。！？]{{0,18}}{_ACTION}"
    rf"|(?:直接|现在|把|将)[^。！？]{{0,16}}{_ACTION}"
    rf"|{_ACTION}\s*(?:一下|吧|掉)"
)


class AIRoutingService:
    def __init__(self, db, access):
        self.db, self.access = db, access

    @staticmethod
    def classify(question: str, *, has_context: bool = False) -> tuple[str, str, str]:
        if _WRITE_REQUEST.search(question):
            return (
                "blocked",
                "read_only_guard",
                "会计助手只做只读核对，不能代为记账、复核、签收、发送或导出；请在业务入口由有权限的人办理。",
            )
        if has_context and _FOLLOW_UP.search(question):
            return (
                "memory",
                "conversation_context",
                "问题依赖本轮之前的上下文；只带入当前企业范围内的历史轮次。",
            )
        return ("model", "", "未命中硬规则；作为只读咨询交给模型，回答仍受当前授权范围限制。")

    def record(
        self,
        conn,
        actor,
        *,
        company_id: str,
        question: str,
        level: str,
        matched_rule: str,
        reason: str,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        budget_state: str = "unknown",
    ) -> dict:
        if not self.access.company(conn, actor, company_id):
            raise AccountingError("无权访问该企业", 404)
        decision = {
            "id": uuid4().hex,
            "firm_id": actor.firm_id,
            "company_id": company_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "level": level,
            "matched_rule": matched_rule,
            "reason": reason,
            "budget_state": budget_state,
            "input_fingerprint": fingerprint(
                {
                    "question": question,
                    "level": level,
                    "rule": matched_rule,
                    "company_id": company_id,
                }
            ),
            "created_by": actor.user_id,
            "created_at": now(),
        }
        conn.execute(
            "INSERT INTO ai_route_decisions(id,firm_id,company_id,conversation_id,turn_id,"
            "question_sha256,level,matched_rule,reason,budget_state,input_fingerprint,"
            "created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision["id"],
                decision["firm_id"],
                decision["company_id"],
                decision["conversation_id"],
                decision["turn_id"],
                decision["question_sha256"],
                decision["level"],
                decision["matched_rule"],
                decision["reason"],
                decision["budget_state"],
                decision["input_fingerprint"],
                decision["created_by"],
                decision["created_at"],
            ),
        )
        return decision

    def list(self, actor, company_id: str, limit: int = 50) -> dict:
        with self.db.transaction(actor=actor) as conn:
            if not self.access.company(conn, actor, company_id):
                raise AccountingError("无权访问该企业", 404)
            rows = conn.execute(
                "SELECT id,conversation_id,turn_id,question_sha256,level,matched_rule,"
                "reason,budget_state,created_by,created_at FROM ai_route_decisions "
                "WHERE company_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (company_id, max(1, min(limit, 200))),
            ).fetchall()
        return {"items": [dict(row) for row in rows]}
