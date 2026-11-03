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


SOURCE_ROLES = {
    "tax_workpaper": "reviewer",
    "batch_item": "ops",
    "notification_investigation": "ops",
    "delivery_package": "delivery",
}


class HandoffService:
    def __init__(self, db):
        self.db = db
        self.access = AccountingService(db).access

    # ---- 适配器：把来源领域事实投影成统一契约，只读、不复制金额 ----

    def _manager(self, conn, actor):
        return self.access.firm_role(conn, actor) in {"owner", "admin"}

    def _batch_pending(self, conn, actor):
        if not self._manager(conn, actor):
            return []
        rows = conn.execute(
            "SELECT i.id,i.batch_id,i.company_id,i.company_name,i.attempt,i.error_code,"
            "i.error_detail,i.updated_at,i.result_json,b.request_json "
            "FROM batch_operation_items i JOIN batch_operations b ON b.id=i.batch_id "
            "WHERE i.firm_id=? AND i.status='failed' AND b.firm_id=? "
            "ORDER BY i.updated_at DESC,i.id",
            (actor.firm_id, actor.firm_id),
        ).fetchall()
        items = []
        for row in rows:
            request = json.loads(row["request_json"] or "{}")
            result = json.loads(row["result_json"] or "{}")
            period = request.get("period")
            book_id = result.get("book_id")
            if not book_id:
                # The guard binds batch handoffs to the exact book written into
                # result_json; a guessed book would be an invalid projection.
                continue
            try:
                book = self.access.book(conn, actor, book_id, "read")
            except AccountingError:
                continue
            items.append(
                {
                    "source": {
                        "kind": "batch_item",
                        "id": row["id"],
                        "version": max(1, int(row["attempt"])),
                        "fingerprint": _digest(
                            "batch_item", row["id"], row["batch_id"], row["attempt"],
                            row["error_code"], row["updated_at"],
                        ),
                    },
                    "scope": {
                        "book_id": book_id,
                        "period": period,
                        "company_id": row["company_id"],
                        "company_name": row["company_name"],
                        "book_name": book["name"],
                        "batch_id": row["batch_id"],
                    },
                    "role": "ops",
                    "next_action": ROLE_NEXT_ACTION["ops"],
                    "reason": row["error_detail"] or row["error_code"],
                }
            )
        return items

    def _notification_pending(self, conn, actor):
        if not self._manager(conn, actor):
            return []
        rows = conn.execute(
            "SELECT n.id,n.book_id,n.period,n.package_id,n.package_version,n.manifest_sha256,"
            "n.attempts,n.state,n.updated_at,b.name AS book_name,b.company_id,c.name AS company_name "
            "FROM delivery_notifications n JOIN books b ON b.id=n.book_id "
            "JOIN companies c ON c.id=b.company_id "
            "WHERE n.firm_id=? AND n.state='unknown' ORDER BY n.updated_at DESC,n.id",
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
                        "kind": "notification_investigation",
                        "id": row["id"],
                        "version": int(row["attempts"]) + 1,
                        "fingerprint": _digest(
                            "notification_investigation", row["id"], row["package_id"],
                            row["package_version"], row["manifest_sha256"], row["attempts"],
                            row["updated_at"],
                        ),
                    },
                    "scope": {
                        "book_id": row["book_id"],
                        "period": row["period"],
                        "company_id": row["company_id"],
                        "company_name": row["company_name"],
                        "book_name": row["book_name"],
                        "package_id": row["package_id"],
                        "package_version": row["package_version"],
                    },
                    "role": "ops",
                    "next_action": "核对未知发送结果并登记人工结论，不自动重发",
                    "reason": "通道结果未知，需人工核查",
                }
            )
        return items

    def _delivery_pending(self, conn, actor):
        manager = self._manager(conn, actor)
        rows = conn.execute(
            "SELECT p.id,p.version,p.book_id,p.period,p.manifest_sha256,p.created_at,"
            "b.name AS book_name,b.company_id,c.name AS company_name "
            "FROM monthly_delivery_packages p JOIN books b ON b.id=p.book_id "
            "JOIN companies c ON c.id=b.company_id "
            "WHERE p.firm_id=? AND NOT EXISTS(SELECT 1 FROM monthly_delivery_acknowledgements a "
            "WHERE a.package_id=p.id) AND p.version=(SELECT MAX(p2.version) FROM "
            "monthly_delivery_packages p2 WHERE p2.book_id=p.book_id AND p2.period=p.period) "
            "AND (? OR EXISTS(SELECT 1 FROM handoff_tasks h WHERE h.firm_id=p.firm_id "
            "AND h.source_kind='delivery_package' AND h.source_id=p.id "
            "AND h.assignee_id=? AND h.superseded_at IS NULL)) "
            "ORDER BY p.created_at DESC,p.id",
            (actor.firm_id, manager, actor.user_id),
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
                        "kind": "delivery_package",
                        "id": row["id"],
                        "version": int(row["version"]),
                        "fingerprint": row["manifest_sha256"],
                    },
                    "scope": {
                        "book_id": row["book_id"],
                        "period": row["period"],
                        "company_id": row["company_id"],
                        "company_name": row["company_name"],
                        "book_name": row["book_name"],
                        "package_version": row["version"],
                    },
                    "role": "delivery",
                    "next_action": "核对客户签收阻断并安排交付",
                    "reason": "客户尚未签收该版本的月度包",
                }
            )
        return items

    def _tax_pending(self, conn, actor):
        rows = conn.execute(
            "SELECT w.id,w.version,w.source_digest,w.book_id,w.period,w.created_by,w.status,"
            "b.name AS book_name,c.name AS company_name "
            "FROM tax_workpapers w JOIN books b ON b.id=w.book_id "
            "JOIN companies c ON c.id=w.company_id "
            "WHERE w.status='draft' AND b.firm_id=? "
            "AND w.version=(SELECT MAX(w2.version) FROM tax_workpapers w2 "
            "WHERE w2.book_id=w.book_id AND w2.period=w.period) "
            "ORDER BY w.period DESC,w.id",
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
            pending = (
                self._tax_pending(conn, actor)
                + self._batch_pending(conn, actor)
                + self._notification_pending(conn, actor)
                + self._delivery_pending(conn, actor)
            )
            for item in pending:
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
            items.sort(
                key=lambda value: (
                    value["status"] != "pending",
                    value["scope"].get("period") or "",
                    value["source"]["id"],
                )
            )
            return {
                "items": items,
                "total": len(items),
                "pending": sum(1 for value in items if value["status"] == "pending"),
            }

    def candidates(self, actor, book_id, role):
        with self.db.transaction(actor=actor) as conn:
            self.access.book(conn, actor, book_id, "read")
            if role not in {"reviewer", "ops", "delivery"}:
                raise AccountingError("未知的接手岗位", 422)
            rows = conn.execute(
                "SELECT u.id,u.name,m.role AS firm_role FROM users u "
                "JOIN memberships m ON m.user_id=u.id AND m.firm_id=? "
                "WHERE u.active=1 ORDER BY u.name,u.id",
                (actor.firm_id,),
            ).fetchall()
            result = []
            for row in rows:
                if role == "ops" and row["firm_role"] not in {"owner", "admin"}:
                    continue
                if role == "delivery" and row["firm_role"] not in FIRM_REVIEWERS:
                    continue
                if role == "reviewer" and row["firm_role"] not in FIRM_REVIEWERS:
                    continue
                grant = conn.execute(
                    "SELECT role FROM book_access WHERE book_id=? AND user_id=? AND firm_id=?",
                    (book_id, row["id"], actor.firm_id),
                ).fetchone()
                if role == "reviewer" and (not grant or grant["role"] != "reviewer"):
                    continue
                if role != "reviewer" and not grant:
                    continue
                result.append(
                    {"user_id": row["id"], "name": row["name"], "firm_role": row["firm_role"]}
                )
            return {"items": result, "total": len(result)}

    def assign(
        self, actor, source_id, expected_version, assignee_id, due_on, reason,
        kind="tax_workpaper",
    ):
        reason = (reason or "").strip()
        if kind not in SOURCE_ROLES:
            raise AccountingError("未知的接手来源类型", 422)
        if not 3 <= len(reason) <= 1000:
            raise AccountingError("指派原因需 3-1000 字", 422)
        if due_on is not None:
            try:
                if date.fromisoformat(due_on).isoformat() != due_on:
                    raise ValueError()
            except (TypeError, ValueError):
                raise AccountingError("期限须为有效的 YYYY-MM-DD 日期", 422) from None
        with self.db.transaction(write=True, actor=actor) as conn:
            if kind == "tax_workpaper":
                workpaper = conn.execute(
                    "SELECT * FROM tax_workpapers WHERE id=?", (source_id,)
                ).fetchone()
                if not workpaper:
                    raise AccountingError("税务底稿不存在或无权访问", 404)
                self.access.book(conn, actor, workpaper["book_id"], "read")
                self.access.require_manager(conn, actor)
                version = workpaper["version"]
                if int(expected_version) != int(version):
                    raise AccountingError(
                        json.dumps(
                            {
                                "message": "税务底稿版本已变化，请刷新后重新指派",
                                "current_version": version,
                                "current_fingerprint": workpaper["source_digest"],
                                "refresh": "重新读取税务底稿",
                            },
                            ensure_ascii=False,
                        ),
                        409,
                    )
                if workpaper["status"] != "draft":
                    raise AccountingError("该底稿已复核，不需要再指派", 409)
                latest = conn.execute(
                    "SELECT MAX(version) FROM tax_workpapers WHERE book_id=? AND period=?",
                    (workpaper["book_id"], workpaper["period"]),
                ).fetchone()[0]
                if version != latest:
                    raise AccountingError(
                        "税务底稿已有更新版本，旧版本不能继续指派；请刷新后核对最新版本", 409
                    )
                if assignee_id == workpaper["created_by"]:
                    raise AccountingError("制单人不能复核自己的底稿，请指派其他复核人", 409)
                role = "reviewer"
                book_id = workpaper["book_id"]
                fingerprint = workpaper["source_digest"]
                target_book = book_id
                target_role = "review"
            else:
                if kind not in {
                    "batch_item",
                    "notification_investigation",
                    "delivery_package",
                }:
                    raise AccountingError("不支持的人工接手来源", 404)
                pending = {
                    "batch_item": self._batch_pending,
                    "notification_investigation": self._notification_pending,
                    "delivery_package": self._delivery_pending,
                }[kind](conn, actor)
                item = next(
                    (entry for entry in pending if entry["source"]["id"] == source_id), None
                )
                if item is None:
                    raise AccountingError("来源不存在、已处理或无权访问", 404)
                self.access.require_manager(conn, actor)
                version = item["source"]["version"]
                if int(expected_version) != int(version):
                    raise AccountingError(
                        json.dumps(
                            {
                                "message": "来源版本已变化，请刷新后重新指派",
                                "current_version": version,
                                "current_fingerprint": item["source"]["fingerprint"],
                                "refresh": "重新读取来源事实",
                            },
                            ensure_ascii=False,
                        ),
                        409,
                    )
                role = item["role"]
                book_id = item["scope"]["book_id"]
                fingerprint = item["source"]["fingerprint"]
                target_book = book_id
                target_role = "read"

            target = Actor(assignee_id, actor.firm_id)
            firm_role = self.access.firm_role(conn, target)
            if role == "ops":
                allowed = firm_role in {"owner", "admin"}
                denied = "被指派人不是事务所负责人，指派不会授予权限"
            else:
                allowed = firm_role in FIRM_REVIEWERS
                denied = "被指派人不是事务所复核人员，指派不会授予权限"
            if not allowed:
                raise AccountingError(denied, 403)
            try:
                self.access.book(conn, target, target_book, target_role)
            except AccountingError:
                raise AccountingError(
                    "被指派人没有当前账套权限，指派不会授予权限", 403
                ) from None

            current = self._assignment(conn, kind, source_id, version, role)
            if current and current["assignee_id"] == assignee_id:
                return _json(current)

            stamp = _stamp()
            conn.execute(
                "UPDATE handoff_tasks SET superseded_at=?,updated_at=? "
                "WHERE source_kind=? AND source_id=? AND source_version=? "
                "AND role=? AND superseded_at IS NULL",
                (stamp, stamp, kind, source_id, version, role),
            )
            task_id = uuid4().hex
            conn.execute(
                "INSERT INTO handoff_tasks(id,firm_id,source_kind,source_id,source_version,"
                "source_fingerprint,book_id,role,assignee_id,due_on,next_action,status,reason,"
                "created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    actor.firm_id,
                    kind,
                    source_id,
                    version,
                    fingerprint,
                    book_id,
                    role,
                    assignee_id,
                    due_on,
                    ROLE_NEXT_ACTION[role],
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
                            "source_kind": kind,
                            "source_id": source_id,
                            "version": version,
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
