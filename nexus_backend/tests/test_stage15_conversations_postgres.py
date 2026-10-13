"""FORCE RLS and actual separate worker login, without live model calls."""
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database
from app.accounting.domain import AccountingError
from app.accounting.service import AccountingService
from app.assistant.models import ConversationCreate, TurnCreate, TurnCancel
from app.assistant.service import AssistantService, encode
from app.assistant.worker import AssistantWorker
from app.business.service import BusinessService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres
from test_stage15_conversations import SyntheticChatProvider, SyntheticMeter

WORKER_URL = os.getenv("ACCOUNTING_TEST_WORKER_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL or not WORKER_URL, reason="需要专用 PostgreSQL 管理员、受限 API 与实际 worker 登录")


@pytest.fixture
def chat_pg(business_case):
    c = business_case
    provider, meter = SyntheticChatProvider(), SyntheticMeter()
    service = AssistantService(c.db, c.service, BusinessService(c.db), provider=provider)
    worker_db = Database(WORKER_URL)
    worker_service = AssistantService(worker_db, AccountingService(worker_db), BusinessService(worker_db), provider=provider)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("INSERT INTO accounting.ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) VALUES(%s,%s,1,%s,accounting.ocr_utc_now()) ON CONFLICT(firm_id,company_id) DO UPDATE SET enabled=1", (c.owner.firm_id, c.company, c.owner.user_id))
    try:
        yield SimpleNamespace(source=c, db=c.db, service=service, actor=c.reviewer, company=c.company, book=c.book, provider=provider, meter=meter, worker=AssistantWorker(worker_service, provider, meter))
    finally:
        # Only this fixture's UUID firm; immutable input and messages stay saved.
        with c.psycopg.connect(ADMIN_URL) as conn:
            conn.execute("UPDATE accounting.assistant_turns SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='test_cleanup',updated_at=accounting.ocr_utc_now(),finished_at=accounting.ocr_utc_now() WHERE firm_id=%s AND status IN ('queued','running')", (c.owner.firm_id,))


def make_thread(c, **fields):
    return c.service.create(c.actor, ConversationCreate(company_id=c.company, book_ids=[c.book], idempotency_key=uuid4().hex, **fields))["conversation"]


def enqueue(c, thread, text="合成会计问题"):
    return c.service.enqueue(c.actor, thread["id"], TurnCreate(question=text, expected_scope_version=1, idempotency_key=uuid4().hex))["turn"]


def test_pg_real_queue_and_immutable_history(chat_pg):
    c = chat_pg; thread = make_thread(c); turn = enqueue(c, thread)
    # No global queue assumption: poll until this specific turn is terminal.
    import time
    for _ in range(50):
        c.worker.run_once()
        result = c.service.turn(c.actor, thread["id"], turn["id"])["turn"]
        if result["status"] not in {"queued", "running"}:
            break
        time.sleep(.05)
    assert result["status"] == "succeeded", result
    assert c.provider.calls == 1
    assert [m["role"] for m in c.service.messages(c.actor, thread["id"])["messages"]] == ["user", "assistant"]
    next_turn = enqueue(c, thread, "继续合成问题")
    c.worker.run_once()
    assert c.service.turn(c.actor, thread["id"], next_turn["id"])["turn"]["status"] == "succeeded"
    assert len(c.provider.contexts[-1][2]) == 2
    with pytest.raises(c.source.psycopg.Error):
        with c.db.transaction(write=True, actor=c.actor) as conn:
            conn.execute("UPDATE assistant_messages SET content_json='{}' WHERE turn_id=?", (turn["id"],))


def test_pg_conversation_private_and_scope_revocation_before_counts(chat_pg):
    c = chat_pg; thread = make_thread(c)
    with pytest.raises(AccountingError):
        c.service.detail(c.source.owner, thread["id"])
    assert c.service.list(c.source.owner, c.company)["pagination"]["total"] == 0
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (c.book, c.actor.user_id))
    with c.db.transaction(actor=c.actor) as conn:
        assert conn.execute("SELECT id FROM assistant_conversations").fetchall() == []
        assert conn.execute("SELECT id FROM assistant_messages").fetchall() == []
    with pytest.raises(AccountingError):
        c.service.detail(c.actor, thread["id"])


