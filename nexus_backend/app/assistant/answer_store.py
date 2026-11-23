"""PLT08-C：回答留痕与「失效即清除」。

每条回答都记录依赖对象与版本。再次读取时服务端重新授权并重算依赖版本：
权限被撤销直接拒访（不泄露原结论）；任一依据版本变化或出现新冲突证据就
立即标记失效、隐藏旧结论，并给出「重新调查」入口。核验失败时不自动重试，
计费状态记为未知而不是零。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from . import paths
from .answer_contract import build_answer

KINDS = ("why_not_delivered", "revenue_mismatch", "number_origin")
STATUSES = ("active", "invalid")
DEPENDENCY_LABELS = {
    "source": "来源原件",
    "voucher": "凭证",
    "close_run": "月结预检",
    "delivery_package": "对外交付包",
    "rule": "企业规则",
}


def _now(now=None):
    return now or datetime.now(timezone.utc).isoformat()


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    return json.loads(value)


def _fingerprint(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _question(payload):
    return {
        "kind": payload["kind"],
        "book_id": payload["book_id"],
        "period": payload["period"],
        "settlement_source_id": payload.get("settlement_source_id") or "",
        "bank_source_id": payload.get("bank_source_id") or "",
        "fee_source_id": payload.get("fee_source_id") or "",
        "account_code": payload.get("account_code") or "",
    }


def run_path(reader, payload, *, narrative=None):
    """按问题类型调固定调查路径；三条路径都只读。"""
    kind = payload.get("kind")
    if kind == "why_not_delivered":
        return paths.answer_why_not_delivered(reader, narrative=narrative)
    if kind == "revenue_mismatch":
        return paths.answer_revenue_mismatch(
            reader,
            payload.get("settlement_source_id"),
            payload.get("bank_source_id"),
            payload.get("fee_source_id") or None,
            narrative=narrative,
        )
    if kind == "number_origin":
        return paths.answer_number_origin(reader, payload.get("account_code"), narrative=narrative)
    raise AccountingError("未知的调查类型", 422)


def _current_version(conn, dependency):
    """重算某个依赖对象的当前版本；返回 None 表示无法核验。"""
    kind = dependency.get("object_type")
    identifier = dependency.get("object_id")
    if not identifier:
        return None
    if kind == "source":
        row = conn.execute(
            "SELECT s.fingerprint,COALESCE((SELECT se.evidence_ref FROM source_evidence se "
            "WHERE se.book_id=s.book_id AND se.source_id=s.id ORDER BY se.id DESC LIMIT 1),"
            "s.evidence_ref) AS latest_evidence FROM sources s WHERE s.id=?",
            (identifier,),
        ).fetchone()
        if not row:
            return None
        return hashlib.sha256(
            f"{row['fingerprint']}|{row['latest_evidence']}".encode("utf-8")
        ).hexdigest()
    if kind == "voucher":
        row = conn.execute(
            "SELECT content_fingerprint FROM vouchers WHERE id=?", (identifier,)
        ).fetchone()
        return row["content_fingerprint"] if row else None
    if kind == "close_run":
        book_id, _, period = str(identifier).partition(":")
        row = conn.execute(
            "SELECT MAX(version) AS version FROM internal_close_runs WHERE book_id=? AND period=?",
            (book_id, period),
        ).fetchone()
        return None if not row or row["version"] is None else str(row["version"])
    if kind == "delivery_package":
        book_id, _, period = str(identifier).partition(":")
        row = conn.execute(
            "SELECT MAX(version) AS version FROM monthly_delivery_packages "
            "WHERE book_id=? AND period=?",
            (book_id, period),
        ).fetchone()
        return None if not row or row["version"] is None else str(row["version"])
    if kind == "rule":
        row = conn.execute(
            "SELECT version,status,effective_from,expires_at FROM assistant_rule_proposals "
            "WHERE id=?",
            (identifier,),
        ).fetchone()
        if not row or row["status"] != "approved":
            return "invalid"
        return str(row["version"])
    return None


class AnswerStore:
    def __init__(self, db, accounting=None):
        self.db = db
        self.access = accounting.access if accounting is not None else AccessControl()

    def reader(self, actor, payload):
        return paths.BookReader(
            self.db,
            actor,
            payload["book_id"],
            payload["period"],
            now=payload.get("now"),
        )

    def preview(self, actor, payload):
        """只跑路径不落库：供评测与红绿测试使用，零持久化副作用。"""
        reader = self.reader(actor, payload)
        return run_path(reader, payload, narrative=payload.get("narrative"))

    def investigate(self, actor, payload, *, now=None):
        question = _question(payload)
        answer = self.preview(actor, payload)
        company_id = answer["company_id"]
        stamp = _now(now)
        with self.db.transaction(
            write=True,
            actor=actor,
            company_id=company_id,
            write_scope="company",
        ) as conn:
            existing = conn.execute(
                "SELECT * FROM assistant_answers WHERE firm_id=? AND book_id=? "
                "AND answer_fingerprint=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                (actor.firm_id, payload["book_id"], answer["answer_fingerprint"]),
            ).fetchone()
            if existing:
                return {
                    "answer": self._payload(existing),
                    "answer_id": existing["id"],
                    "reused": True,
                    "visible": True,
                    "verification": "fresh",
                }
            answer_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO assistant_answers "
                "(id,firm_id,company_id,book_id,period,kind,question_json,sections_json,"
                "dependencies_json,answer_fingerprint,conclusion_state,status,invalid_reason,"
                "cost_state,superseded_by,created_by,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    answer_id,
                    actor.firm_id,
                    company_id,
                    payload["book_id"],
                    payload["period"],
                    payload["kind"],
                    json.dumps(question, ensure_ascii=False, sort_keys=True),
                    json.dumps(answer["sections"], ensure_ascii=False),
                    json.dumps(answer["dependencies"], ensure_ascii=False),
                    answer["answer_fingerprint"],
                    answer["conclusion_state"],
                    "active",
                    "",
                    "not_incurred",
                    None,
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM assistant_answers WHERE id=?", (answer_id,)
            ).fetchone()
            return {
                "answer": self._payload(row),
                "answer_id": answer_id,
                "reused": False,
                "visible": True,
                "verification": "fresh",
            }

    def read(self, actor, answer_id, *, now=None):
        with self.db.transaction(actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM assistant_answers WHERE id=? AND firm_id=?",
                (answer_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("回答不存在或无权访问", 404)
            try:
                self.access.book(conn, actor, row["book_id"], "read")
            except AccountingError as failure:
                # 撤权后连原结论是否存在都不再透露。
                raise AccountingError(
                    "当前账号已无权查看该账套的调查回答", failure.status
                ) from None
            state, reason, changed = self._verify(conn, row, now=now)
        if state == "fresh":
            return {
                "answer": self._payload(row),
                "answer_id": answer_id,
                "visible": True,
                "verification": "fresh",
                "invalid_reason": "",
            }
        self._invalidate(actor, row, reason)
        return {
            "answer": self._hidden(row),
            "answer_id": answer_id,
            "visible": False,
            "verification": "stale" if state == "stale" else "unknown",
            "invalid_reason": reason,
            "reinvestigate": True,
            "changed": changed,
        }

    def _verify(self, conn, row, *, now=None):
        dependencies = _json(row["dependencies_json"]) or []
        if row["conclusion_state"] == "abstained":
            return "fresh", "", []
        changed = []
        for dependency in dependencies:
            try:
                current = _current_version(conn, dependency)
            except Exception:
                return "unknown", "依赖版本未能核验，旧结论暂不展示", []
            if current is None:
                return "unknown", "依赖对象或版本无法核验，旧结论暂不展示", []
            if str(current) != str(dependency.get("version")):
                label = DEPENDENCY_LABELS.get(
                    dependency.get("object_type"), dependency.get("object_type")
                )
                changed.append(f"{label} {dependency.get('object_id')}")
        if changed:
            return "stale", "依据已变化：" + "、".join(changed[:5]), changed
        return "fresh", "", []

    def _invalidate(self, actor, row, reason):
        with self.db.transaction(
            write=True,
            actor=actor,
            company_id=row["company_id"],
            write_scope="company",
        ) as conn:
            conn.execute(
                "UPDATE assistant_answers SET status='invalid',invalid_reason=?,"
                "cost_state=CASE WHEN cost_state='not_incurred' THEN 'unknown' ELSE cost_state END,"
                "updated_at=? WHERE id=? AND status='active'",
                (reason, _now(), row["id"]),
            )

    def _hidden(self, row):
        return {
            "schema": "plt08-answer-v1",
            "kind": row["kind"],
            "book_id": row["book_id"],
            "period": row["period"],
            "conclusion_state": "withheld",
            "sections": [],
            "text": "结论已失效并隐藏，请重新调查。",
            "answer_fingerprint": row["answer_fingerprint"],
            "withheld": True,
        }

    def _payload(self, row):
        return {
            "schema": "plt08-answer-v1",
            "kind": row["kind"],
            "book_id": row["book_id"],
            "company_id": row["company_id"],
            "period": row["period"],
            "conclusion_state": row["conclusion_state"],
            "sections": _json(row["sections_json"]) or [],
            "text": _render(_json(row["sections_json"]) or []),
            "answer_fingerprint": row["answer_fingerprint"],
            "dependencies": _json(row["dependencies_json"]) or [],
            "cost_state": row["cost_state"],
            "model_calls": 0,
            "auto_retry": False,
            "status": row["status"],
        }

    def reinvestigate(self, actor, answer_id, *, now=None):
        with self.db.transaction(actor=actor) as conn:
            row = conn.execute(
                "SELECT * FROM assistant_answers WHERE id=? AND firm_id=?",
                (answer_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("回答不存在或无权访问", 404)
            self.access.book(conn, actor, row["book_id"], "read")
            payload = _json(row["question_json"]) or {}
        payload["kind"] = row["kind"]
        payload["book_id"] = row["book_id"]
        payload["period"] = row["period"]
        fresh = self.investigate(actor, payload, now=now)
        if fresh["answer_id"] != answer_id:
            with self.db.transaction(
                write=True,
                actor=actor,
                company_id=row["company_id"],
                write_scope="company",
            ) as conn:
                conn.execute(
                    "UPDATE assistant_answers SET status='invalid',invalid_reason=?,"
                    "superseded_by=?,updated_at=? WHERE id=?",
                    ("已由重新调查的回答替代", fresh["answer_id"], _now(now), answer_id),
                )
        return fresh

    def list_answers(self, actor, book_id, period="", *, limit=50):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            rows = conn.execute(
                "SELECT * FROM assistant_answers WHERE firm_id=? AND book_id=? "
                "AND (?='' OR period=?) ORDER BY created_at DESC LIMIT ?",
                (actor.firm_id, book_id, period, period, limit),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "period": row["period"],
                    "status": row["status"],
                    "conclusion_state": row["conclusion_state"],
                    "invalid_reason": row["invalid_reason"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]


def _render(sections):
    blocks = []
    for section in sections or []:
        blocks.append(f"【{section.get('label')}】")
        for line in section.get("lines") or []:
            blocks.append(f"- {line}")
    return "\n".join(blocks)


def build_answer_for_payload(reader, payload, *, narrative=None):
    """评测入口：与线上共用同一组装逻辑，但不接线数据库。"""
    return run_path(reader, payload, narrative=narrative)


__all__ = [
    "AnswerStore",
    "KINDS",
    "build_answer_for_payload",
    "build_answer",
    "run_path",
]
