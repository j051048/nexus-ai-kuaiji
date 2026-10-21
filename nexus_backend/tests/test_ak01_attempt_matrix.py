"""Three real worker lanes, durable SQLite, independent processes, loopback supplier.

No claim/SQL implementation is mocked. Lease timestamp writes below are explicit
clock-fault injection after process death; every subsequent recovery uses the
production worker. Counters are recorded by a separate HTTP receiver database.
"""

import json
import multiprocessing
import os
import socket
import sqlite3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.models import DraftUpdate
from app.assistant.models import ConversationCreate, TurnCancel, TurnCreate
from app.business.models import BusinessEventUpdate, HandoffRequest
from app.intelligence.requests import OCRRequest
from app.jobs.requests import BatchItem, BatchRequest
from ak01_process_support import runtime, worker_process
from test_intelligence_workflow import ai_case, create_event, invoice_document, review_original


KINDS = ("ocr", "journal", "chat")
TABLES = {"ocr": "ocr_jobs", "journal": "proposal_jobs", "chat": "assistant_turns"}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("ACCOUNTING_", "SUPABASE_", "ZEABUR_", "VERCEL_", "OPENAI_")):
            monkeypatch.delenv(name)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    scratch = tmp_path / "runtime-temp"
    scratch.mkdir()
    for name in ("TEMP", "TMP"):
        monkeypatch.setenv(name, str(scratch))


@contextmanager
def supplier(directory):
    count_path = directory / "supplier-received.db"
    with sqlite3.connect(count_path) as conn:
        conn.execute("CREATE TABLE requests(id INTEGER PRIMARY KEY,model TEXT NOT NULL)")
    state = SimpleNamespace(
        mode="success",
        received=threading.Event(),
        release=threading.Event(),
        count_path=count_path,
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            model = body["model"]
            with sqlite3.connect(count_path) as conn:
                conn.execute("INSERT INTO requests(model) VALUES(?)", (model,))
            mode = state.mode
            state.received.set()
            if mode == "gate" and not state.release.wait(25):
                self.close_connection = True
                return
            if mode == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            if model == "ak01-ocr":
                data = {"documents": [invoice_document()]}
            elif model == "ak01-journal":
                context = json.loads(body["messages"][-1]["content"].split("\n", 1)[1])
                if "context_pack" in context:
                    context = {row["key"]: row["value"] for row in context["context_pack"]["facts"]}
                document = context["documents"][0]
                data = {
                    "description": "合成票据对应费用",
                    "lines": [
                        {"account_code": "6602.02", "debit": "0.30", "credit": "0.00"},
                        {"account_code": "2241", "debit": "0.00", "credit": "0.30"},
                    ],
                    "reasoning": "按合成票据核对，等待会计复核。",
                    "evidence_refs": [{
                        "attachment_id": document["attachment_id"],
                        "extraction_id": document["extraction_id"],
                        "field": "gross_amount", "page": document["evidence"][0]["page"],
                    }],
                    "warnings": [],
                }
            else:
                data = {"answer": "请人工核对当前资料。", "citation_ids": [], "proposals": []}
            output = json.dumps({
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(data, ensure_ascii=False),
                }}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }, ensure_ascii=False).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(output)))
                self.end_headers()
                self.wfile.write(output)
            except (ConnectionError, OSError):
                # A killed requester is the intended hard-crash fault.
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    state.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.fixture
def attempt_case(ai_case, tmp_path):
    with supplier(tmp_path) as fake:
        c = ai_case
        services = runtime(c.db, c.storage.root, fake.base_url)
        c.intelligence = services.intelligence
        c.business = services.business
        c.ledger = services.ledger
        c.queue = services.queue
        c.provider = services.provider
        c.worker = services.worker
        c.chat = services.chat
        c.chat_worker = services.chat_worker
        c.supplier = fake
        c.folder = tmp_path
        c.processes = []
        try:
            yield c
        finally:
            fake.release.set()
            for process in c.processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
            services.client.close()


