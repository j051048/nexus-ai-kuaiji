"""Persistent dialogue lane. Sent requests are never automatically replayed."""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.accounting.domain import Actor, AccountingError
from app.accounting.service import now
from app.intelligence.provider import ProviderError
from app.operations.meter import BudgetError
from .models import AssistantReply
from .monitor import AssistantMonitor
from .service import encode


logger = logging.getLogger(__name__)


class AssistantWorker:
    def __init__(self, service, provider, meter, concurrency=1, lease_seconds=180, poll_seconds=1, monitor=None):
        if concurrency not in {1, 2} or not 30 <= lease_seconds <= 600:
            raise ValueError("会计助手并发为 1–2，租约为 30–600 秒")
        self.service, self.db, self.provider, self.meter = service, service.db, provider, meter
        self.concurrency, self.lease_seconds, self.poll_seconds = concurrency, lease_seconds, poll_seconds
        self.worker_id = "assistant-" + uuid4().hex
        self.monitor = monitor or AssistantMonitor(self.db, self.worker_id)
        self._stop = threading.Event(); self._thread = None

    def _private(self, name, args=(), rows=False):
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN")
            try:
                conn.execute("SET LOCAL ROLE accounting_worker")
                conn.execute("SET LOCAL search_path TO accounting,pg_catalog")
                marks = ",".join("?" for _ in args)
                query = "SELECT row_to_json(t)::text FROM accounting." + name + "(" + marks + ") t" if rows else "SELECT accounting." + name + "(" + marks + ")"
                result = conn.execute(query, args)
                value = [json.loads(r[0]) for r in result.fetchall()] if rows else result.fetchone()[0]
                conn.commit(); return value
            except Exception:
                conn.rollback(); raise

    @staticmethod
    def _actor(turn):
        return Actor(turn["created_by"], turn["firm_id"])

    def _current(self, conn, turn):
        actor = self._actor(turn)
        conversation = self.service._conversation(conn, actor, turn["conversation_id"])
        if not self.service._availability(conn, actor, conversation["company_id"])["company_enabled"]:
            raise AccountingError("企业已关闭模型处理，资料不会继续发送", 403)
        context = self.service.snapshot_in_transaction(conn, actor, conversation)
        if context["input_fingerprint"] != turn["input_fingerprint"] or encode(context) != turn["snapshot_json"]:
            raise AccountingError("会计助手引用资料已变化，请重新提交当前问题", 409)
        if self.provider.configuration_fingerprint() != turn["model_fingerprint"]:
            raise AccountingError("模型配置已变化，请明确提交新问题", 409)
        if encode(self.service._history(conn, turn["conversation_id"], turn["id"])) != turn["history_json"]:
            raise AccountingError("已保存对话历史不匹配，不会发送伪造上下文", 409)
        return context

    def _check(self, conn, turn):
        if self.db.is_postgres:
            conn.execute("SELECT set_config('app.assistant_turn_id',?,true),set_config('app.assistant_token',?,true)", (turn["id"], turn["lease_token"]))
            valid = conn.execute("SELECT accounting.assistant_check(?,?)", (turn["id"], turn["lease_token"])).fetchone()[0]
            if not valid:
                raise AccountingError("会计助手租约或授权已失效", 409)
        else:
            row = conn.execute("SELECT 1 FROM assistant_turns WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (turn["id"], turn["lease_token"], now())).fetchone()
            if not row:
                raise AccountingError("会计助手租约已失效", 409)
        return self._current(conn, turn)

    def claim(self):
        if self.db.is_postgres:
            rows = self._private("assistant_claim", (self.worker_id, self.lease_seconds), rows=True)
            return rows[0] if rows else None
        with self.db.transaction(write=True) as conn:
            stamp = now()
            for row in conn.execute("SELECT * FROM assistant_turns WHERE status='running' AND lease_until<=? ORDER BY lease_until,id LIMIT 100", (stamp,)).fetchall():
                outcome = "interrupted" if row["sent_at"] else "failed" if row["attempt_count"] >= 3 else "queued"
                conn.execute("UPDATE assistant_turns SET status=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code=?,error_detail=?,updated_at=?,finished_at=?,available_at=? WHERE id=?", (outcome, "response_unknown" if row["sent_at"] else "lease_expired", "请求已经发出，结果未知；不会自动重复发送" if row["sent_at"] else "未发送请求已恢复排队", stamp, None if outcome == "queued" else stamp, stamp, row["id"]))
            active = sum(conn.execute("SELECT COUNT(*) FROM " + table + " WHERE status='running'").fetchone()[0] for table in ("assistant_turns", "ocr_jobs", "proposal_jobs"))
            if active >= 2:
                return None
            conn.execute("INSERT OR IGNORE INTO assistant_company_schedule(firm_id,company_id) SELECT DISTINCT firm_id,company_id FROM assistant_turns WHERE status='queued'")
            rows = conn.execute("""SELECT t.* FROM assistant_turns t JOIN assistant_company_schedule s ON s.firm_id=t.firm_id AND s.company_id=t.company_id
              WHERE t.status='queued' AND t.available_at<=? AND NOT EXISTS(SELECT 1 FROM assistant_turns a WHERE a.firm_id=t.firm_id AND a.company_id=t.company_id AND a.status='running')
              AND NOT EXISTS(SELECT 1 FROM ocr_jobs a WHERE a.firm_id=t.firm_id AND a.company_id=t.company_id AND a.status='running')
              AND NOT EXISTS(SELECT 1 FROM proposal_jobs a WHERE a.firm_id=t.firm_id AND a.company_id=t.company_id AND a.status='running')
              ORDER BY s.last_dispatch_seq,t.created_at,t.id LIMIT 100""", (stamp,)).fetchall()
            for row in rows:
                turn = dict(row)
                try:
                    self._current(conn, turn)
                except AccountingError:
                    conn.execute("UPDATE assistant_turns SET status='stale',error_code='scope_changed',error_detail='授权、资料或模型配置已变化，请重新提交',updated_at=?,finished_at=? WHERE id=?", (stamp, stamp, turn["id"]))
                    continue
                seq = conn.execute("SELECT COALESCE(MAX(last_dispatch_seq),0)+1 FROM assistant_company_schedule").fetchone()[0]
                conn.execute("UPDATE assistant_company_schedule SET last_dispatch_seq=? WHERE firm_id=? AND company_id=?", (seq, turn["firm_id"], turn["company_id"]))
                token = uuid4().hex
                lease_until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
                conn.execute("UPDATE assistant_turns SET status='running',attempt_count=attempt_count+1,lease_token=?,lease_until=?,worker_id=?,updated_at=?,error_code='',error_detail='' WHERE id=?", (token, lease_until, self.worker_id, stamp, turn["id"]))
                return dict(conn.execute("SELECT * FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone())
        return None

    def finish(self, turn, outcome, reply=None, code="", detail=""):
        answer = encode(reply.model_dump()) if reply else None
        if self.db.is_postgres:
            return self._private("assistant_finish", (turn["id"], turn["lease_token"], outcome, answer, code, detail))
        with self.db.transaction(write=True) as conn:
            row = conn.execute("SELECT * FROM assistant_turns WHERE id=? AND status='running' AND lease_token=? AND lease_until>?", (turn["id"], turn["lease_token"], now())).fetchone()
            if not row:
                return False
            try:
                self._current(conn, turn)
            except AccountingError:
                outcome, answer, code, detail = "stale", None, "scope_changed", "资料或权限已变化，旧回复不会发布"
            stamp = now()
            conn.execute("UPDATE assistant_turns SET status=?,reply_json=?,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code=?,error_detail=?,updated_at=?,finished_at=? WHERE id=?", (outcome, answer if outcome == "succeeded" else None, code, detail, stamp, stamp, turn["id"]))
            if outcome == "succeeded":
                conn.execute("INSERT INTO assistant_messages(id,conversation_id,turn_id,role,content_json,created_at) VALUES(?,?,?,'assistant',?,?)", (uuid4().hex, turn["conversation_id"], turn["id"], answer, stamp))
            return True

    def run_once(self):
        turn = self.claim()
        if turn is None:
            return None
        context = self.meter.context(self._actor(turn), turn)
        attempted, result, error = False, None, None
        try:
            if not self.provider.available:
                raise ProviderError("unavailable", "会计对话模型尚未配置，未发送任何资料")
            with self.db.transaction(write=True, actor=self._actor(turn)) as conn:
                snapshot = self._check(conn, turn)
                context.before_send(conn)
                if self.db.is_postgres:
                    sent = conn.execute("SELECT accounting.assistant_mark_sent(?,?,?)", (turn["id"], turn["lease_token"], turn["model_fingerprint"])).fetchone()[0]
                    if not sent:
                        raise AccountingError("助手请求已发送或租约已变化", 409)
                else:
                    sent = conn.execute("UPDATE assistant_turns SET sent_at=?,updated_at=? WHERE id=? AND sent_at IS NULL AND status='running' AND lease_token=?", (now(), now(), turn["id"], turn["lease_token"])).rowcount
                    if not sent:
                        raise AccountingError("助手请求已发送或租约已变化", 409)
            attempted = True
            result = self.provider.reply(snapshot, turn["question"], json.loads(turn["history_json"]))
            reply = result.data if isinstance(result.data, AssistantReply) else AssistantReply.model_validate(result.data)
            allowed = {item["id"] for item in snapshot["evidence"]}
            if set(reply.citation_ids) - allowed:
                raise ProviderError("citation_invalid", "模型引用了当前资料范围之外的证据，回复未发布")
            with self.db.transaction(actor=self._actor(turn)) as conn:
                self._check(conn, turn)
            # Cost facts survive a later authorization change; content does not.
            context.finish(result=result, attempted=True)
            self.finish(turn, "succeeded", reply)
        except BudgetError as caught:
            error = ProviderError(caught.code, caught.detail)
            context.finish(result=result, error=error, attempted=attempted)
            self.finish(turn, "failed", code=error.code, detail=error.detail)
        except AccountingError as caught:
            error = ProviderError("scope_changed" if caught.status in {403, 404, 409} else "policy_rejected", caught.detail)
            context.finish(result=result, error=error, attempted=attempted)
            self.finish(turn, "stale" if caught.status in {403, 404, 409} else "failed", code=error.code, detail=error.detail)
        except ProviderError as caught:
            error = caught
            context.finish(result=result, error=error, attempted=attempted)
            unknown = attempted and caught.code in {"timeout", "network", "network_error", "transport", "response_unknown"}
            self.finish(turn, "interrupted" if unknown else "failed", code="model_unavailable" if caught.code == "unavailable" else caught.code, detail=caught.detail)
        except Exception:
            # No supplier body, credentials, private prompt or stack in API.
            error = ProviderError("response_unknown" if attempted else "worker_failed", "请求结果未知，未自动重复发送" if attempted else "后台处理失败，请检查任务状态")
            context.finish(result=result, error=error, attempted=attempted)
            self.finish(turn, "interrupted" if attempted else "failed", code=error.code, detail=error.detail)
        return turn["id"]

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self.worker_id, daemon=True)
        self._thread.start()

    def _loop(self):
        last_tick = -15.0
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            pending = set()
            while not self._stop.is_set():
                for future in list(pending):
                    if future.done():
                        pending.remove(future)
                        try:
                            future.result()
                        except Exception:
                            logger.warning("会计助手后台任务失败，保留任务等待租约恢复")
                if self.monitor and time.monotonic() - last_tick >= 15:
                    try:
                        self.monitor.tick(inflight=len(pending), state="active" if pending else "idle")
                    except Exception:
                        logger.warning("会计助手运行监测暂不可用，处理任务保持原有租约控制")
                    last_tick = time.monotonic()
                if len(pending) < self.concurrency:
                    pending.add(pool.submit(self.run_once))
                self._stop.wait(self.poll_seconds)
        if self.monitor:
            try:
                self.monitor.tick(inflight=0, state="stopped")
            except Exception:
                logger.warning("会计助手停止心跳保存失败")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
