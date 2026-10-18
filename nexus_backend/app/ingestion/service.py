import hashlib
import json
import secrets
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now

MAX_EVENT_BYTES = 256 * 1024
MAX_EVENT_KEY_LENGTH = 200


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_hash(payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IngestionService:
    def __init__(self, db, accounting, enabled=False):
        self.db, self.access = db, accounting.access
        self.enabled = enabled

    @staticmethod
    def _json(row, *, token=None):
        value = {
            key: row[key]
            for key in (
                "id",
                "company_id",
                "kind",
                "name",
                "status",
                "last_cursor",
                "created_at",
                "updated_at",
            )
        }
        value["config"] = json.loads(row["config_json"])
        if token:
            value["token"] = token
        return value

    def list(self, actor, limit=50):
        with self.db.transaction(actor=actor) as conn:
            self.access.require_manager(conn, actor)
            rows = conn.execute(
                "SELECT * FROM ingestion_connections WHERE firm_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT ?",
                (actor.firm_id, limit),
            ).fetchall()
            runs = conn.execute(
                "SELECT r.*,c.name AS connection_name FROM ingestion_runs r "
                "JOIN ingestion_connections c ON c.id=r.connection_id "
                "WHERE r.firm_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT 100",
                (actor.firm_id,),
            ).fetchall()
            return {
                "connections": [self._json(row) for row in rows],
                "runs": [
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "connection_id",
                            "connection_name",
                            "status",
                            "attempt",
                            "cursor",
                            "error_code",
                            "error_detail",
                            "created_at",
                            "finished_at",
                        )
                    }
                    for row in runs
                ],
            }

    def create(self, actor, request):
        token = secrets.token_urlsafe(32)
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            self.access.company(conn, actor, request.company_id, manage=True)
            stamp = now()
            connection_id = uuid4().hex
            conn.execute(
                "INSERT INTO ingestion_connections(id,firm_id,company_id,kind,name,status,"
                "config_json,token_hash,last_cursor,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'active',?,?,'',?,?,?)",
                (
                    connection_id,
                    actor.firm_id,
                    request.company_id,
                    request.kind,
                    request.name,
                    json.dumps(request.config, ensure_ascii=False),
                    token_hash(token),
                    actor.user_id,
                    stamp,
                    stamp,
                ),
            )
            row = conn.execute(
                "SELECT * FROM ingestion_connections WHERE id=?", (connection_id,)
            ).fetchone()
            return self._json(row, token=token)

    def rotate(self, actor, connection_id):
        token = secrets.token_urlsafe(32)
        with self.db.transaction(write=True, actor=actor) as conn:
            self.access.require_manager(conn, actor)
            changed = conn.execute(
                "UPDATE ingestion_connections SET token_hash=?,updated_at=? "
                "WHERE id=? AND firm_id=?",
                (token_hash(token), now(), connection_id, actor.firm_id),
            ).rowcount
            if not changed:
                raise AccountingError("数据连接不存在或无权访问", 404)
            row = conn.execute(
                "SELECT * FROM ingestion_connections WHERE id=?", (connection_id,)
            ).fetchone()
            return self._json(row, token=token)

    def receive_webhook(self, token, payload, event_key=""):
        if not self.enabled:
            raise AccountingError("数据接入尚未启用", 404)
        if not token:
            raise AccountingError("数据连接凭证无效", 403)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_EVENT_BYTES:
            raise AccountingError("接入事件超过 256 KiB 上限", 413)
        digest = hashlib.sha256(encoded).hexdigest()
        provided_key = str(event_key or "").strip()
        if provided_key and len(provided_key) > MAX_EVENT_KEY_LENGTH:
            key = "sha256:" + hashlib.sha256(provided_key.encode("utf-8")).hexdigest()
        elif provided_key:
            key = provided_key
        else:
            # A stable content key still prevents a replay of an identical body.
            key = "sha256:" + digest
        with self.db.transaction(system=True, write=True) as conn:
            row = conn.execute(
                "SELECT c.*,co.status AS company_status,fc.status AS client_status "
                "FROM ingestion_connections c "
                "JOIN companies co ON co.id=c.company_id "
                "JOIN firm_clients fc ON fc.company_id=c.company_id AND fc.firm_id=c.firm_id "
                "WHERE c.token_hash=? AND c.status='active'",
                (token_hash(token),),
            ).fetchone()
            if not row:
                raise AccountingError("数据连接不存在或已停用", 404)
            if row["company_status"] != "active" or row["client_status"] != "active":
                raise AccountingError("企业已停用，不能接收外部数据", 409)
            existing = conn.execute(
                "SELECT id,status FROM ingestion_runs WHERE connection_id=? AND event_key=?",
                (row["id"], key),
            ).fetchone()
            if existing:
                return {
                    "run_id": existing["id"],
                    "status": existing["status"],
                    "duplicate": True,
                }
            summary = {
                "bytes": len(encoded),
                "keys": sorted(payload)[:30] if isinstance(payload, dict) else ["array"],
                "records": len(payload) if isinstance(payload, list) else 1,
                "event_key": key,
            }
            run_id, stamp = uuid4().hex, now()
            conn.execute(
                "INSERT INTO ingestion_runs(id,connection_id,firm_id,company_id,status,attempt,"
                "cursor,summary_json,error_code,error_detail,created_at,event_key,raw_event_json,"
                "request_hash) VALUES(?,?,?,?,'received',1,?,?,'','',?,?,?,?)",
                (
                    run_id,
                    row["id"],
                    row["firm_id"],
                    row["company_id"],
                    row["last_cursor"],
                    json.dumps(summary, ensure_ascii=False),
                    stamp,
                    key,
                    raw,
                    digest,
                ),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,NULL,?,?,?,?,?)",
                (
                    row["firm_id"],
                    row["created_by"],
                    "ingestion.receive",
                    run_id,
                    json.dumps(
                        {
                            "connection_id": row["id"],
                            "event_key": key,
                            "request_hash": digest,
                            "bytes": len(encoded),
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                ),
            )
            # Receipt only; a consumer must produce an explicit imported/posted
            # result later. Never report the 202 as accounting completion.
            return {"run_id": run_id, "status": "received", "duplicate": False}
