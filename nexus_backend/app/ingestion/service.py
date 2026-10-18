import hashlib
import json
import secrets
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class IngestionService:
    def __init__(self, db, accounting):
        self.db, self.access = db, accounting.access

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

    def receive_webhook(self, token, payload):
        if not token:
            raise AccountingError("数据连接凭证无效", 403)
        with self.db.transaction(system=True, write=True) as conn:
            row = conn.execute(
                "SELECT * FROM ingestion_connections WHERE token_hash=? AND status='active'",
                (token_hash(token),),
            ).fetchone()
            if not row:
                raise AccountingError("数据连接不存在或已停用", 404)
            summary = {
                "bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                "keys": sorted(payload)[:30] if isinstance(payload, dict) else ["array"],
                "records": len(payload) if isinstance(payload, list) else 1,
            }
            run_id, stamp = uuid4().hex, now()
            conn.execute(
                "INSERT INTO ingestion_runs(id,connection_id,firm_id,company_id,status,attempt,"
                "cursor,summary_json,error_code,error_detail,created_at) "
                "VALUES(?,?,?,?,'received',1,?,?,'','',?)",
                (
                    run_id,
                    row["id"],
                    row["firm_id"],
                    row["company_id"],
                    row["last_cursor"],
                    json.dumps(summary, ensure_ascii=False),
                    stamp,
                ),
            )
            return {"run_id": run_id, "status": "received"}