def test_pg_api_cannot_read_lease_forge_claim_send_or_completion(chat_pg):
    c = chat_pg; thread = make_thread(c); turn = enqueue(c, thread)
    for sql, args in [
        ("SELECT lease_token FROM assistant_turns WHERE id=?", [turn["id"]]),
        ("SELECT accounting.assistant_claim(?,180)", ["fake-api-worker"]),
        ("SELECT accounting.assistant_mark_sent(?,?,?)", [turn["id"], "forged", c.provider.configuration_fingerprint()]),
        ("SELECT accounting.assistant_finish(?,?,'succeeded',?,?,'')", [turn["id"], "forged", encode({"answer": "假回答", "citation_ids": [], "proposals": []}), ""]),
        ("UPDATE assistant_turns SET status='succeeded',reply_json='{}' WHERE id=?", [turn["id"]]),
        ("INSERT INTO assistant_messages(id,conversation_id,turn_id,role,content_json,created_at) VALUES(?,?,?,'assistant','{}','2026-10-13T00:00:00+00:00')", [uuid4().hex, thread["id"], turn["id"]]),
    ]:
        if "mark_sent" in sql:
            with c.db.transaction(write=True, actor=c.actor) as conn:
                assert conn.execute(sql, args).fetchone()[0] is False
        else:
            with pytest.raises(c.source.psycopg.Error):
                with c.db.transaction(write=True, actor=c.actor) as conn:
                    conn.execute(sql, args)


def test_pg_native_turn_rejects_forged_history_and_extra_scope(chat_pg):
    c = chat_pg; thread = make_thread(c); turn = enqueue(c, thread)
    c.service.cancel(c.actor, thread["id"], turn["id"], TurnCancel(reason="合成测试取消"))
    with c.db.transaction(actor=c.actor) as conn:
        # Explicit projection never reads private leased columns.
        columns = "conversation_id,firm_id,company_id,created_by,kind,provider,model,expected_scope_version,question,input_fingerprint,model_fingerprint,snapshot_json,history_json,available_at,created_at,updated_at"
        base = dict(conn.execute("SELECT " + columns + " FROM assistant_turns WHERE id=?", (turn["id"],)).fetchone())
    history = [{"role": "user", "content": {"question": "合成会计问题"}}]
    for attack in ("history", "scope", "evidence"):
        payload = dict(base); payload.update(id=uuid4().hex, request_key=uuid4().hex, request_fingerprint="a" * 64, status="queued", history_json=encode(history))
        snapshot = json.loads(payload["snapshot_json"])
        if attack == "history":
            payload["history_json"] = encode([{"role": "assistant", "content": {"answer": "假造过去成功结果"}}])
        elif attack == "scope":
            snapshot["books"].append({"id": c.source.other_book})
        else:
            snapshot["evidence"].append({"id": "book:" + c.source.other_book, "kind": "book", "book_id": c.source.other_book})
        payload["snapshot_json"] = encode(snapshot)
        with pytest.raises(c.source.psycopg.Error):
            with c.db.transaction(write=True, actor=c.actor) as conn:
                conn.execute("INSERT INTO assistant_turns(" + ",".join(payload) + ") VALUES(" + ",".join("?" for _ in payload) + ")", list(payload.values()))


def test_pg_expired_sent_turn_is_interrupted_and_old_token_fenced(chat_pg):
    c = chat_pg; thread = make_thread(c); turn = enqueue(c, thread); claimed = c.worker.claim()
    assert claimed and claimed["id"] == turn["id"]
    with c.worker.db.transaction(write=True, actor=c.actor) as conn:
        assert conn.execute("SELECT accounting.assistant_mark_sent(?,?,?)", (turn["id"], claimed["lease_token"], c.provider.configuration_fingerprint())).fetchone()[0]
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.assistant_turns SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s", (turn["id"],))
    c.worker.claim()
    assert c.service.turn(c.actor, thread["id"], turn["id"])["turn"]["status"] == "interrupted"
    assert not c.worker.finish(claimed, "failed", code="old-worker") and c.provider.calls == 0


