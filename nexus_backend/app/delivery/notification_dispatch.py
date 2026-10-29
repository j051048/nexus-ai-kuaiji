"""Bounded notification attempts; no transport runs inside a transaction.

PostgreSQL workers execute narrowly granted functions. SQLite implements the
same state machine locally, without claiming to provide database-role security.
A sending marker is committed before transport entry. An ambiguous result can
only become unknown, never an automatically retried failure.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from ..accounting.domain import AccountingError, Actor
from ..jobs.restore_hold import restore_hold_active


def timestamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def restore_epoch(conn):
    row = conn.execute(
        "SELECT restore_id FROM restore_state WHERE instance_id='restored'"
    ).fetchone()
    return row[0] if row else ""


def assert_not_held(conn, database=None):
    path = None if getattr(database, "is_postgres", False) else getattr(database, "path", None)
    if (
        restore_hold_active(path)
        or conn.execute(
            "SELECT 1 FROM restore_state WHERE hold<>FALSE OR hold IS NULL LIMIT 1"
        ).fetchone()
    ):
        raise AccountingError("恢复隔离期间禁止外发或新增交付通知", 409)


class NotificationDispatcher:
    @contextmanager
    def worker_transaction(self):
        conn = self.db.connect()
        try:
            if self.db.is_postgres:
                conn.begin(write=True, role="accounting_worker")
            else:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _lease(seconds):
        if type(seconds) is not int or not 1 <= seconds <= 600:
            raise AccountingError("通知租约须为 1–600 秒的整数", 422)

    @staticmethod
    def _json_result(value):
        return json.loads(value) if isinstance(value, str) else value

    def _notification_stale(self, conn, row):
        if row["restore_epoch"] != restore_epoch(conn):
            return "恢复代次已变化，请人工核对后重新安排"
        if row["kind"] == "missing_contact_followup":
            return "缺联系人属于内部待办，不得外发"
        try:
            self.access.book(
                conn, Actor(row["created_by"], row["firm_id"]), row["book_id"], "review"
            )
        except AccountingError:
            return "任务创建人的当前复核权限已变化"
        package = self._package(conn, row["book_id"], row["period"])
        if not package or (package["id"], package["version"], package["manifest_sha256"]) != (
            row["package_id"],
            row["package_version"],
            row["manifest_sha256"],
        ):
            return "交付包版本已变化"
        if self._ack(conn, package["id"]):
            return "客户已签收"
        if conn.execute(
            "SELECT 1 FROM delivery_notifications WHERE package_id=? AND recipient_user_id=? "
            "AND id<>? AND state IN('claimed','sending','unknown') LIMIT 1",
            (row["package_id"], row["recipient_user_id"], row["id"]),
        ).fetchone():
            return "同一交付包已有发送中或结果未知的提醒，请人工核对"
        if not self._recipient_eligible(
            conn,
            row["firm_id"],
            row["company_id"],
            row["book_id"],
            package,
            row["recipient_user_id"],
        ):
            return "收件人当前不能查看并签收该冻结包，请核对权限和服务单联系人"
        grant = self._latest_grant(conn, row["book_id"], row["period"])
        if row["grant_id"]:
            if not grant or (
                grant["id"],
                grant["version"],
                grant["state"],
                grant["recipient_user_id"],
            ) != (row["grant_id"], row["grant_version"], "active", row["recipient_user_id"]):
                return "交付授权已撤回或被替代"
        elif grant:
            return "历史通知未绑定当前交付授权"
        if package["contact_user_id"] and package["contact_user_id"] != row["recipient_user_id"]:
            return "收件人与冻结包不一致"
        try:
            payload = json.loads(row["payload_json"])
            expected = {
                key: row[key]
                for key in (
                    "firm_id",
                    "company_id",
                    "book_id",
                    "period",
                    "package_id",
                    "package_version",
                    "manifest_sha256",
                    "recipient_user_id",
                    "grant_id",
                    "grant_version",
                    "restore_epoch",
                    "kind",
                )
            }
            expected["notification_id"] = row["id"]
            if any(payload.get(key) != value for key, value in expected.items()):
                return "通知内容与冻结身份不一致"
        except (ValueError, TypeError, AttributeError):
            return "通知内容损坏"
        return None

    def _notification_audit(self, conn, row, action, details):
        self._audit(
            conn,
            Actor(row["created_by"], row["firm_id"]),
            action,
            {
                "book_id": row["book_id"],
                "notification_id": row["id"],
                "package_id": row["package_id"],
                "package_version": row["package_version"],
                "executor_kind": "worker",
                "worker_id": row["worker_id"],
                "initiated_by": row["created_by"],
                **details,
            },
            timestamp(),
        )

    def _recover_local(self, conn):
        stamp, epoch = timestamp(), restore_epoch(conn)
        held = bool(
            conn.execute(
                "SELECT 1 FROM restore_state WHERE hold<>FALSE OR hold IS NULL LIMIT 1"
            ).fetchone()
        )
        rows = conn.execute(
            "SELECT * FROM delivery_notifications WHERE state IN('pending','failed','claimed','sending') "
            "AND (restore_epoch<>? OR (state IN('claimed','sending') AND (lease_until<=? OR ?))) "
            "ORDER BY created_at,id LIMIT 100",
            (epoch, stamp, held),
        ).fetchall()
        for row in rows:
            state = (
                "unknown"
                if row["state"] == "sending"
                else ("cancelled" if row["restore_epoch"] != epoch or held else "pending")
            )
            reason = "恢复代次变化或租约过期；已标记发送的结果须人工核对"
            conn.execute(
                "UPDATE delivery_notifications SET state=?,updated_at=?,last_error=?,lease_until=NULL "
                "WHERE id=? AND state=? AND lease_token IS ?",
                (state, stamp, reason, row["id"], row["state"], row["lease_token"]),
            )
            self._notification_audit(conn, row, "delivery.notification.recover", {"state": state})
        return len(rows)

    def recover(self):
        """Recover bookkeeping only; never invokes a transport, including under hold."""
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                return int(
                    conn.execute("SELECT accounting.recover_delivery_notifications()").fetchone()[0]
                )
            return self._recover_local(conn)

    def pending_count(self):
        with self.worker_transaction() as conn:
            assert_not_held(conn, self.db)
            if self.db.is_postgres:
                return int(
                    conn.execute(
                        "SELECT accounting.pending_delivery_notification_count()"
                    ).fetchone()[0]
                )
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM delivery_notifications WHERE state IN('pending','failed') AND attempts<5 "
                    "AND kind<>'missing_contact_followup'"
                ).fetchone()[0]
            )

    def claim(self, worker_id="delivery-local", lease_seconds=120):
        self._lease(lease_seconds)
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 100:
            raise AccountingError("通知执行器标识无效", 422)
        with self.worker_transaction() as conn:
            assert_not_held(conn, self.db)
            if self.db.is_postgres:
                return self._json_result(
                    conn.execute(
                        "SELECT accounting.claim_delivery_notification(?,?)",
                        (worker_id, lease_seconds),
                    ).fetchone()[0]
                )
            self._recover_local(conn)
            row = conn.execute(
                "SELECT * FROM delivery_notifications WHERE state IN('pending','failed') "
                "AND kind<>'missing_contact_followup' AND attempts<5 AND available_at<=? "
                "ORDER BY created_at,id LIMIT 1",
                (timestamp(),),
            ).fetchone()
            if not row:
                return None
            stale = self._notification_stale(conn, row)
            if stale:
                conn.execute(
                    "UPDATE delivery_notifications SET state='cancelled',updated_at=?,last_error=? WHERE id=?",
                    (timestamp(), stale, row["id"]),
                )
                self._notification_audit(
                    conn, row, "delivery.notification.cancel", {"reason": stale}
                )
            else:
                from uuid import uuid4

                conn.execute(
                    "UPDATE delivery_notifications SET state='claimed',worker_id=?,lease_token=?,lease_until=?,"
                    "updated_at=? WHERE id=?",
                    (worker_id, uuid4().hex, timestamp(lease_seconds), timestamp(), row["id"]),
                )
                self._notification_audit(
                    conn, row, "delivery.notification.claim", {"worker_id": worker_id}
                )
            return dict(
                conn.execute(
                    "SELECT * FROM delivery_notifications WHERE id=?", (row["id"],)
                ).fetchone()
            )

    def begin(self, notification_id, lease_token, lease_seconds=120):
        self._lease(lease_seconds)
        with self.worker_transaction() as conn:
            assert_not_held(conn, self.db)
            if self.db.is_postgres:
                return self._json_result(
                    conn.execute(
                        "SELECT accounting.begin_delivery_notification(?,?,?)",
                        (notification_id, lease_token, lease_seconds),
                    ).fetchone()[0]
                )
            row = conn.execute(
                "SELECT * FROM delivery_notifications WHERE id=?", (notification_id,)
            ).fetchone()
            if (
                not row
                or row["state"] != "claimed"
                or row["lease_token"] != lease_token
                or row["lease_until"] <= timestamp()
            ):
                return None
            stale = self._notification_stale(conn, row)
            if stale:
                conn.execute(
                    "UPDATE delivery_notifications SET state='cancelled',last_error=?,updated_at=? WHERE id=?",
                    (stale, timestamp(), notification_id),
                )
                self._notification_audit(
                    conn, row, "delivery.notification.cancel", {"reason": stale}
                )
                return None
            conn.execute(
                "UPDATE delivery_notifications SET state='sending',attempts=attempts+1,send_started_at=?,"
                "updated_at=?,lease_until=? WHERE id=?",
                (timestamp(), timestamp(), timestamp(lease_seconds), notification_id),
            )
            self._notification_audit(
                conn, row, "delivery.notification.begin", {"attempt": row["attempts"] + 1}
            )
            return dict(
                conn.execute(
                    "SELECT * FROM delivery_notifications WHERE id=?", (notification_id,)
                ).fetchone()
            )

    def finish(self, notification_id, lease_token, outcome, sink_ref="", error=""):
        if outcome not in {"sink_written", "failed", "unknown"}:
            raise AccountingError("通知结算状态无效", 422)
        if (
            not isinstance(sink_ref, str)
            or len(sink_ref) > 500
            or not isinstance(error, str)
            or len(error) > 200
        ):
            raise AccountingError("通知回执格式无效", 422)
        if outcome == "sink_written" and not sink_ref.strip():
            raise AccountingError("通知回执为空，不能判定已写入", 422)
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                return bool(
                    conn.execute(
                        "SELECT accounting.finish_delivery_notification(?,?,?,?,?)",
                        (notification_id, lease_token, outcome, sink_ref, error),
                    ).fetchone()[0]
                )
            row = conn.execute(
                "SELECT * FROM delivery_notifications WHERE id=?", (notification_id,)
            ).fetchone()
            if (
                not row
                or row["state"] != "sending"
                or row["lease_token"] != lease_token
                or row["lease_until"] <= timestamp()
                or row["restore_epoch"] != restore_epoch(conn)
            ):
                return False
            conn.execute(
                "UPDATE delivery_notifications SET state=?,sink_ref=?,last_error=?,updated_at=?,"
                "dispatched_at=?,available_at=?,lease_until=NULL WHERE id=?",
                (
                    outcome,
                    sink_ref,
                    error,
                    timestamp(),
                    timestamp() if outcome == "sink_written" else None,
                    timestamp(30) if outcome == "failed" else "",
                    notification_id,
                ),
            )
            self._notification_audit(
                conn, row, "delivery.notification.finish", {"state": outcome, "error": error}
            )
            return True
