"""Persisted assignment overlay over the existing workspace read model."""

from datetime import date
import json
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now

BLOCKED_STATES = {
    "awaiting_info",
    "response_submitted",
    "in_review",
    "blocked",
    "needs_attention",
}
FAILED_STATES = {"failed", "interrupted", "stale", "retry_wait"}


class FirmTasksService:
    def __init__(self, db, accounting, workspace=None):
        self.db, self.access = db, accounting.access
        self.accounting = accounting
        self.workspace = workspace

    def _workspace(self):
        if self.workspace is None:
            from ..accounting.service import AccountingService
            from ..accounting.review import ReviewService
            from ..business.service import BusinessService
            from ..cases.service import CaseService
            from ..workspace.service import WorkspaceService

            accounting, business = AccountingService(self.db), BusinessService(self.db)
            self.workspace = WorkspaceService(
                self.db,
                accounting,
                business,
                ReviewService(self.db, accounting),
                CaseService(self.db, business),
            )
        return self.workspace

    @staticmethod
    def _key(kind, ref):
        return f"{kind}:{ref}"

    def _visible_tasks(self, conn, actor, refs):
        if len({(item.kind, item.ref) for item in refs}) != len(refs):
            raise AccountingError("任务不得重复", 422)
        found = {}
        query, params = self._workspace().authorized_task_query(conn, actor)
        for item in refs:
            row = conn.execute(
                query + "SELECT * FROM tasks WHERE kind=? AND ref=?",
                (*params, item.kind, item.ref),
            ).fetchone()
            if not row:
                raise AccountingError(f"任务不存在或无权访问：{item.kind}/{item.ref}", 404)
            value = dict(row)
            event = (
                conn.execute(
                    "SELECT applicant_id FROM business_events WHERE id=? AND firm_id=?",
                    (row["event_id"], actor.firm_id),
                ).fetchone()
                if row["event_id"]
                else None
            )
            value["applicant_id"] = event["applicant_id"] if event else None
            value["recipient_id"] = None
            found[(item.kind, item.ref)] = value
        return found

    def _assignees(self, conn, actor):
        rows = conn.execute(
            "SELECT u.id,u.name,'firm' AS source FROM users u JOIN memberships m "
            "ON m.user_id=u.id WHERE m.firm_id=? AND u.active=1 "
            "UNION SELECT u.id,u.name,'client' AS source FROM users u "
            "JOIN company_memberships cm ON cm.user_id=u.id JOIN firm_clients fc "
            "ON fc.company_id=cm.company_id AND fc.firm_id=? AND fc.status='active' "
            "WHERE u.active=1 ORDER BY name,id",
            (actor.firm_id, actor.firm_id),
        ).fetchall()
        return [{"id": row["id"], "name": row["name"], "source": row["source"]} for row in rows]

    def _assignee_valid(self, conn, firm_id, user_id):
        return bool(
            conn.execute(
                "SELECT 1 FROM users u WHERE u.id=? AND u.active=1 AND ("
                "EXISTS(SELECT 1 FROM memberships m WHERE m.user_id=u.id AND m.firm_id=?) "
                "OR EXISTS(SELECT 1 FROM company_memberships cm JOIN firm_clients fc "
                "ON fc.company_id=cm.company_id WHERE cm.user_id=u.id AND fc.firm_id=? "
                "AND fc.status='active'))",
                (user_id, firm_id, firm_id),
            ).fetchone()
        )

    def inbox(self, actor, bucket="all", limit=100, offset=0):
        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or type(offset) is not int
            or offset < 0
            or bucket not in {"all", "today", "overdue", "blocked", "review", "failed"}
        ):
            raise AccountingError("待办筛选范围无效", 422)
        today = date.today().isoformat()
        with self.db.transaction(actor=actor, jit=False) as conn:
            query, params = self._workspace().authorized_task_query(conn, actor)
            manager = self.access.firm_role(conn, actor) in {"owner", "admin"}
            query += """ , assigned AS (
              SELECT t.*,c.name AS company_name,a.assignee_id,u.name AS assignee_name,
                COALESCE(a.due_on,t.due_on,'') AS assigned_due_on,
                COALESCE(a.version,0) AS assignment_version,COALESCE(a.note,'') AS assignment_note
              FROM tasks t JOIN companies c ON c.id=t.company_id
              LEFT JOIN firm_task_assignments a ON a.firm_id=? AND a.kind=t.kind AND a.ref=t.ref
              LEFT JOIN users u ON u.id=a.assignee_id
            ), categorized AS (SELECT assigned.*,CASE
              WHEN kind IN ('ocr','proposal','alert') AND state IN ('failed','interrupted','stale','retry_wait') THEN 'failed'
              WHEN state IN ('awaiting_info','response_submitted','in_review','blocked','needs_attention') OR kind IN ('case','alert') THEN 'blocked'
              WHEN assigned_due_on<>'' AND assigned_due_on<? THEN 'overdue'
              WHEN assigned_due_on=? THEN 'today'
              WHEN kind IN ('review','ocr','proposal') THEN 'review' ELSE 'today' END AS category FROM assigned)
            """
            params = [*params, actor.firm_id, today, today]
            counts = {key: 0 for key in ("today", "overdue", "blocked", "review", "failed")}
            for row in conn.execute(
                query + "SELECT category,COUNT(*) AS total FROM categorized GROUP BY category",
                params,
            ):
                counts[row["category"]] = int(row["total"])
            total = sum(counts.values()) if bucket == "all" else counts[bucket]
            where = "" if bucket == "all" else " WHERE category=?"
            rows = conn.execute(
                query
                + "SELECT * FROM categorized"
                + where
                + " ORDER BY priority,NULLIF(assigned_due_on,''),updated_at DESC,kind,ref LIMIT ? OFFSET ?",
                [*params, *([] if bucket == "all" else [bucket]), limit, offset],
            ).fetchall()
            assignees = self._assignees(conn, actor) if manager else []
            items = []
            for row in rows:
                item = {
                    key: row[key]
                    for key in (
                        "kind",
                        "ref",
                        "company_id",
                        "company_name",
                        "book_id",
                        "event_id",
                        "draft_id",
                        "batch_id",
                        "title",
                        "state",
                        "responsible",
                        "action_label",
                        "assignee_id",
                        "assignee_name",
                        "assignment_version",
                        "assignment_note",
                        "updated_at",
                    )
                }
                item["due_on"] = row["assigned_due_on"]
                item["bucket"] = row["category"]
                item["task_bucket"] = row["bucket"]
                item["permissions"] = {
                    "assign": manager,
                    "remind": manager,
                    "claim": row["bucket"] == "action" and not row["assignee_id"],
                }
                items.append(item)
        return {
            "scope": {"firm_id": actor.firm_id, "user_id": actor.user_id, "bucket": bucket},
            "counts": counts,
            "items": items,
            "assignees": assignees,
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
            "note": "按当前授权读取实际待办；指派不授予权限，进入原事项后仍须核对最新版本。",
        }

    def assign(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            if not self._assignee_valid(conn, actor.firm_id, request.assignee_id):
                raise AccountingError("指派对象不是本所当前有效成员", 409)
            tasks = self._visible_tasks(conn, actor, request.tasks)
            stamp = now()
            for key, row in tasks.items():
                current = conn.execute(
                    "SELECT version FROM firm_task_assignments WHERE firm_id=? AND kind=? AND ref=?",
                    (actor.firm_id, *key),
                ).fetchone()
                conn.execute(
                    "INSERT INTO firm_task_assignments(firm_id,kind,ref,assignee_id,due_on,"
                    "sla_state,version,note,updated_by,updated_at) VALUES(?,?,?,?,?,?,1,?,?,?) "
                    "ON CONFLICT(firm_id,kind,ref) DO UPDATE SET assignee_id=excluded.assignee_id,"
                    "due_on=excluded.due_on,sla_state=excluded.sla_state,note=excluded.note,"
                    "version=firm_task_assignments.version+1,updated_by=excluded.updated_by,"
                    "updated_at=excluded.updated_at",
                    (
                        actor.firm_id,
                        key[0],
                        key[1],
                        request.assignee_id,
                        request.due_on,
                        "normal",
                        request.note,
                        actor.user_id,
                        stamp,
                    ),
                )
            self._audit(
                conn,
                actor,
                "firm.task.assign",
                json.dumps(
                    {
                        "tasks": [item.model_dump() for item in request.tasks],
                        "assignee_id": request.assignee_id,
                        "due_on": request.due_on,
                        "note": request.note,
                    },
                    ensure_ascii=False,
                ),
                stamp,
            )
            return {"updated": len(tasks)}

    def claim(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            current_task = self._visible_tasks(conn, actor, [request])[(request.kind, request.ref)]
            if current_task["bucket"] != "action":
                raise AccountingError("当前事项正在等待其他岗位办理", 409)
            assigned = conn.execute(
                "SELECT assignee_id FROM firm_task_assignments WHERE firm_id=? AND kind=? AND ref=?",
                (actor.firm_id, request.kind, request.ref),
            ).fetchone()
            if assigned and assigned["assignee_id"]:
                if assigned["assignee_id"] != actor.user_id:
                    raise AccountingError("事项已由其他成员领取，请刷新或联系负责人重新分派", 409)
                return {"kind": request.kind, "ref": request.ref, "assignee_id": actor.user_id}
            stamp = now()
            conn.execute(
                "INSERT INTO firm_task_assignments(firm_id,kind,ref,assignee_id,due_on,sla_state,"
                "version,note,updated_by,updated_at) VALUES(?,?,?,?,NULL,'normal',1,?,?,?) "
                "ON CONFLICT(firm_id,kind,ref) DO UPDATE SET assignee_id=excluded.assignee_id,"
                "version=firm_task_assignments.version+1,note=excluded.note,"
                "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (
                    actor.firm_id,
                    request.kind,
                    request.ref,
                    actor.user_id,
                    request.note,
                    actor.user_id,
                    stamp,
                ),
            )
            return {"kind": request.kind, "ref": request.ref, "assignee_id": actor.user_id}

    def remind(self, actor, request):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            tasks = self._visible_tasks(conn, actor, request.tasks)
            stamp = now()
            created = 0
            for (kind, ref), row in tasks.items():
                assignment = conn.execute(
                    "SELECT assignee_id FROM firm_task_assignments WHERE firm_id=? AND kind=? AND ref=?",
                    (actor.firm_id, kind, ref),
                ).fetchone()
                recipient = (
                    assignment["assignee_id"]
                    if assignment and assignment["assignee_id"]
                    else row["recipient_id"] or row["applicant_id"]
                )
                if not recipient:
                    raise AccountingError("请先明确此事项的接收人，再发送催办", 409)
                conn.execute(
                    "INSERT INTO client_messages(id,firm_id,company_id,recipient_user_id,kind,"
                    "subject,body,related_kind,related_ref,created_by,created_at) "
                    "VALUES(?,?,?,?,'reminder',?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        actor.firm_id,
                        row["company_id"],
                        recipient,
                        "待办催办：" + row["title"],
                        request.message,
                        kind,
                        ref,
                        actor.user_id,
                        stamp,
                    ),
                )
                created += 1
            self._audit(
                conn,
                actor,
                "firm.task.remind",
                json.dumps(
                    {
                        "tasks": [item.model_dump() for item in request.tasks],
                        "message": request.message,
                    },
                    ensure_ascii=False,
                ),
                stamp,
            )
            return {"created": created}

    def _message_rows(self, conn, actor, limit, unread_only=False, message_id=None):
        self.access.require_user(conn, actor.user_id)
        role = self.access.firm_role(conn, actor)
        manager = role in {"owner", "admin"}
        # Apply firm + active enterprise + recipient together. The parentheses
        # matter: a broadcast must never escape the current firm predicate.
        receipt = "COALESCE(r.read_at,CASE WHEN m.recipient_user_id IS NOT NULL THEN m.read_at END)"
        sql = f"""SELECT m.*, {receipt} AS reader_read_at FROM client_messages m
            JOIN companies c ON c.id=m.company_id AND c.status='active'
            JOIN firm_clients fc ON fc.company_id=m.company_id AND fc.firm_id=m.firm_id AND fc.status='active'
            LEFT JOIN client_message_reads r ON r.message_id=m.id AND r.user_id=?
            WHERE m.firm_id=? AND (
                ?=1 OR EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=m.company_id AND cm.user_id=?)
                OR (?=1 AND EXISTS(SELECT 1 FROM books b JOIN effective_book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id
                    WHERE b.company_id=m.company_id AND b.firm_id=m.firm_id AND a.user_id=?)))
            AND (?=1 OR m.recipient_user_id=? OR (m.recipient_user_id IS NULL AND EXISTS(
                SELECT 1 FROM company_memberships cm WHERE cm.company_id=m.company_id AND cm.user_id=?)))"""
        values = [
            actor.user_id,
            actor.firm_id,
            int(manager),
            actor.user_id,
            int(bool(role)),
            actor.user_id,
            int(manager),
            actor.user_id,
            actor.user_id,
        ]
        if message_id is not None:
            sql += " AND m.id=?"
            values.append(message_id)
        if unread_only:
            sql += f" AND {receipt} IS NULL"
        sql += " ORDER BY m.created_at DESC,m.id DESC LIMIT ?"
        values.append(limit)
        return conn.execute(sql, values).fetchall()

    def messages(self, actor, limit=50, unread_only=False):
        with self.db.transaction(actor=actor) as conn:
            rows = self._message_rows(conn, actor, limit, unread_only)
            return {
                "messages": [
                    {
                        **{
                            key: row[key]
                            for key in (
                                "id",
                                "company_id",
                                "recipient_user_id",
                                "kind",
                                "subject",
                                "body",
                                "related_kind",
                                "related_ref",
                                "created_at",
                            )
                        },
                        "read_at": row["reader_read_at"],
                    }
                    for row in rows
                ],
                "unread": sum(1 for row in rows if not row["reader_read_at"]),
            }

    def mark_message_read(self, actor, message_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            rows = self._message_rows(conn, actor, 1, message_id=message_id)
            if not rows:
                raise AccountingError("消息不存在或无权访问", 404)
            row = rows[0]
            conn.execute(
                "INSERT INTO client_message_reads(message_id,firm_id,company_id,user_id,read_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(message_id,user_id) DO NOTHING",
                (message_id, actor.firm_id, row["company_id"], actor.user_id, now()),
            )
            return {"id": message_id, "read": True}

    def message_target(self, actor, message_id):
        """Reauthorize the original object. A notice is never an access grant."""
        from ..business.service import BusinessService
        from ..service_plans.service import ServicePlansService
        from ..delivery.monthly_package import MonthlyPackageService

        with self.db.transaction(actor=actor) as conn:
            rows = self._message_rows(conn, actor, 1, message_id=message_id)
            if not rows:
                raise AccountingError("消息不存在或无权访问", 404)
            row = rows[0]
            kind, ref = row["related_kind"], row["related_ref"]
            target = {"message_id": message_id, "company_id": row["company_id"]}
            if kind in {"business", "approval", "finance", "handoff"}:
                service = BusinessService(self.db)
                original = service._event(conn, actor, ref)
                # _event applies visibility; do not replace with an unscoped SELECT.
                if not service._visible(conn, actor, original):
                    raise AccountingError("原事项不存在或无权访问", 404)
                target.update(
                    kind="business",
                    event_id=ref,
                    period=json.loads(original["payload"])["occurred_on"][:7],
                )
            elif kind == "material":
                original = ServicePlansService(
                    self.db, self.accounting, BusinessService(self.db)
                )._request(conn, actor, ref)
                target.update(
                    kind="material",
                    material_id=ref,
                    period=original["period"],
                    book_id=original["book_id"],
                )
            elif kind == "monthly_package":
                package = conn.execute(
                    "SELECT book_id FROM monthly_delivery_packages WHERE id=? AND firm_id=?",
                    (ref, actor.firm_id),
                ).fetchone()
                if not package:
                    raise AccountingError("交付包不存在或无权访问", 404)
                service = MonthlyPackageService(self.db)
                book, original = service._package(conn, actor, package["book_id"], ref)
                view = service._view(conn, actor, book, original)
                if not view.get("can_open"):
                    raise AccountingError("当前交付包不可查看", 403)
                target.update(
                    kind="monthly_package",
                    package_id=ref,
                    book_id=book["id"],
                    period=original["period"],
                    version=original["version"],
                    manifest_sha256=original["manifest_sha256"],
                )
            else:
                raise AccountingError("该历史提醒没有可直达的原事项，请联系负责人员", 409)
            if original["company_id"] != row["company_id"]:
                raise AccountingError("原事项不存在或无权访问", 404)
            return target

    @staticmethod
    def _audit(conn, actor, action, details, stamp):
        conn.execute(
            "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (actor.firm_id, actor.user_id, action, "", details, stamp),
        )
