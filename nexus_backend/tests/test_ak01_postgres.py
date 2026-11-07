"""AK01 process boundaries against an explicitly opted-in, disposable local PG.

No live supplier is contacted. Child processes use a deterministic fixture
provider and an fsynced receipt counter before simulating a process crash.
Existing PostgreSQL service tests cover authorization, billing and exports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

import pytest

from app.jobs.requests import BatchRequest
from test_intelligence_workflow import create_event, extract, handoff
from test_queue_worker_postgres import (
    ADMIN_URL,
    APP_URL,
    WORKER_URL,
    business_case,
    business_postgres,
    pg_ai,
    pg_queue,
)
from test_stage7_proposal_postgres import pg_stage7
from test_stage15_assistant_meter_postgres import billed_chat
from test_stage15_conversations_postgres import chat_pg, enqueue, make_thread
from pg_queue_clock import wait_until_due


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK01_ISOLATED_TARGET") != "1"
    or not ADMIN_URL
    or not APP_URL
    or not WORKER_URL,
    reason="requires an explicitly opted-in disposable AK01 local PostgreSQL",
)


@pytest.fixture(autouse=True)
def require_isolated_target():
    for value in (ADMIN_URL, APP_URL, WORKER_URL):
        parsed = urlsplit(value)
        assert parsed.hostname == "127.0.0.1"
        assert parsed.path.startswith("/ak01_local_")
        assert parsed.port and parsed.port != 5432
    assert (
        len(
            {
                urlsplit(value).netloc.rsplit("@", 1)[-1]
                for value in (ADMIN_URL, APP_URL, WORKER_URL)
            }
        )
        == 1
    )


# Passed via stdin, never via the command line. A new interpreter and a new
# connection prevent a test from accidentally proving only an in-process lock.
CHILD = r"""
import json, os, pathlib, socket, sys
from urllib.parse import urlsplit
p=json.loads(sys.stdin.readline())
target=urlsplit(p["worker_url"])
assert target.hostname == "127.0.0.1"
assert target.path.startswith("/ak01_local_") and target.port != 5432
root=pathlib.Path(p["root"])
sys.path[:0]=[str(root/"nexus_backend"),str(root/"nexus_backend/tests"),str(root)]
import psycopg
real_connect=psycopg.connect
def connect(url,*args,**kwargs):
    assert url == p["worker_url"], "child rejected another database"
    return real_connect(url,*args,**kwargs)
psycopg.connect=connect
def deny_network(*args,**kwargs):
    raise RuntimeError("AK01 fixture providers may not open network connections")
socket.socket.connect=deny_network
socket.create_connection=deny_network
if p["action"] == "claim":
    with psycopg.connect(p["worker_url"]) as conn:
        conn.execute("SET LOCAL ROLE accounting_worker")
        print("ready",flush=True)
        assert sys.stdin.readline().strip()=="go"
        if p["kind"] == "chat":
            fetched=conn.execute("SELECT row_to_json(t) FROM accounting.assistant_claim(%s,180) t",(p["name"],)).fetchone()
            row=fetched[0] if fetched else None
        else:
            row=conn.execute("SELECT accounting.claim_processing_job(%s,180,2)",(p["name"],)).fetchone()[0]
    print(json.dumps(row,default=str),flush=True)
else:
    from app.accounting.database import Database
    from app.accounting.service import AccountingService
    from app.business.service import BusinessService
    from app.business.storage import AttachmentStore
    from app.operations.meter import MeterService
    db=Database(p["worker_url"])
    def receipt():
        with open(p["receipt"],"ab") as stream:
            stream.write(b"received\n")
            stream.flush()
            os.fsync(stream.fileno())
        if p["action"] == "crash":
            os._exit(79)
    if p["kind"] == "chat":
        from test_stage15_conversations import SyntheticChatProvider
        from app.assistant.service import AssistantService
        from app.assistant.worker import AssistantWorker
        from app.assistant.meter import AssistantMeter
        from app.operations.pricing import Pricing,Rate
        provider=SyntheticChatProvider()
        provider.hook=receipt
        service=AssistantService(db,AccountingService(db),BusinessService(db),provider=provider)
        pricing=Pricing(ocr=Rate("2.000000","4.000000"),proposal=Rate("2.000000","4.000000"),chat=Rate("2.000000","4.000000"))
        worker=AssistantWorker(service,provider,AssistantMeter(db,MeterService(db,pricing=pricing)))
    else:
        from test_intelligence_workflow import FakeProvider
        from app.intelligence.service import IntelligenceService
        from app.jobs.service import QueueService
        from app.jobs.worker import QueueWorker
        provider=FakeProvider(document=p["document"])
        provider.extract_hook=receipt
        provider.proposal_hook=receipt
        service=IntelligenceService(db,AttachmentStore(p["storage"]),provider)
        worker=QueueWorker(QueueService(db,service),worker_id=p["name"])
    if p["action"] == "crash" and p.get("fault_stage") == "after_marker":
        # The durable worker/meter marker commits before the provider method.
        # This crash is before the fake supplier receipt, so absence is real.
        def die_before_supplier(*args,**kwargs):
            os._exit(79)
        if p["kind"] == "chat":
            provider.reply=die_before_supplier
        else:
            provider.extract_invoice=die_before_supplier
            provider.suggest_journal=die_before_supplier
    result=worker.run_once()
    db.close()
    print(json.dumps({"result":result}),flush=True)
