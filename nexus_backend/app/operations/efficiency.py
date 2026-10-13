"""Permission-scoped, explicitly acknowledged human time and audit metrics."""

from datetime import datetime, timezone
import json
from uuid import uuid4

from ..accounting.access import AccessControl
from ..accounting.domain import AccountingError
from ..accounting.service import fingerprint
from ..business.service import BusinessService, encode

HEARTBEAT_CAP_SECONDS = 60
MAX_SESSION_SECONDS = 8 * 60 * 60


def credited_seconds(last_at, current_at):
    """A heartbeat certifies at most one minute, never an unattended interval."""
    last = datetime.fromisoformat(last_at)
    current = datetime.fromisoformat(current_at)
    return min(HEARTBEAT_CAP_SECONDS, max(0, int((current - last).total_seconds())))


class EfficiencyService:
    def __init__(self, db, clock=None):
        self.db, self.access = db, AccessControl()
        self.business = BusinessService(db)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _stamp(self, conn):
        if self.db.is_postgres:
            return conn.execute(
                "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"+00:00\"')"
            ).fetchone()[0]
        return self.clock().astimezone(timezone.utc).isoformat()

    def _scope(self, conn, actor, book_id, event_id):
        if book_id:
            book = self.access.book(conn, actor, book_id, "review")
            return book["company_id"]
        event = self.business._event(conn, actor, event_id)
        if event["applicant_id"] != actor.user_id or not self.business._membership(
            conn, actor, event["company_id"]
        ):
            raise AccountingError("业务录入计时仅允许本人事项", 404)
        return event["company_id"]

    @staticmethod
    def _json(row):
        return {
            key: row[key]
            for key in (
                "id",
                "book_id",
                "event_id",
                "company_id",
                "activity",
                "state",
                "credited_seconds",
                "started_at",
                "last_active_at",
                "updated_at",
                "version",
            )
        }

    def _session(self, conn, actor, session_id):
        row = conn.execute(
            "SELECT * FROM human_work_sessions WHERE id=? AND firm_id=? AND user_id=?",
            (session_id, actor.firm_id, actor.user_id),
        ).fetchone()
        if not row:
            raise AccountingError("计时记录不存在或无权访问", 404)
        self._scope(conn, actor, row["book_id"], row["event_id"])
        return row

    def start(self, actor, request):
        digest = fingerprint(request.model_dump(exclude={"idempotency_key"}))
        with self.db.transaction(write=True, actor=actor) as conn:
            company_id = self._scope(conn, actor, request.book_id, request.event_id)
            prior = conn.execute(
                "SELECT * FROM human_work_sessions WHERE firm_id=? AND user_id=? AND start_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["start_fingerprint"] != digest:
                    raise AccountingError("计时幂等键已用于不同事项", 409)
                return self._json(prior)
            active = conn.execute(
                "SELECT id FROM human_work_sessions WHERE firm_id=? AND user_id=? AND state='active'",
                (actor.firm_id, actor.user_id),
            ).fetchone()
            if active:
                raise AccountingError("请先暂停或完成已有计时，不能重复计入并行工作", 409)
            stamp, session_id = self._stamp(conn), uuid4().hex
            conn.execute(
                "INSERT INTO human_work_sessions(id,firm_id,company_id,user_id,book_id,event_id,activity,state,credited_seconds,started_at,last_active_at,updated_at,version,start_key,start_fingerprint) VALUES(?,?,?,?,?,?,?,'active',0,?,?,?,1,?,?)",
                (
                    session_id,
                    actor.firm_id,
                    company_id,
                    actor.user_id,
                    request.book_id,
                    request.event_id,
                    request.activity,
                    stamp,
                    stamp,
                    stamp,
                    request.idempotency_key,
                    digest,
                ),
            )
            return self._json(self._session(conn, actor, session_id))

    def command(self, actor, session_id, request):
        digest = fingerprint(
            {"session_id": session_id, **request.model_dump(exclude={"idempotency_key"})}
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            row = self._session(conn, actor, session_id)
            prior = conn.execute(
                "SELECT * FROM human_work_commands WHERE firm_id=? AND user_id=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if prior:
                if prior["request_fingerprint"] != digest:
                    raise AccountingError("计时幂等键已用于不同操作", 409)
                return json.loads(prior["response_json"])
            if row["version"] != request.expected_version:
                raise AccountingError("计时状态已改变，请刷新", 409)
            allowed = {
                "heartbeat": {"active"},
                "pause": {"active"},
                "resume": {"paused"},
                "complete": {"active", "paused"},
            }
            if row["state"] not in allowed[request.action]:
                raise AccountingError("计时状态不允许此操作", 409)
            if (
                request.action == "resume"
                and conn.execute(
                    "SELECT 1 FROM human_work_sessions WHERE firm_id=? AND user_id=? AND state='active'",
                    (actor.firm_id, actor.user_id),
                ).fetchone()
            ):
                raise AccountingError("请先暂停或完成已有计时", 409)
            stamp = self._stamp(conn)
            credit = (
                credited_seconds(row["last_active_at"], stamp) if row["state"] == "active" else 0
            )
            total = min(MAX_SESSION_SECONDS, row["credited_seconds"] + credit)
            state = {
                "heartbeat": "active",
                "resume": "active",
                "pause": "paused",
                "complete": "completed",
            }[request.action]
            if total == MAX_SESSION_SECONDS and state == "active":
                state = "paused"
            if self.db.is_postgres:
                updated = conn.execute(
                    "SELECT accounting.advance_human_work_session(?,?,?,?,?)",
                    (
                        session_id,
                        request.expected_version,
                        request.action,
                        request.idempotency_key,
                        digest,
                    ),
                ).fetchone()[0]
                if isinstance(updated, str):
                    updated = json.loads(updated)
                return updated
            changed = conn.execute(
                "UPDATE human_work_sessions SET state=?,credited_seconds=?,last_active_at=?,updated_at=?,version=version+1 WHERE id=? AND version=?",
                (state, total, stamp, stamp, session_id, request.expected_version),
            )
            if changed.rowcount != 1:
                raise AccountingError("计时状态已改变", 409)
            result = self._json(self._session(conn, actor, session_id))
            conn.execute(
                "INSERT INTO human_work_commands(firm_id,user_id,idempotency_key,session_id,request_fingerprint,response_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    actor.user_id,
                    request.idempotency_key,
                    session_id,
                    digest,
                    encode(result),
                    stamp,
                ),
            )
            return result

    def own(self, actor, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise AccountingError("计时分页参数无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_user(conn, actor.user_id)
            role = self.access.firm_role(conn, actor)
            # Authorization precedes both count and pagination. PostgreSQL RLS
            # independently enforces the same scope; SQLite checks it here.
            where = """FROM human_work_sessions s JOIN firm_clients fc ON fc.firm_id=s.firm_id AND fc.company_id=s.company_id AND fc.status='active'
                LEFT JOIN books b ON b.id=s.book_id LEFT JOIN book_access a ON a.book_id=s.book_id AND a.firm_id=s.firm_id AND a.user_id=s.user_id
                LEFT JOIN company_memberships cm ON cm.company_id=s.company_id AND cm.user_id=s.user_id
                LEFT JOIN business_events e ON e.id=s.event_id
                WHERE s.firm_id=? AND s.user_id=? AND ((s.book_id IS NOT NULL AND (?=1 OR (a.role='reviewer' AND (?=1 OR (?=1 AND cm.role='finance')))))
                OR (s.event_id IS NOT NULL AND cm.user_id IS NOT NULL AND e.applicant_id=s.user_id))"""
            values = (
                actor.firm_id,
                actor.user_id,
                int(role in {"owner", "admin"}),
                int(role in {"owner", "admin", "reviewer"}),
                int(role is None),
            )
            total = conn.execute("SELECT COUNT(*) " + where, values).fetchone()[0]
            rows = conn.execute(
                "SELECT s.* " + where + " ORDER BY s.started_at DESC,s.id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            return {
                "sessions": [self._json(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
                "heartbeat_cap_seconds": HEARTBEAT_CAP_SECONDS,
            }

    def overview(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self.access.book(conn, actor, book_id, "review")
            sessions = conn.execute(
                "SELECT activity,state,SUM(credited_seconds) AS seconds,COUNT(*) AS count FROM human_work_sessions WHERE firm_id=? AND book_id=? GROUP BY activity,state",
                (actor.firm_id, book_id),
            ).fetchall()
            audited = conn.execute(
                "SELECT action,object_id,details,created_at FROM audit_events WHERE firm_id=? AND book_id=? AND action IN ('draft.approve','draft.update','draft.template.apply','draft.company_rule.apply') ORDER BY created_at,id",
                (actor.firm_id, book_id),
            ).fetchall()
            by_draft = {}
            for row in audited:
                by_draft.setdefault(row["object_id"], []).append(dict(row))
            reviewed = first_pass = corrected = rule_covered = 0
            for actions in by_draft.values():
                approval = next(
                    (item for item in actions if item["action"] == "draft.approve"), None
                )
                if not approval:
                    continue
                reviewed += 1
                before = actions[: actions.index(approval)]
                had_correction = any(item["action"] == "draft.update" for item in before)
                corrected += int(had_correction)
                first_pass += int(not had_correction)
                rule_covered += int(
                    any(item["action"] == "draft.company_rule.apply" for item in before)
                )
            cases = conn.execute(
                "SELECT h.case_id,h.action,h.created_at FROM case_history h JOIN case_tasks c ON c.id=h.case_id LEFT JOIN business_mappings m ON m.event_id=c.event_id WHERE c.firm_id=? AND (c.book_id=? OR (c.book_id IS NULL AND m.book_id=?)) AND c.kind='supplement' AND h.action IN ('create','respond') ORDER BY h.created_at,h.case_version,h.id",
                (actor.firm_id, book_id, book_id),
            ).fetchall()
            waiting = {}
            waits = []
            for entry in cases:
                if entry["action"] == "create":
                    waiting[entry["case_id"]] = entry["created_at"]
                elif entry["case_id"] in waiting:
                    begun = datetime.fromisoformat(waiting.pop(entry["case_id"]))
                    ended = datetime.fromisoformat(entry["created_at"])
                    waits.append(max(0, int((ended - begun).total_seconds())))
            # Fixed rules are draft help, and a zero modification count is not
            # independently proven accounting correctness or saved work time.
            return {
                "book_id": book_id,
                "company_id": book["company_id"],
                "period": book["period"],
                "human": {
                    "credited_seconds": sum(int(row["seconds"]) for row in sessions),
                    "session_count": sum(row["count"] for row in sessions),
                    "activities": [
                        {
                            "activity": row["activity"],
                            "state": row["state"],
                            "credited_seconds": int(row["seconds"]),
                            "session_count": row["count"],
                        }
                        for row in sessions
                    ],
                    "measurement": "explicit_sessions",
                    "heartbeat_cap_seconds": HEARTBEAT_CAP_SECONDS,
                },
                "review": {
                    "reviewed_drafts": reviewed,
                    "without_recorded_correction": first_pass,
                    "with_recorded_correction": corrected,
                    "company_rule_covered": rule_covered,
                },
                "supplement": {
                    "completed_first_response_intervals": len(waits),
                    "calendar_wait_seconds": sum(waits),
                    "measurement": "首次发布补件请求到首次提交响应的日历等待，不是人工工时",
                },
                "definitions": {
                    "without_recorded_correction": "首次审核前没有 draft.update 审计记录；不代表独立正确率",
                    "company_rule_covered": "首次审核前记录了企业规则应用；不代表最终采用了全部规则分录",
                    "human": "用户明确开启计时且持续确认活跃的累计时间；不推算未计时工时",
                },
            }
