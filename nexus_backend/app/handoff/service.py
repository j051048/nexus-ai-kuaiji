"""DCP05：统一最小接手契约（首个适配器：税务底稿待复核）。

岗位（role）回答“该谁办”，指派（assignee）回答“谁在办”。两者分开记录：
- 没有合格候选人时保持 pending，界面显示「待指派」，不默认第一个管理员；
- 指派前校验资格，指派不会授予权限；
- 版本冲突返回当前调用者有权知道的版本与指纹；
- 重复指派按 (source,version,role) 幂等，不产生第二条待办。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from uuid import uuid4

from ..accounting.domain import AccountingError, Actor
from ..accounting.service import AccountingService

FIRM_REVIEWERS = {"owner", "admin", "reviewer"}
ROLE_NEXT_ACTION = {
    "reviewer": "复核税务底稿并出具意见",
    "delivery": "确认月度包并交付客户",
    "ops": "排查并重跑失败的批量项",
}


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _digest(*parts):
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _json(row):
    return {
        "id": row["id"],
        "source": {
            "kind": row["source_kind"],
            "id": row["source_id"],
            "version": row["source_version"],
            "fingerprint": row["source_fingerprint"],
        },
        "scope": {"book_id": row["book_id"]},
        "role": row["role"],
        "next_action": row["next_action"],
        "assignee_id": row["assignee_id"],
        "assignee": None,
        "status": row["status"],
        "due_on": row["due_on"],
        "reason": row["reason"],
        "assigned": row["assignee_id"] is not None,
        "label": "待指派" if row["assignee_id"] is None else "已指派",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class HandoffService:
    def __init__(self, db):
        self.db = db
        self.access = AccountingService(db).access

    # ---- 适配器：把来源领域事实投影成统一契约，只读、不复制金额 ----

    def _tax_pending(self, conn, actor):
        rows = conn.execute(
            "SELECT w.id,w.version,w.source_digest,w.book_id,w.period,w.created_by,w.status,"
            "b.name AS book_name,c.name AS company_name "
            "FROM tax_workpapers w JOIN books b ON b.id=w.book_id "
            "JOIN companies c ON c.id=w.company_id "
            "WHERE w.status='draft' AND b.firm_id=? ORDER BY w.period DESC,w.id",
            (actor.firm_id,),
        ).fetchall()
        items = []
        for row in rows:
            try:
                self.access.book(conn, actor, row["book_id"], "read")
            except AccountingError:
                continue
            items.append(
                {
                    "source": {
                        "kind": "tax_workpaper",
                        "id": row["id"],
                        "version": row["version"],
                        "fingerprint": row["source_digest"],
                    },
                    "scope": {
                        "book_id": row["book_id"],
                        "period": row["period"],
                        "company_name": row["company_name"],
                        "book_name": row["book_name"],
                    },
                    "role": "reviewer",
                    "next_action": ROLE_NEXT_ACTION["reviewer"],
                    "maker_id": row["created_by"],
                }
            )
        return items

    def _assignment(self, conn, kind, source_id, version, role):
        return conn.execute(
            "SELECT * FROM handoff_tasks WHERE source_kind=? AND source_id=? "
            "AND source_version=? AND role=? AND superseded_at IS NULL "
            "ORDER BY created_at DESC,id DESC LIMIT 1",
            (kind, source_id, version, role),
        ).fetchone()

    def queue(self, actor):
        with self.db.transaction(actor=actor) as conn:
            items = []
            for item in self._tax_pending(conn, actor):
                row = self._assignment(
                    conn,
                    item["source"]["kind"],
                    item["source"]["id"],
                    item["source"]["version"],
                    item["role"],
                )
                item = {**item, "assignee_id": row["assignee_id"] if row else None}
                item["status"] = "assigned" if item["assignee_id"] else "pending"
                item["label"] = "待指派" if not item["assignee_id"] else "已指派"
                item["due_on"] = row["due_on"] if row else None
                items.append(item)
            items.sort(key=lambda value: (value["status"] != "pending", value["scope"]["period"]))
            return {
                "items": items,
                "total": len(items),
                "pending": sum(1 for value in items if value["status"] == "pending"),
            }

    def candidates(self, actor, book_id, role):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            if role != "reviewer":
                raise AccountingError("当前只支持复核岗位候选", 422)
            rows = conn.execute(
                "SELECT u.id,u.name,m.role AS firm_role FROM users u "
                "JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
                "WHERE u.active=1 ORDER BY u.name,u.id",
                (actor.firm_id,),
            ).fetchall()
            result = []
            for row in rows:
                if row["firm_role"] not in FIRM_REVIEWERS:
                    continue
                grant = conn.execute(
                    "SELECT role FROM book_access WHERE book_id=? AND user_id=? AND firm_id=?",
                    (book_id, row["id"], actor.firm_id),
                ).fetchone()
                if not grant or grant["role"] != "reviewer":
                    continue
                result.append(
                    {"user_id": row["id"], "name": row["name"], "firm_role": row["firm_role"]}
                )
            return {"items": result, "total": len(result)}

    def assign(self, actor, source_id, expected_version, assignee_id, due_on, reason):
        reason = (reason or "").strip()
        if not 3 <= len(reason) <= 1000:
            raise AccountingError("指派原因需 3-1000 字", 422)
        if due_on is not None:
            try:
                if date.fromisoformat(due_on).isoformat() != due_on:
                    raise ValueError()
            except (TypeError, ValueError):
                raise AccountingError("期限须为有效的 YYYY-MM-DD 日期", 422) from None
        with self.db.transaction(write=True, actor=actor) as conn:
            workpaper = conn.execute(
                "SELECT * FROM tax_workpapers WHERE id=?", (source_id,)
            ).fetchone()
            if not workpaper:
                raise AccountingError("税务底稿不存在或无权访问", 404)
            self.access.book(conn, actor, workpaper["book_id"], "read")
            self.access.require_manager(conn, actor)
            if int(expected_version) != int(workpaper["version"]):
                raise AccountingError(
                    json.dumps(
                        {
                            "message": "税务底稿版本已变化，请刷新后重新指派",
                            "current_version": workpaper["version"],
                            "current_fingerprint": workpaper["source_digest"],
                            "refresh": "重新读取税务底稿",
                        },
                        ensure_ascii=False,
                    ),
                    409,
                )
            if workpaper["status"] != "draft":
                raise AccountingError("该底稿已复核，不需要再指派", 409)
            if assignee_id == workpaper["created_by"]:
                raise AccountingError("制单人不能复核自己的底稿，请指派其他复核人", 409)
            target = Actor(assignee_id, actor.firm_id)
            firm_role = self.access.firm_role(conn, target)
            if firm_role not in FIRM_REVIEWERS:
                raise AccountingError("被指派人不是事务所复核人员，指派不会授予权限", 403)
            try:
                self.access.book(conn, target, workpaper["book_id"], "review")
            except AccountingError:
                raise AccountingError(
                    "被指派人没有当前账套复核权限，指派不会授予权限", 403
                ) from None

            current = self._assignment(
                conn, "tax_workpaper", source_id, workpaper["version"], "reviewer"
            )
            if current and current["assignee_id"] == assignee_id:
                return _json(current)

            stamp = _stamp()
            conn.execute(
                "UPDATE handoff_tasks SET superseded_at=?,updated_at=? "
                "WHERE source_kind='tax_workpaper' AND source_id=? AND source_version=? "
                "AND role='reviewer' AND superseded_at IS NULL",
                (stamp, stamp, source_id, workpaper["version"]),
            )
            task_id = uuid4().hex
            conn.execute(
                "INSERT INTO handoff_tasks(id,firm_id,source_kind,source_id,source_version,"
                "source_fingerprint,book_id,role,assignee_id,due_on,next_action,status,reason,"
                "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    actor.firm_id,
                    "tax_workpaper",
                    source_id,
                    workpaper["version"],
                    workpaper["source_digest"],
                    workpaper["book_id"],
                    "reviewer",
                    assignee_id,
                    due_on,
                    ROLE_NEXT_ACTION["reviewer"],
                    "assigned",
                    reason,
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,'handoff.assign',?,?,?)",
                (
                    actor.firm_id,
                    actor.user_id,
                    task_id,
                    json.dumps(
                        {
                            "source_kind": "tax_workpaper",
                            "source_id": source_id,
                            "version": workpaper["version"],
                            "assignee_id": assignee_id,
                            "reason": reason,
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                ),
            )
            row = conn.execute("SELECT * FROM handoff_tasks WHERE id=?", (task_id,)).fetchone()
            return _json(row)