"""


def prepare(request, kind):
    if kind == "chat":
        case = request.getfixturevalue("billed_chat")
        thread = make_thread(case)
        task = enqueue(case, thread)
        wait_until_due(case.source.psycopg.connect, ADMIN_URL, "assistant_turns", [task["id"]])
        return case, task["id"], case.source.owner.firm_id
    case = request.getfixturevalue("pg_stage7")
    event = create_event(case)
    if kind == "ocr":
        batch = case.queue.enqueue(
            case.actors["employee"],
            BatchRequest(
                company_id=case.company["id"],
                items=[
                    {
                        "event_id": event["id"],
                        "attachment_id": event["attachments"][0]["id"],
                        "expected_version": event["version"],
                    }
                ],
                idempotency_key="ak01-ocr-" + event["id"],
            ),
        )
        wait_until_due(case.psycopg.connect, ADMIN_URL, "ocr_jobs", [batch["jobs"][0]["id"]])
        return case, batch["jobs"][0]["id"], case.manager.firm_id
    assert extract(case, event)["status"] == "succeeded"
    result = handoff(case, event)
    task_id = result["automatic_job"]["id"]
    wait_until_due(case.psycopg.connect, ADMIN_URL, "proposal_jobs", [task_id])
    assert case.queue.proposals.get(case.actors["accountant"], task_id)["status"] == "queued"
    return case, task_id, case.manager.firm_id


def child_payload(case, kind, tmp_path, *, action, name):
    payload = {
        "root": str(Path(__file__).resolve().parents[2]),
        "worker_url": WORKER_URL,
        "kind": kind,
        "action": action,
        "name": name,
        "receipt": str(tmp_path / "provider-received.txt"),
    }
    if kind != "chat":
        payload.update(document=case.provider.document, storage=str(case.storage.root))
    return payload


def child(payload):
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        env=env,
    )
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()
    return process


def safe_error(output):
    """Child errors must never expose even disposable test DSNs."""
    return output.replace(WORKER_URL, "[isolated worker]")


@pytest.mark.parametrize("kind", ["ocr", "proposal", "chat"])
def test_two_process_claims_share_pg_mutex_without_duplicate_attempt(request, tmp_path, kind):
    case, task_id, firm_id = prepare(request, kind)
    processes = [
        child(child_payload(case, kind, tmp_path, action="claim", name=f"ak01-claim-{index}"))
        for index in range(2)
    ]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        claims = []
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0, safe_error(error)
            claims.append(json.loads(output))
        claimed = [row for row in claims if row]
        assert len(claimed) == 1
        assert claimed[0]["id"] == task_id
        assert claimed[0]["firm_id"] == firm_id
        assert len({process.pid for process in processes}) == 2
        import psycopg

        with psycopg.connect(ADMIN_URL) as conn:
            table = "assistant_turns" if kind == "chat" else kind + "_jobs"
            assert conn.execute(
                f"SELECT status FROM accounting.{table} WHERE id=%s", (task_id,)
            ).fetchone() == ("running",)
            if kind != "chat":
                assert (
                    conn.execute(
                        f"SELECT count(*) FROM accounting.{kind}_attempts WHERE job_id=%s",
                        (task_id,),
                    ).fetchone()[0]
                    == 1
                )
        assert not (tmp_path / "provider-received.txt").exists()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("kind", ["ocr", "proposal", "chat"])
@pytest.mark.parametrize("fault_stage", ["after_marker", "after_receipt"])
def test_process_death_preserves_exposure_and_does_not_resend(request, tmp_path, kind, fault_stage):
    case, task_id, _ = prepare(request, kind)
    payload = child_payload(case, kind, tmp_path, action="crash", name="ak01-crashed")
    payload["fault_stage"] = fault_stage
    first = child(payload)
    try:
        _, error = first.communicate(timeout=45)
        assert first.returncode == 79, safe_error(error)
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate(timeout=10)
    receipt = tmp_path / "provider-received.txt"
    expected_receipts = ["received"] if fault_stage == "after_receipt" else []
    assert (receipt.read_text().splitlines() if receipt.exists() else []) == expected_receipts

    import psycopg

    table = "assistant_turns" if kind == "chat" else kind + "_jobs"
    with psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute(
            f"SELECT status FROM accounting.{table} WHERE id=%s", (task_id,)
        ).fetchone() == ("running",)
        # Deterministic fault injection only in this newly created isolated DB.
        conn.execute(
            f"UPDATE accounting.{table} SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s",
            (task_id,),
        )
    if kind != "chat":
        case.worker.recover()
    restarted = child(child_payload(case, kind, tmp_path, action="resume", name="ak01-restarted"))
    try:
        _, error = restarted.communicate(timeout=45)
        assert restarted.returncode == 0, safe_error(error)
    finally:
        if restarted.poll() is None:
            restarted.kill()
            restarted.communicate(timeout=10)
    assert (receipt.read_text().splitlines() if receipt.exists() else []) == expected_receipts
    assert first.pid != restarted.pid
    with psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute(
            f"SELECT status,{('id' if kind == 'chat' else 'current_run_id')} FROM accounting.{table} WHERE id=%s",
            (task_id,),
        ).fetchone()
        assert row[0] == "interrupted"
        cost = conn.execute(
            "SELECT state,estimated_micro,input_tokens,output_tokens FROM accounting.model_costs WHERE id=%s",
            (row[1],),
        ).fetchone()
        assert cost is not None
        assert cost[0] in {"sent", "uncertain"}
        assert cost[1:] == (None, None, None)
        if kind == "chat":
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'",
                    (task_id,),
                ).fetchone()[0]
                == 0
            )
        else:
            assert (
                conn.execute(
                    f"SELECT count(*) FROM accounting.{kind}_attempts WHERE job_id=%s",
                    (task_id,),
                ).fetchone()[0]
                == 1
            )


@pytest.mark.parametrize("kind", ["ocr", "proposal", "chat"])
@pytest.mark.parametrize("usage_kind", ["known", "zero", "missing"])
def test_pg_429_billing_survives_without_automatic_new_attempt(request, kind, usage_kind):
    """Exercise actual services and worker logins, not synthetic SQL cost rows."""
    from app.intelligence.models import ProviderUsage
    from app.intelligence.provider import ProviderError
    from app.operations.meter import MeterService
    from app.operations.pricing import Pricing, Rate
    import psycopg

    case, task_id, _ = prepare(request, kind)
    error = ProviderError("rate_limited", "合成 429 响应", status=429, retryable=True)
    error.llm_attempted = True
    error.usage = (
        ProviderUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
        if usage_kind == "known"
        else ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        if usage_kind == "zero"
        else None
    )
    if kind == "chat":
        case.provider.error = error
    else:
        case.provider.failure = error
        pricing = Pricing(
            ocr=Rate("2.000000", "4.000000"),
            proposal=Rate("2.000000", "4.000000"),
        )
        case.worker.queue.intelligence.meter = MeterService(case.worker.queue.db, pricing=pricing)
    assert case.worker.run_once()
    assert not case.worker.run_once()
    calls = (
        case.provider.calls
        if kind == "chat"
        else case.provider.extract_calls
        if kind == "ocr"
        else case.provider.proposal_calls
    )
    assert calls == 1
    table = "assistant_turns" if kind == "chat" else kind + "_jobs"
    with psycopg.connect(ADMIN_URL) as conn:
        task = conn.execute(
            f"SELECT status,{('id' if kind == 'chat' else 'current_run_id')} FROM accounting.{table} WHERE id=%s",
            (task_id,),
        ).fetchone()
        assert task[0] == "failed", "A received 429 must not enter automatic retry_wait"
        cost = conn.execute(
            "SELECT state,estimated_micro,input_tokens,output_tokens FROM accounting.model_costs WHERE id=%s",
            (task[1],),
        ).fetchone()
        assert cost is not None
        if usage_kind == "known":
            assert cost == ("estimated", 16, 2, 3)
        elif usage_kind == "zero":
            assert cost == ("estimated", 0, 0, 0)
        else:
            assert cost == ("uncertain", None, None, None)
        if kind != "chat":
            assert (
                conn.execute(
                    f"SELECT count(*) FROM accounting.{kind}_attempts WHERE job_id=%s",
                    (task_id,),
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.ai_proposal_applications WHERE run_id=%s",
                    (task[1],),
                ).fetchone()[0]
                == 0
            )
        else:
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'",
                    (task_id,),
                ).fetchone()[0]
                == 0
            )


@pytest.mark.parametrize("kind", ["ocr", "proposal", "chat"])
@pytest.mark.parametrize("action", ["cancel_before", "cancel_during", "revoke_during"])
def test_pg_cancel_and_revoke_preserve_usage_without_publishing(request, kind, action):
    from app.accounting.directory import DirectoryService
    from app.assistant.models import TurnCancel
    from app.intelligence.models import ProviderUsage
    from app.operations.meter import MeterService
    from app.operations.pricing import Pricing, Rate
    import psycopg

    case, task_id, _ = prepare(request, kind)
    pricing = Pricing(
        ocr=Rate("2.000000", "4.000000"),
        proposal=Rate("2.000000", "4.000000"),
    )
    if kind != "chat":
        case.provider.usage = ProviderUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
        case.worker.queue.intelligence.meter = MeterService(case.worker.queue.db, pricing=pricing)

    def cancel():
        if kind == "chat":
            with psycopg.connect(ADMIN_URL) as conn:
                conversation_id = conn.execute(
                    "SELECT conversation_id FROM accounting.assistant_turns WHERE id=%s",
                    (task_id,),
                ).fetchone()[0]
            case.service.cancel(
                case.actor, conversation_id, task_id, TurnCancel(reason="合成在途取消验证")
            )
        elif kind == "ocr":
            case.queue.cancel_job(case.actors["finance"], task_id, "合成在途取消验证")
        else:
            case.queue.proposals.cancel(case.actors["accountant"], task_id, "合成在途取消验证")

    def revoke():
        if kind == "chat":
            DirectoryService(case.db).revoke_book_member(
                case.source.owner, case.book, case.actor.user_id
            )
        else:
            actor = case.actors["employee"] if kind == "ocr" else case.actors["finance"]
            case.directory.revoke_company_member(case.manager, case.company["id"], actor.user_id)

    if action == "cancel_before":
        cancel()
    else:
        hook = cancel if action == "cancel_during" else revoke
        if kind == "chat":
            case.provider.hook = hook
        elif kind == "ocr":
            case.provider.extract_hook = hook
        else:
            case.provider.proposal_hook = hook
    case.worker.run_once()
    assert not case.worker.run_once()
    calls = (
        case.provider.calls
        if kind == "chat"
        else case.provider.extract_calls
        if kind == "ocr"
        else case.provider.proposal_calls
    )
    assert calls == (0 if action == "cancel_before" else 1)
    table = "assistant_turns" if kind == "chat" else kind + "_jobs"
    with psycopg.connect(ADMIN_URL) as conn:
        task = conn.execute(
            f"SELECT status,{('id' if kind == 'chat' else 'current_run_id')} FROM accounting.{table} WHERE id=%s",
            (task_id,),
        ).fetchone()
        assert task[0] == ("stale" if action == "revoke_during" else "cancelled")
        cost = conn.execute(
            "SELECT state,estimated_micro,input_tokens,output_tokens FROM accounting.model_costs WHERE id=%s",
            (task[1],),
        ).fetchone()
        if action == "cancel_before":
            assert cost is None
        else:
            assert cost == ("estimated", 16, 2, 3)
        if kind == "chat":
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.assistant_messages WHERE turn_id=%s AND role='assistant'",
                    (task_id,),
                ).fetchone()[0]
                == 0
            )
        elif task[1] is not None:
            assert conn.execute(
                "SELECT status,result_json FROM accounting.ai_runs WHERE id=%s", (task[1],)
            ).fetchone() == ("stale", None)
            assert (
                conn.execute(
                    "SELECT count(*) FROM accounting.ai_proposal_applications WHERE run_id=%s",
                    (task[1],),
                ).fetchone()[0]
                == 0
            )