def prepare(c, kind):
    c.kind = kind
    if kind == "chat":
        c.actor = c.actors["employee"]
        c.event = create_event(c)
        conv = c.chat.create(c.actor, ConversationCreate(
            company_id=c.company["id"], event_ids=[c.event["id"]], idempotency_key=uuid4().hex,
        ))["conversation"]
        c.conv = conv
        c.command = TurnCreate(
            question="请说明当前可见资料", expected_scope_version=1, idempotency_key=uuid4().hex,
        )
        c.job = c.chat.enqueue(c.actor, conv["id"], c.command)["turn"]
    else:
        event = create_event(c)
        c.event = event
        if kind == "ocr":
            c.actor = c.actors["employee"]
            c.command = BatchRequest(
                company_id=c.company["id"], idempotency_key=uuid4().hex,
                items=[BatchItem(event_id=event["id"], attachment_id=event["attachments"][0]["id"],
                                 expected_version=event["version"])],
            )
            c.batch = c.queue.enqueue(c.actor, c.command)
            c.job = c.batch["jobs"][0]
        else:
            c.intelligence.extract(c.actors["employee"], event["id"], OCRRequest(
                attachment_id=event["attachments"][0]["id"], expected_version=event["version"],
                idempotency_key=uuid4().hex,
            ))
            c.actor = c.actors["finance"]
            submitted = c.business.submit(c.actors["employee"], event["id"], event["version"])
            departmental = c.business.approve_department(
                c.actors["approver"], event["id"], submitted["version"]
            )
            approved = c.business.approve_finance(
                c.actor, event["id"], departmental["version"]
            )
            c.command = HandoffRequest(
                expected_version=approved["version"], book_id=c.book["id"],
                idempotency_key=uuid4().hex,
            )
            result = c.business.handoff(c.actor, event["id"], c.command)
            # The journal fault matrix starts after independent original review.
            # Its synthetic bitmap has no native OCR proof under AK03; do not
            # bypass that blocker merely to reach a transport fault point.
            review_original(c, event)
            c.handoff_receipt = result
            c.draft_id = result["draft_id"]
            c.job = result["automatic_job"]
    c.supplier.received.clear()
    c.supplier.release.clear()
    return c.job["id"]


def received(c):
    with sqlite3.connect(c.supplier.count_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM requests WHERE model=?", ("ak01-" + c.kind,)
        ).fetchone()[0]


def row(c):
    with c.db.transaction() as conn:
        return dict(conn.execute(
            f"SELECT * FROM {TABLES[c.kind]} WHERE id=?", (c.job["id"],)
        ).fetchone())


def costs(c):
    with c.db.transaction() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM model_costs WHERE kind=? ORDER BY created_at",
            ({"journal": "proposal"}.get(c.kind, c.kind),),
        ).fetchall()]


def expire(c):
    with c.db.transaction(write=True) as conn:
        conn.execute(
            f"UPDATE {TABLES[c.kind]} SET lease_until=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", c.job["id"]),
        )


def launch(c, *, before_http_crash=False, shared_start=None):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    start = shared_start or context.Event()
    result_path = c.folder / f"child-{uuid4().hex}.json"
    config = {
        "database": c.db.path, "storage": str(c.storage.root),
        "base_url": c.supplier.base_url, "kind": c.kind,
    }
    process = context.Process(
        target=worker_process,
        args=(config, ready, start, str(result_path), before_http_crash),
    )
    process.start()
    c.processes.append(process)
    assert ready.wait(30), f"worker did not become ready; exit={process.exitcode}"
    if shared_start is None:
        start.set()
    return process, result_path


def complete(process, result_path):
    process.join(30)
    assert not process.is_alive(), "worker did not finish"
    assert process.exitcode == 0, (
        result_path.read_text(encoding="utf-8") if result_path.exists() else process.exitcode
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"], result
    return result


def unpublished(c, expected_draft_version=1):
    with c.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drafts WHERE status='approved'").fetchone()[0] == 0
        if c.kind == "chat":
            assert conn.execute(
                "SELECT COUNT(*) FROM assistant_messages WHERE turn_id=? AND role='assistant'",
                (c.job["id"],),
            ).fetchone()[0] == 0
        elif c.kind == "journal":
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (c.draft_id,)).fetchone()
            assert draft["version"] == expected_draft_version and draft["status"] == "draft"
        else:
            assert conn.execute(
                "SELECT COUNT(*) FROM ai_runs WHERE event_id=? AND kind='ocr' AND status='succeeded'",
                (c.event["id"],),
            ).fetchone()[0] == 0


