"""Durable parent/child batch state over the existing per-company services."""

from datetime import datetime, timezone, timedelta
from contextlib import contextmanager
import json
from uuid import uuid4

from ..accounting.domain import Actor, AccountingError
from ..accounting.service import fingerprint
from ..monthly.batch import BatchOpen

ACTIVE = {"queued", "running"}
FINISHED = {"succeeded", "skipped", "failed", "cancelled"}


def timestamp(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class BatchOperationsService:
    def __init__(self, db, monthly):
        self.db, self.monthly = db, monthly
        self.access = monthly.access

    @staticmethod
    def _request_json(request):
        return json.dumps(request.model_dump(exclude={"idempotency_key"}), ensure_ascii=False)

    @contextmanager
    def worker_transaction(self):
        conn = self.db.connect()
        try:
            if self.db.is_postgres:
                conn.begin(write=True, role="accounting_worker")
                conn.execute("SELECT accounting.lock_legacy_write()")
            else:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def preview_monthly_open(self, actor, request):
        """Read-only dry run reused by the batch dialog before committing."""
        return self.monthly.preview(actor, request)

    def create_monthly_open(self, actor, request):
        company_ids, names = self.monthly.resolve_companies(actor, request)
        request_json = self._request_json(request)
        digest = fingerprint({"kind": "monthly_open", "request": json.loads(request_json)})
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            existing = conn.execute(
                "SELECT * FROM batch_operations WHERE firm_id=? AND created_by=? AND idempotency_key=?",
                (actor.firm_id, actor.user_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_json"] != request_json:
                    raise AccountingError("批量任务幂等键已用于不同请求", 409)
                return self._json(conn, existing, with_items=True)
            batch_id, stamp = uuid4().hex, timestamp()
            status = "queued" if company_ids else "completed"
            conn.execute(
                "INSERT INTO batch_operations(id,firm_id,kind,status,idempotency_key,request_json,total,"
                "succeeded,skipped,failed,created_by,created_at,updated_at,started_at,finished_at) "
                "VALUES(?,?,'monthly_open',?,?,?,?,0,0,0,?,?,?,?,?)",
                (
                    batch_id,
                    actor.firm_id,
                    status,
                    request.idempotency_key,
                    request_json,
                    len(company_ids),
                    actor.user_id,
                    stamp,
                    stamp,
                    None if company_ids else stamp,
                    stamp if not company_ids else None,
                ),
            )
            for sequence, company_id in enumerate(company_ids, start=1):
                conn.execute(
                    "INSERT INTO batch_operation_items(id,batch_id,firm_id,company_id,company_name,"
                    "sequence_no,status,attempt,max_attempts,available_at,result_json,error_code,"
                    "error_detail,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,'queued',0,3,?,'{}','','',?,?)",
                    (
                        uuid4().hex,
                        batch_id,
                        actor.firm_id,
                        company_id,
                        names.get(company_id, ""),
                        sequence,
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
            conn.execute(
                "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,'batch.monthly_open.enqueue',?,?,?)",
                (
                    actor.firm_id,
                    actor.user_id,
                    batch_id,
                    json.dumps(
                        {
                            "kind": "monthly_open",
                            "period": request.period,
                            "total": len(company_ids),
                            "digest": digest,
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                ),
            )
            row = conn.execute("SELECT * FROM batch_operations WHERE id=?", (batch_id,)).fetchone()
            return self._json(conn, row, with_items=True)

    def list(self, actor, limit=20, offset=0):
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise AccountingError("分页范围无效", 422)
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            total = conn.execute(
                "SELECT COUNT(*) FROM batch_operations WHERE firm_id=?", (actor.firm_id,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM batch_operations WHERE firm_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (actor.firm_id, limit, offset),
            ).fetchall()
            return {"batches": [self._json(conn, row, False) for row in rows], "total": total}

    def get(self, actor, batch_id):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM batch_operations WHERE id=? AND firm_id=?",
                (batch_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("批量任务不存在或无权访问", 404)
            return self._json(conn, row, True)

    def retry(self, actor, batch_id, request=None):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM batch_operations WHERE id=? AND firm_id=?",
                (batch_id, actor.firm_id),
            ).fetchone()
            if not row:
                raise AccountingError("批量任务不存在或无权访问", 404)
            stamp = timestamp()
            changed = conn.execute(
                "UPDATE batch_operation_items SET status='queued',attempt=0,available_at=?,"
                "lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='',error_detail='',"
                "finished_at=NULL,updated_at=? WHERE batch_id=? AND status IN('failed','cancelled')",
                (stamp, stamp, batch_id),
            ).rowcount
            if changed:
                conn.execute(
                    "UPDATE batch_operations SET status='queued',finished_at=NULL,updated_at=? "
                    "WHERE id=?",
                    (stamp, batch_id),
                )
                conn.execute(
                    "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at) "
                    "VALUES(?,?,'batch.retry',?,?,?)",
                    (
                        actor.firm_id,
                        actor.user_id,
                        batch_id,
                        json.dumps({"items": changed}, ensure_ascii=False),
                        stamp,
                    ),
                )
            fresh = conn.execute(
                "SELECT * FROM batch_operations WHERE id=?", (batch_id,)
            ).fetchone()
            return self._json(conn, fresh, True)

    def claim(self, worker_id, lease_seconds=120):
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                row = conn.execute(
                    "SELECT * FROM accounting.worker_claim_batch_item(?,?)",
                    (worker_id, lease_seconds),
                ).fetchone()
                return dict(row) if row else None
            stamp, lease = timestamp(), timestamp(lease_seconds)
            row = conn.execute(
                "SELECT i.*,b.kind,b.request_json,b.created_by AS parent_created_by "
                "FROM batch_operation_items i "
                "JOIN batch_operations b ON b.id=i.batch_id "
                "WHERE i.status IN('queued','retry_wait') AND i.available_at<=? "
                "AND i.attempt<i.max_attempts "
                "ORDER BY i.created_at,i.id LIMIT 1",
                (stamp,),
            ).fetchone()
            if not row:
                return None
            token = uuid4().hex
            conn.execute(
                "UPDATE batch_operation_items SET status='running',attempt=attempt+1,worker_id=?,"
                "lease_token=?,lease_until=?,started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
                (worker_id, token, lease, stamp, stamp, row["id"]),
            )
            conn.execute(
                "UPDATE batch_operations SET status='running',started_at=COALESCE(started_at,?),"
                "updated_at=? WHERE id=?",
                (stamp, stamp, row["batch_id"]),
            )
            return {
                "item_id": row["id"],
                "batch_id": row["batch_id"],
                "firm_id": row["firm_id"],
                "created_by": row["parent_created_by"],
                "kind": row["kind"],
                "request_json": row["request_json"],
                "company_id": row["company_id"],
                "company_name": row["company_name"],
                "attempt": row["attempt"] + 1,
                "max_attempts": row["max_attempts"],
                "lease_token": token,
            }

    def finish(self, item, status, result=None, error_code="", error_detail="", retry_seconds=30):
        if status == "retry_wait" and int(item.get("attempt", 0)) >= int(
            item.get("max_attempts", 0)
        ):
            status = "failed"
            error_code = error_code or "max_attempts"
            error_detail = "达到重试上限，已转人工处理"
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                return conn.execute(
                    "SELECT accounting.worker_finish_batch_item(?,?,?,?,?,?,?)",
                    (
                        item["item_id"],
                        item["lease_token"],
                        status,
                        json.dumps(result or {}, ensure_ascii=False),
                        error_code,
                        error_detail,
                        retry_seconds,
                    ),
                ).fetchone()[0]
            stamp = timestamp()
            changed = conn.execute(
                "UPDATE batch_operation_items SET status=?,result_json=?,error_code=?,error_detail=?,"
                "available_at=CASE WHEN ?='retry_wait' THEN ? ELSE available_at END,"
                "lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=?,"
                "finished_at=CASE WHEN ? IN('succeeded','skipped','failed') THEN ? ELSE NULL END "
                "WHERE id=? AND lease_token=? AND status='running'",
                (
                    status,
                    json.dumps(result or {}, ensure_ascii=False),
                    error_code,
                    error_detail,
                    status,
                    timestamp(retry_seconds),
                    stamp,
                    status,
                    stamp,
                    item["item_id"],
                    item["lease_token"],
                ),
            ).rowcount
            if changed:
                counts = conn.execute(
                    "SELECT COUNT(*) FILTER(WHERE status='succeeded') succeeded,"
                    "COUNT(*) FILTER(WHERE status='skipped') skipped,"
                    "COUNT(*) FILTER(WHERE status='failed') failed,"
                    "COUNT(*) FILTER(WHERE status IN('queued','retry_wait')) pending,"
                    "COUNT(*) FILTER(WHERE status='running') running "
                    "FROM batch_operation_items WHERE batch_id=?",
                    (item["batch_id"],),
                ).fetchone()
                state = (
                    "running"
                    if counts["running"]
                    else "queued"
                    if counts["pending"]
                    else "attention"
                    if counts["failed"]
                    else "completed"
                )
                conn.execute(
                    "UPDATE batch_operations SET succeeded=?,skipped=?,failed=?,status=?,updated_at=?,"
                    "finished_at=CASE WHEN ? IN('completed','attention') THEN ? ELSE NULL END WHERE id=?",
                    (
                        counts["succeeded"],
                        counts["skipped"],
                        counts["failed"],
                        state,
                        stamp,
                        state,
                        stamp,
                        item["batch_id"],
                    ),
                )
            return bool(changed)

    def recover(self):
        with self.worker_transaction() as conn:
            if self.db.is_postgres:
                return conn.execute("SELECT accounting.recover_batch_operations()").fetchone()[0]
            stamp = timestamp()
            rows = conn.execute(
                "SELECT * FROM batch_operation_items WHERE status='running' AND lease_until<=?",
                (stamp,),
            ).fetchall()
            for item in rows:
                status = "retry_wait" if item["attempt"] < item["max_attempts"] else "failed"
                conn.execute(
                    "UPDATE batch_operation_items SET status=?,error_code='worker_restarted',"
                    "error_detail=?,available_at=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,"
                    "updated_at=?,finished_at=CASE WHEN ?='failed' THEN ? ELSE NULL END WHERE id=?",
                    (
                        status,
                        "后台执行中断，已进入重试队列"
                        if status == "retry_wait"
                        else "后台执行中断且达到重试上限，请人工处理",
                        stamp,
                        stamp,
                        status,
                        stamp,
                        item["id"],
                    ),
                )
            return len(rows)

    def execute(self, item):
        # The worker executes as the operator who created the parent task; all
        # company and role checks run again in the per-company transaction.
        actor = Actor(item["created_by"], item["firm_id"])
        if item["kind"] != "monthly_open":
            raise AccountingError("批量任务类型不受支持", 422)
        request = BatchOpen.model_validate_json(item["request_json"])
        return self.monthly.open_company(actor, item["company_id"], item["company_name"], request)

    def _json(self, conn, row, with_items):
        value = {
            key: row[key]
            for key in (
                "id",
                "kind",
                "status",
                "idempotency_key",
                "total",
                "succeeded",
                "skipped",
                "failed",
                "created_by",
                "created_at",
                "updated_at",
                "started_at",
                "finished_at",
            )
        }
        value["request"] = json.loads(row["request_json"])
        value["progress"] = {
            "total": value["total"],
            "completed": value["succeeded"] + value["skipped"] + value["failed"],
            "percent": (
                (value["succeeded"] + value["skipped"] + value["failed"]) * 100 // value["total"]
                if value["total"]
                else 100
            ),
        }
        value["items"] = []
        if with_items:
            value["items"] = [
                {
                    **{
                        key: item[key]
                        for key in (
                            "id",
                            "company_id",
                            "company_name",
                            "sequence_no",
                            "status",
                            "attempt",
                            "max_attempts",
                            "error_code",
                            "error_detail",
                            "created_at",
                            "updated_at",
                            "finished_at",
                        )
                    },
                    "result": json.loads(item["result_json"] or "{}"),
                }
                for item in conn.execute(
                    "SELECT * FROM batch_operation_items WHERE batch_id=? ORDER BY sequence_no,id",
                    (row["id"],),
                )
            ]
        return value
