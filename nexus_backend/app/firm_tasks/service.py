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
    def __init__(self, db, accounting):
        self.db, self.access = db, accounting.access

    @staticmethod
    def _key(kind, ref):
        return f"{kind}:{ref}"

    def _visible_tasks(self, conn, actor, refs):
        if len({(item.kind, item.ref) for item in refs}) != len(refs):
            raise AccountingError("任务不得重复", 422)
        found = {}
        for item in refs:
            row = conn.execute(
                "SELECT * FROM workspace_task_projections WHERE firm_id=? AND kind=? AND ref=?",
                (actor.firm_id, item.kind, item.ref),
            ).fetchone()
            if not row:
                raise AccountingError(f"任务不存在或无权访问：{item.kind}/{item.ref}", 404)
            found[(item.kind, item.ref)] = row
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

    def inbox(self, actor, bucket="all", limit=100):
        if not 1 <= limit <= 200:
            raise AccountingError("分页范围无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            rows = conn.execute(
                "SELECT p.*,c.name AS company_name,a.assignee_id AS assigned_assignee_id,"
                "a.due_on AS assigned_due_on,a.sla_state,a.version AS assignment_version,"
                "a.note AS assignment_note,u.name AS assignee_name "
                "FROM workspace_task_projections p JOIN companies c ON c.id=p.company_id "
                "LEFT JOIN firm_task_assignments a "
                "ON a.firm_id=p.firm_id AND a.kind=p.kind AND a.ref=p.ref "
                "LEFT JOIN users u ON u.id=a.assignee_id WHERE p.firm_id=? "
                "ORDER BY p.updated_at DESC,p.kind,p.ref LIMIT 500",
                (actor.firm_id,),
            ).fetchall()
            assignees = self._assignees(conn, actor)
        today = date.today().isoformat()
        items = []
        counts = {"today": 0, "overdue": 0, "blocked": 0, "review": 0, "failed": 0}
        for row in rows:
            state = str(row["state"] or "")
            kind = str(row["kind"] or "")
            due = row["assigned_due_on"] or row["due_on"] or ""
            if kind in {"ocr", "proposal", "alert"} and state in FAILED_STATES:
                target = "failed"
            elif state in BLOCKED_STATES or kind in {"case", "alert"}:
                target = "blocked"
            elif due and due < today:
                target = "overdue"
            elif due == today:
                target = "today"
            elif kind in {"review", "ocr", "proposal"}:
                target = "review"
            else:
                target = "today"
            counts[target] += 1
            if bucket not in {"all", target}:
                continue
            items.append(
                {
                    "kind": kind,
                    "ref": row["ref"],
                    "company_id": row["company_id"],
                    "company_name": row["company_name"],
                    "book_id": row["book_id"],
                    "title": row["title"],
                    "state": state,
                    "due_on": due,
                    "assignee_id": row["assigned_assignee_id"],
                    "assignee_name": row["assignee_name"],
                    "assignment_version": row["assignment_version"] or 0,
                    "assignment_note": row["assignment_note"] or "",
                    "bucket": target,
                    "updated_at": row["updated_at"],
                }
            )
        items.sort(
            key=lambda item: (
                {"failed": 0, "blocked": 1, "overdue": 2, "today": 3, "review": 4}[item["bucket"]],
                item["due_on"] or "9999-12-31",
                item["updated_at"],
            )
        )
        return {
            "counts": counts,
            "items": items[:limit],
            "assignees": assignees,
            "note": "收件箱复用 workspace_task_projections；分派和截止日期独立记录。",
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
            self._visible_tasks(conn, actor, [request])
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

    def messages(self, actor, limit=50, unread_only=False):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            manager = self.access.firm_role(conn, actor) in {"owner", "admin"}
            companies = [
                row["company_id"]
                for row in conn.execute(
                    "SELECT company_id FROM company_memberships WHERE user_id=?",
                    (actor.user_id,),
                )
            ]
            if manager:
                clauses, values = ["1=1"], []
            else:
                clauses = ["recipient_user_id=?"]
                values = [actor.user_id]
                if companies:
                    clauses.append(
                        "recipient_user_id IS NULL AND company_id IN("
                        + ",".join("?" for _ in companies)
                        + ")"
                    )
                    values.extend(companies)
            truth = " OR ".join("(" + clause + ")" for clause in clauses)
            sql = "SELECT * FROM client_messages WHERE firm_id=? AND " + truth
            if unread_only:
                sql += " AND read_at IS NULL"
            sql += " ORDER BY created_at DESC,id DESC LIMIT ?"
            values = [actor.firm_id, *values, limit]
            rows = conn.execute(sql, values).fetchall()
            return {
                "messages": [
                    {
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
                            "read_at",
                        )
                    }
                    for row in rows
                ],
                "unread": sum(1 for row in rows if not row["read_at"]),
            }

    def mark_message_read(self, actor, message_id):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            row = conn.execute(
                "SELECT * FROM client_messages WHERE id=? AND firm_id=?",
                (message_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("消息不存在或无权访问", 404)
            manager = self.access.firm_role(conn, actor) in {"owner", "admin"}
            company = conn.execute(
                "SELECT 1 FROM company_memberships WHERE company_id=? AND user_id=?",
                (row["company_id"], actor.user_id),
            ).fetchone()
            if (
                not manager
                and row["recipient_user_id"] not in {None, actor.user_id}
                and not company
            ):
                raise AccountingError("消息不存在或无权访问", 404)
            if not row["read_at"]:
                conn.execute("UPDATE client_messages SET read_at=? WHERE id=?", (now(), message_id))
            return {"id": message_id, "read": True}

    @staticmethod
    def _audit(conn, actor, action, details, stamp):
        conn.execute(
            "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (actor.firm_id, actor.user_id, action, "", details, stamp),
        )
