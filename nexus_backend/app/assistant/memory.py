"""PLT08-D：三类受控记忆的硬约束。

- 个人偏好：归属个人，只影响排序与展示，可删除，永不参与金额与结论。
- 企业规则：助手只能提议；须有权者提交、另一人批准；带生效期与版本；
  按企业隔离；撤销或过期后立即不可见；引用时在「依据」写明规则标识与版本。
- 当前事实：永不记忆，每次调查都重新读取来源（本模块不存任何账务事实）。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError

PREFERENCE_KEYS = ("queue_order", "default_period", "panel_density")
PREFERENCE_VALUES = {
    "queue_order": {"due_first", "created_first", "owner_first"},
    "default_period": {"current", "previous", "latest_closed"},
    "panel_density": {"compact", "comfortable"},
}
RULE_PAYLOAD_KEYS = {"priority", "tags", "match", "note"}
RULE_MATCH_KEYS = {"keyword", "process_kind", "department", "account_code"}
FORBIDDEN_TOKENS = ("amount", "cents", "money", "price", "threshold", "total", "rate")
RULE_STATUSES = ("proposed", "approved", "rejected", "retired", "expired")


def _now(now=None):
    return now or datetime.now(timezone.utc).isoformat()


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    return json.loads(value)


def _normalize_key(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,59}", text):
        raise AccountingError("记忆标识只能是小写字母、数字、点、下划线与短横线", 422)
    return text


def _validate_payload(payload):
    """企业规则只承载路由与提示，不允许夹带任何金额或阈值。"""
    if not isinstance(payload, dict):
        raise AccountingError("企业规则内容必须是对象", 422)
    # 先按「像金额的字段」拒绝：这类尝试要给出明确原因，而不是当成未知字段放过。
    for key, value in payload.items():
        _reject_money(key, value)
    unknown = sorted(set(payload) - RULE_PAYLOAD_KEYS)
    if unknown:
        raise AccountingError("企业规则含未知字段：" + "、".join(unknown), 422)
    match = payload.get("match")
    if match is not None:
        if not isinstance(match, dict):
            raise AccountingError("企业规则的匹配条件必须是对象", 422)
        for key, value in match.items():
            _reject_money(key, value)
        extra = sorted(set(match) - RULE_MATCH_KEYS)
        if extra:
            raise AccountingError("企业规则匹配条件含未知字段：" + "、".join(extra), 422)
        for key, value in match.items():
            if not isinstance(value, str):
                raise AccountingError("企业规则匹配条件只能是文本", 422)
    tags = payload.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)
    ):
        raise AccountingError("企业规则标签必须是文本列表", 422)
    priority = payload.get("priority")
    if priority is not None and (type(priority) is not int or priority < 0 or priority > 100):
        raise AccountingError("企业规则优先级必须是 0 到 100 的整数", 422)
    return payload


def _reject_money(key, value):
    text = str(key).lower()
    if any(token in text for token in FORBIDDEN_TOKENS):
        raise AccountingError("企业规则不能包含金额、阈值或费率字段", 422)
    if isinstance(value, dict):
        for inner_key, inner_value in value.items():
            _reject_money(inner_key, inner_value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                _reject_money(key, item)


def list_preferences(actor, company_id, *, db):
    with db.transaction(actor=actor) as conn:
        AccessControl().company(conn, actor, company_id)
        rows = conn.execute(
            "SELECT pref_key,pref_value,updated_at FROM assistant_preferences "
            "WHERE firm_id=? AND company_id=? AND user_id=? ORDER BY pref_key",
            (actor.firm_id, company_id, actor.user_id),
        ).fetchall()
    return [dict(row) for row in rows]


def set_preference(actor, company_id, key, value, *, db, now=None):
    pref_key = _normalize_key(key)
    if pref_key not in PREFERENCE_KEYS:
        raise AccountingError("个人偏好只支持排序与展示项：" + "、".join(PREFERENCE_KEYS), 422)
    if str(value) not in PREFERENCE_VALUES[pref_key]:
        raise AccountingError(
            "个人偏好取值不合法：" + "、".join(sorted(PREFERENCE_VALUES[pref_key])),
            422,
        )
    stamp = _now(now)
    with db.transaction(
        write=True,
        actor=actor,
        company_id=company_id,
        write_scope="company",
    ) as conn:
        AccessControl().company(conn, actor, company_id)
        existing = conn.execute(
            "SELECT id FROM assistant_preferences "
            "WHERE firm_id=? AND company_id=? AND user_id=? AND pref_key=?",
            (actor.firm_id, company_id, actor.user_id, pref_key),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE assistant_preferences SET pref_value=?,updated_at=? WHERE id=?",
                (str(value), stamp, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO assistant_preferences "
                "(id,firm_id,company_id,user_id,pref_key,pref_value,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    actor.firm_id,
                    company_id,
                    actor.user_id,
                    pref_key,
                    str(value),
                    stamp,
                    stamp,
                ),
            )
    return {"key": pref_key, "value": str(value), "updated_at": stamp}


def delete_preference(actor, company_id, key, *, db, now=None):
    pref_key = _normalize_key(key)
    with db.transaction(
        write=True,
        actor=actor,
        company_id=company_id,
        write_scope="company",
    ) as conn:
        AccessControl().company(conn, actor, company_id)
        row = conn.execute(
            "SELECT id,pref_value FROM assistant_preferences "
            "WHERE firm_id=? AND company_id=? AND user_id=? AND pref_key=?",
            (actor.firm_id, company_id, actor.user_id, pref_key),
        ).fetchone()
        if not row:
            return {"key": pref_key, "deleted": False}
        conn.execute("DELETE FROM assistant_preferences WHERE id=?", (row["id"],))
        _audit(
            conn,
            actor,
            "assistant.preference.delete",
            company_id,
            pref_key,
            {"value": row["pref_value"]},
            now=now,
        )
    return {"key": pref_key, "deleted": True}


def list_rules(actor, company_id, *, db):
    with db.transaction(actor=actor) as conn:
        AccessControl().company(conn, actor, company_id)
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM assistant_rule_proposals "
                "WHERE firm_id=? AND company_id=? ORDER BY rule_key,version DESC",
                (actor.firm_id, company_id),
            ).fetchall()
        ]
    return [_rule(row) for row in rows]


def _rule(row):
    value = dict(row)
    value["payload"] = _json(value.get("payload_json")) or {}
    value.pop("payload_json", None)
    return value


def propose_rule(
    actor,
    company_id,
    rule_key,
    title,
    payload,
    *,
    db,
    effective_from=None,
    expires_at=None,
    now=None,
):
    rule_key = _normalize_key(rule_key)
    clean = _validate_payload(payload or {})
    label = str(title or "").strip()
    if not 2 <= len(label) <= 60:
        raise AccountingError("规则标题需要 2 到 60 个字", 422)
    stamp = _now(now)
    with db.transaction(
        write=True,
        actor=actor,
        company_id=company_id,
        write_scope="company",
    ) as conn:
        AccessControl().company(conn, actor, company_id, manage=True)
        version = _next_version(conn, actor.firm_id, company_id, rule_key)
        proposal_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO assistant_rule_proposals "
            "(id,firm_id,company_id,rule_key,title,payload_json,version,status,"
            "proposed_by,approved_by,effective_from,expires_at,state_version,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                proposal_id,
                actor.firm_id,
                company_id,
                rule_key,
                label,
                json.dumps(clean, ensure_ascii=False, sort_keys=True),
                version,
                "proposed",
                actor.user_id,
                None,
                effective_from,
                expires_at,
                1,
                stamp,
                stamp,
            ),
        )
        _audit(
            conn,
            actor,
            "assistant.rule.propose",
            company_id,
            proposal_id,
            {"rule_key": rule_key, "version": version},
            now=now,
        )
    return {
        "id": proposal_id,
        "rule_key": rule_key,
        "title": label,
        "payload": clean,
        "version": version,
        "status": "proposed",
        "proposed_by": actor.user_id,
        "effective_from": effective_from,
        "expires_at": expires_at,
    }


def _next_version(conn, firm_id, company_id, rule_key):
    row = conn.execute(
        "SELECT COALESCE(MAX(version),0) AS version FROM assistant_rule_proposals "
        "WHERE firm_id=? AND company_id=? AND rule_key=?",
        (firm_id, company_id, rule_key),
    ).fetchone()
    return int(row["version"]) + 1


def approve_rule(actor, proposal_id, *, db, now=None):
    stamp = _now(now)
    with db.transaction(actor=actor) as conn:
        head = _proposal_row(conn, actor, proposal_id)
        company_id = head["company_id"]
    with db.transaction(
        write=True, actor=actor, company_id=company_id, write_scope="company"
    ) as conn:
        row = _proposal_row(conn, actor, proposal_id)
        AccessControl().company(conn, actor, row["company_id"], manage=True)
        if row["status"] != "proposed":
            raise AccountingError("只有待批准的提议可以批准", 409)
        if row["proposed_by"] == actor.user_id:
            raise AccountingError("不能批准自己提议的企业规则", 403)
        conn.execute(
            "UPDATE assistant_rule_proposals SET status='approved',approved_by=?,"
            "state_version=state_version+1,updated_at=? WHERE id=?",
            (actor.user_id, stamp, proposal_id),
        )
        _audit(
            conn,
            actor,
            "assistant.rule.approve",
            row["company_id"],
            proposal_id,
            {"rule_key": row["rule_key"], "version": row["version"]},
            now=now,
        )
    return {"id": proposal_id, "status": "approved", "approved_by": actor.user_id}


def retire_rule(actor, proposal_id, reason, *, db, now=None):
    text = str(reason or "").strip()
    if len(text) < 2:
        raise AccountingError("撤销企业规则必须填写原因", 422)
    stamp = _now(now)
    with db.transaction(actor=actor) as conn:
        head = _proposal_row(conn, actor, proposal_id)
        company_id = head["company_id"]
    with db.transaction(
        write=True, actor=actor, company_id=company_id, write_scope="company"
    ) as conn:
        row = _proposal_row(conn, actor, proposal_id)
        AccessControl().company(conn, actor, row["company_id"], manage=True)
        if row["status"] not in ("approved", "proposed"):
            raise AccountingError("该规则已经失效，无需撤销", 409)
        conn.execute(
            "UPDATE assistant_rule_proposals SET status='retired',state_version=state_version+1,"
            "updated_at=? WHERE id=?",
            (stamp, proposal_id),
        )
        _audit(
            conn,
            actor,
            "assistant.rule.retire",
            row["company_id"],
            proposal_id,
            {"reason": text},
            now=now,
        )
    return {"id": proposal_id, "status": "retired"}


def _proposal_row(conn, actor, proposal_id):
    row = conn.execute(
        "SELECT * FROM assistant_rule_proposals WHERE id=? AND firm_id=?",
        (proposal_id, actor.firm_id),
    ).fetchone()
    if not row:
        raise AccountingError("企业规则提议不存在或无权访问", 404)
    return row


def effective_rules(conn, actor, company_id, *, now=None):
    """当前对本企业生效的规则；过期、撤销、未批准、跨企业的一律不返回。"""
    stamp = _now(now)
    AccessControl().company(conn, actor, company_id)
    rows = conn.execute(
        "SELECT id,rule_key,title,payload_json,version,effective_from,expires_at "
        "FROM assistant_rule_proposals WHERE firm_id=? AND company_id=? AND status='approved' "
        "AND (effective_from IS NULL OR effective_from<=?) "
        "AND (expires_at IS NULL OR expires_at>?) "
        "ORDER BY rule_key,version DESC",
        (actor.firm_id, company_id, stamp, stamp),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "rule_key": row["rule_key"],
            "title": row["title"],
            "payload": _json(row["payload_json"]) or {},
            "version": row["version"],
            "effective_from": row["effective_from"],
            "expires_at": row["expires_at"],
        }
        for row in rows
    ]


def suggest_rule_from_correction(correction, *, company_id=None):
    """一次人工更正只生成「是否提议为规则」的建议，绝不自动生效。"""
    text = str((correction or {}).get("note") or "").strip()
    if not text:
        return {"applied": False, "suggestion": None}
    return {
        "applied": False,
        "requires_human_proposal": True,
        "company_id": company_id,
        "suggestion": {
            "rule_key": "correction_suggestion",
            "title": text[:60],
            "payload": {"note": text[:200], "priority": 10},
            "reason": "一次人工更正不等于企业规则，需要有权者提交并由另一人批准",
        },
    }


def _audit(conn, actor, action, company_id, object_id, details, *, now=None):
    conn.execute(
        "INSERT INTO audit_events "
        "(firm_id,book_id,user_id,action,object_id,details,created_at,company_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            actor.firm_id,
            None,
            actor.user_id,
            action,
            object_id,
            json.dumps(details, ensure_ascii=False, sort_keys=True),
            _now(now),
            company_id,
        ),
    )
