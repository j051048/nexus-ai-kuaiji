"""Real dedicated worker permissions and native company chat monitor."""
from contextlib import closing
import json
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.assistant.monitor import AssistantMonitor
from app.operations.meter import MeterService
from app.operations.service import OperationsService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage15_conversations_postgres import WORKER_URL, chat_pg, make_thread, enqueue

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要真实独立 PostgreSQL API 与 worker 测试登录")


def test_pg_chat_lane_has_private_metrics_and_current_independent_heartbeat(chat_pg):
    c = chat_pg
    thread = make_thread(c); turn = enqueue(c, thread, "合成私有问题，不得进入监控聚合")
    worker_db = c.worker.db
    operations = OperationsService(c.db, MeterService(c.db))
    monitor = AssistantMonitor(worker_db, "monitor-test-" + uuid4().hex)
    current = operations.overview(c.source.owner, c.company)
    assert current["assistant"]["queue"]["queued"] == current["queue"]["queued"] == 1
    assert turn["question"] not in json.dumps(current, ensure_ascii=False)
    monitor.tick()
    current = operations.overview(c.source.owner, c.company)
    assert current["assistant"]["workers"]["available"] and current["workers"]["assistant_available"]
    with pytest.raises(AccountingError):
        operations.overview(c.actor, c.company)
    # API cannot forge liveness, inspect worker identities or bypass company RLS.
    for sql in ["SELECT accounting.record_assistant_heartbeat('api-forgery','idle',0)", "SELECT worker_id FROM assistant_worker_heartbeats", "SELECT accounting.ops_company_metrics_internal('other','other','2026-10')"]:
        with pytest.raises(c.source.psycopg.Error):
            with c.db.transaction(actor=c.source.owner) as conn:
                conn.execute(sql)
    assert operations.overview(c.source.owner, c.source.other_company)["assistant"]["queue"]["queued"] == 0
    monitor.tick(state="stopped")


def test_pg_chat_creation_and_completion_mark_incremental_company_dirty(chat_pg):
    c = chat_pg; monitor = AssistantMonitor(c.worker.db, "dirty-test-" + uuid4().hex)
    monitor.tick()
    thread = make_thread(c); turn = enqueue(c, thread)
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT 1 FROM accounting.ops_company_dirty WHERE firm_id=%s AND company_id=%s", (c.actor.firm_id, c.company)).fetchone()
    claimed = None
    import time
    for _ in range(50):
        claimed = c.worker.claim()
        if claimed:
            break
        time.sleep(.02)
    assert claimed and claimed["id"] == turn["id"]
    assert c.worker.finish(claimed, "failed", code="synthetic_failure", detail="合成失败")
    monitor.tick()
    current = OperationsService(c.db, MeterService(c.db)).overview(c.source.owner, c.company)
    assert current["assistant"]["queue"]["failed"] == current["queue"]["recent_failures"] == 1
    monitor.tick(state="stopped")