def test_pg_native_unknown_citation_rejected_and_worker_has_no_raw_select(chat_pg):
    c = chat_pg; thread = make_thread(c); turn = enqueue(c, thread); claimed = c.worker.claim()
    assert claimed and claimed["id"] == turn["id"]
    with c.worker.db.transaction(write=True, actor=c.actor) as conn:
        assert conn.execute("SELECT accounting.assistant_mark_sent(?,?,?)", (turn["id"], claimed["lease_token"], c.provider.configuration_fingerprint())).fetchone()[0]
    with pytest.raises(c.source.psycopg.Error):
        c.worker._private("assistant_finish", (turn["id"], claimed["lease_token"], "succeeded", encode({"answer": "违规引用合成答案", "citation_ids": ["book:secret"], "proposals": []}), "", ""))
    with c.source.psycopg.connect(ADMIN_URL) as conn:
        for table in ("assistant_conversations", "assistant_turns", "assistant_messages"):
            assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ("accounting." + table,)).fetchone()[0]
            assert not conn.execute("SELECT has_table_privilege(%s,%s,'SELECT')", ("accounting_worker", "accounting." + table)).fetchone()[0]


def test_pg_explicit_event_ocr_versions_bind_native_dialogue_basis(chat_pg, tmp_path):
    from app.business.models import BusinessEventCreate
    from app.business.storage import AttachmentStore
    from app.intelligence.requests import OCRRequest
    from app.intelligence.service import IntelligenceService
    from test_business_api import attachment_bytes
    from test_intelligence_workflow import FakeProvider

    c = chat_pg; actor = c.source.employee
    storage = AttachmentStore(tmp_path / "synthetic-private-originals")
    intelligence = IntelligenceService(c.db, storage, FakeProvider())
    event = intelligence.business.create(actor, BusinessEventCreate(company_id=c.company, template_id="reimbursement", payload={
        "occurred_on": "2026-10-03", "description": "合成票据对话", "counterparty": "耗材商店", "amount": "0.30", "direction": "expense", "payment_status": "unpaid", "category": "office"}))
    metadata = storage.put("synthetic.png", "image/png", attachment_bytes())
    event = intelligence.business.add_attachment(actor, event["id"], event["version"], metadata)
    body = OCRRequest(attachment_id=metadata["id"], expected_version=event["version"], idempotency_key=uuid4().hex)
    first_run = intelligence.extract(actor, event["id"], body)
    service = AssistantService(c.db, c.source.service, intelligence.business, provider=c.provider, intelligence=intelligence)
    worker_intelligence = IntelligenceService(c.worker.db, storage, FakeProvider())
    worker_service = AssistantService(c.worker.db, AccountingService(c.worker.db), worker_intelligence.business, provider=c.provider, intelligence=worker_intelligence)
    worker = AssistantWorker(worker_service, c.provider, c.meter)
    thread = service.create(actor, ConversationCreate(company_id=c.company, event_ids=[event["id"]], idempotency_key=uuid4().hex))["conversation"]
    context = service.detail(actor, thread["id"])["context"]
    assert context["events"][0]["documents"][0]["extraction_id"] == first_run["id"]
    assert "raw_text" not in json.dumps(context)
    turn = service.enqueue(actor, thread["id"], TurnCreate(question="合成税額核对", expected_scope_version=1, idempotency_key=uuid4().hex))["turn"]
    intelligence.extract(actor, event["id"], body.model_copy(update={"idempotency_key": uuid4().hex}))
    assert service.detail(actor, thread["id"])["context"]["input_fingerprint"] != context["input_fingerprint"]
    worker.run_once()
    assert service.turn(actor, thread["id"], turn["id"])["turn"]["status"] == "stale" and c.provider.calls == 0