def cancel(c):
    if c.kind == "chat":
        c.chat.cancel(c.actor, c.conv["id"], c.job["id"], TurnCancel(reason="合成测试取消任务"))
    elif c.kind == "journal":
        c.queue.proposals.cancel(c.actor, c.job["id"], "合成测试取消分录任务")
    else:
        c.queue.cancel_job(c.actor, c.job["id"], "合成测试取消识别任务")


@pytest.mark.parametrize("kind", KINDS)
def test_A01_real_service_worker_sends_once_and_restart_preserves_receipt(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    complete(*launch(c))
    assert row(c)["status"] == "succeeded"
    assert received(c) == 1
    assert costs(c)[0]["state"] == "estimated"
    assert costs(c)[0]["input_tokens"] == 3 and costs(c)[0]["output_tokens"] == 4
    complete(*launch(c))
    assert received(c) == 1 and len(costs(c)) == 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ("pause", "revoke"))
def test_A02_A09_before_send_pause_or_revocation_prevents_call(attempt_case, kind, change):
    c = attempt_case
    prepare(c, kind)
    if change == "pause":
        c.intelligence.company_settings(c.manager, c.company["id"], enabled=False)
    else:
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
                         (c.company["id"], c.actor.user_id))
            conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?",
                         (c.book["id"], c.actor.user_id))
    complete(*launch(c))
    assert row(c)["status"] == "stale"
    assert received(c) == 0 and costs(c) == []
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
def test_A03_crash_after_real_claim_before_sending_recovers_once(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    process, _ = launch(c, before_http_crash="after_claim")
    process.join(30)
    assert process.exitcode == 71
    assert row(c)["status"] == "running" and received(c) == 0 and costs(c) == []
    expire(c)
    complete(*launch(c))
    assert row(c)["status"] == "succeeded"
    assert row(c)["attempt_count"] == 2 and received(c) == 1
    assert len(costs(c)) == 1 and costs(c)[0]["state"] == "estimated"


@pytest.mark.parametrize("kind", KINDS)
def test_A04_crash_after_durable_send_marker_never_replays(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    process, _ = launch(c, before_http_crash=True)
    process.join(30)
    assert process.exitcode == 73
    assert received(c) == 0 and costs(c)[0]["state"] == "sent"
    expire(c)
    complete(*launch(c))
    assert row(c)["status"] == "interrupted"
    assert row(c)["attempt_count"] == 1 and received(c) == 0
    assert costs(c)[0]["state"] in {"sent", "uncertain"}
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("hard_crash", (False, True))
def test_A05_received_disconnect_or_process_death_then_independent_restart(
    attempt_case, kind, hard_crash
):
    c = attempt_case
    prepare(c, kind)
    c.supplier.mode = "gate" if hard_crash else "disconnect"
    process, result_path = launch(c)
    assert c.supplier.received.wait(20), "request did not reach loopback supplier"
    if hard_crash:
        process.terminate()
        process.join(10)
        assert not process.is_alive()
        expire(c)
        c.supplier.release.set()
    else:
        complete(process, result_path)
    c.supplier.mode = "success"
    complete(*launch(c))
    assert row(c)["status"] == "interrupted"
    assert row(c)["attempt_count"] == 1 and received(c) == 1
    assert len(costs(c)) == 1 and costs(c)[0]["state"] in {"sent", "uncertain"}
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
def test_A06_success_usage_committed_then_crash_before_publication_never_replays(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    process, _ = launch(c, before_http_crash="after_settlement")
    process.join(30)
    assert process.exitcode == 74
    assert row(c)["status"] == "running" and received(c) == 1
    assert costs(c)[0]["state"] == "estimated"
    expire(c)
    complete(*launch(c))
    assert row(c)["status"] == "interrupted"
    assert row(c)["attempt_count"] == 1 and received(c) == 1
    assert len(costs(c)) == 1 and costs(c)[0]["state"] == "estimated"
    assert costs(c)[0]["input_tokens"] == 3 and costs(c)[0]["output_tokens"] == 4
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
def test_A07_lost_command_response_replays_same_service_command_and_receipt(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    # The sender loses the first command response and the completed worker
    # acknowledgement. It resubmits the original body and key unchanged.
    complete(*launch(c))
    before = row(c)
    assert before["status"] == "succeeded" and received(c) == 1
    if kind == "chat":
        repeated = c.chat.enqueue(c.actor, c.conv["id"], c.command)["turn"]
        assert repeated["id"] == c.job["id"]
    elif kind == "ocr":
        repeated = c.queue.enqueue(c.actor, c.command)
        assert repeated["id"] == c.batch["id"]
        assert repeated["jobs"][0]["id"] == c.job["id"]
    else:
        repeated = c.business.handoff(c.actor, c.event["id"], c.command)
        assert repeated["source_id"] == c.handoff_receipt["source_id"]
        assert repeated["draft_id"] == c.handoff_receipt["draft_id"]
        assert repeated["automatic_job"]["id"] == c.job["id"]
    complete(*launch(c))
    after = row(c)
    assert after["id"] == before["id"] and after["attempt_count"] == 1
    if kind != "chat":
        assert after["current_run_id"] == before["current_run_id"]
    assert received(c) == 1 and len(costs(c)) == 1
    with c.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drafts WHERE status='approved'").fetchone()[0] == 0


@pytest.mark.parametrize("kind", KINDS)
def test_A08_cancel_before_send_has_no_supplier_call_or_cost(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    cancel(c)
    complete(*launch(c))
    assert row(c)["status"] == "cancelled"
    assert received(c) == 0 and costs(c) == []
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
def test_A08_cancel_after_send_preserves_usage_but_never_publishes(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    c.supplier.mode = "gate"
    process, result_path = launch(c)
    assert c.supplier.received.wait(20)
    cancel(c)
    c.supplier.release.set()
    complete(process, result_path)
    complete(*launch(c))
    assert row(c)["status"] == "cancelled" and received(c) == 1
    assert len(costs(c)) == 1 and costs(c)[0]["state"] == "estimated"
    assert costs(c)[0]["input_tokens"] == 3 and costs(c)[0]["output_tokens"] == 4
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
def test_A09_revocation_at_result_acceptance_preserves_cost_not_content(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    c.supplier.mode = "gate"
    process, result_path = launch(c)
    assert c.supplier.received.wait(20)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?",
                     (c.company["id"], c.actor.user_id))
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?",
                     (c.book["id"], c.actor.user_id))
    c.supplier.release.set()
    complete(process, result_path)
    complete(*launch(c))
    assert row(c)["status"] == "stale" and received(c) == 1
    assert costs(c)[0]["state"] == "estimated"
    assert costs(c)[0]["input_tokens"] == 3 and costs(c)[0]["output_tokens"] == 4
    unpublished(c)


@pytest.mark.parametrize("kind", KINDS)
def test_A10_current_fact_changes_while_reply_inflight_rejects_late_result(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    c.supplier.mode = "gate"
    process, result_path = launch(c)
    assert c.supplier.received.wait(20)
    if kind == "journal":
        old = c.ledger.get_draft(c.actors["accountant"], c.book["id"], c.draft_id)
        c.ledger.update_draft(c.actors["accountant"], c.book["id"], c.draft_id, DraftUpdate(
            expected_version=old["version"], description="会计已在等待期间修订摘要", lines=old["lines"],
        ))
    else:
        c.business.update(c.actors["employee"], c.event["id"], BusinessEventUpdate(
            expected_version=c.event["version"],
            payload={**c.event["payload"], "description": "申请人在等待期间更正业务摘要"},
        ))
    c.supplier.release.set()
    complete(process, result_path)
    complete(*launch(c))
    assert row(c)["status"] == "stale" and received(c) == 1
    assert costs(c)[0]["state"] == "estimated"
    unpublished(c, expected_draft_version=2 if kind == "journal" else 1)


@pytest.mark.parametrize("kind", KINDS)
def test_A11_two_independent_processes_race_real_claim_and_send_once(attempt_case, kind):
    c = attempt_case
    prepare(c, kind)
    c.supplier.mode = "gate"
    start = multiprocessing.get_context("spawn").Event()
    one = launch(c, shared_start=start)
    two = launch(c, shared_start=start)
    start.set()
    assert c.supplier.received.wait(20)
    c.supplier.release.set()
    complete(*one)
    complete(*two)
    assert row(c)["status"] == "succeeded" and row(c)["attempt_count"] == 1
    assert received(c) == 1 and len(costs(c)) == 1
    assert costs(c)[0]["state"] == "estimated"
