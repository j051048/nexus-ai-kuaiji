"""Real UnifiedWorker OS processes, PostgreSQL leases and exact fake receipts."""

import json
import os
from pathlib import Path
import queue as messages
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import pytest

from app.jobs.requests import BatchRequest
from app.operations.meter import MeterService
from app.operations.pricing import Pricing, Rate
from app.operations.requests import PolicyRequest
from app.operations.service import OperationsService
from test_ak01_postgres import (
    ADMIN_URL,
    APP_URL,
    WORKER_URL,
    business_case,
    business_postgres,
    pg_ai,
    pg_queue,
    pg_stage7,
    prepare,
)
from test_queue_workflow import create_other_event


pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1" or not WORKER_URL,
    reason="requires explicit disposable AK05 PG target",
)


@pytest.fixture(autouse=True)
def isolated_target():
    targets = [urlsplit(value) for value in (ADMIN_URL, APP_URL, WORKER_URL)]
    assert len({(t.hostname, t.port, t.path) for t in targets}) == 1
    assert targets[0].hostname == "127.0.0.1" and targets[0].port != 5432
    assert targets[0].path.startswith("/ak01_local_ak05_")


def rows(case, sql, parameters=()):
    with case.psycopg.connect(ADMIN_URL) as conn:
        return conn.execute(sql, parameters).fetchall()


def await_condition(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("isolated PostgreSQL condition timed out")


def line(process):
    result = messages.Queue()
    reader = threading.Thread(target=lambda: result.put(process.stdout.readline()), daemon=True)
    reader.start()
    return result.get(timeout=15).strip()


def send(process, value):
    process.stdin.write(value + "\n")
    process.stdin.flush()


def spawn(case, tmp_path, action):
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir(exist_ok=True)
    payload = {
        "root": str(Path(__file__).resolve().parents[2]),
        "worker_url": WORKER_URL,
        "storage": str(case.storage.root),
        "document": case.provider.document,
        "receipts": str(receipt_dir),
        "release": str(tmp_path / "release"),
        "action": action,
    }
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("ak05_pg_process_child.py"))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        env=env,
    )
    send(process, json.dumps(payload))
    ready = json.loads(line(process))
    assert ready["ready"] is True
    process.worker_pid = ready["pid"]
    return process


def finish(process, expected=0):
    output, error = process.communicate(timeout=30)
    assert process.returncode == expected, error.replace(WORKER_URL, "[isolated worker]")
    return json.loads(output) if output.strip() else None


def cleanup(processes):
    for process in processes:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                process.kill()
        process.communicate(timeout=10)


def receipts(tmp_path):
    return sum(
        path.read_text().count("received\n") for path in (tmp_path / "receipts").glob("*.txt")
    )


def metered_task(request):
    case, task_id, firm_id = prepare(request, "ocr")
    operations = OperationsService(
        case.db, MeterService(case.db, pricing=Pricing(ocr=Rate("2.000000", "4.000000")))
    )
    current = operations.policy(case.manager, case.company["id"])
    operations.policy(
        case.manager,
        case.company["id"],
        PolicyRequest(
            expected_version=current["version"],
            quota_enabled=True,
            monthly_limit="1.00",
            ocr_reserve="0.10",
            proposal_reserve="0.10",
        ),
    )
    return case, task_id, firm_id


@pytest.mark.parametrize("companies", [1, 2])
def test_unified_processes_claim_without_duplicate_attempt_or_charge(request, tmp_path, companies):
    case, first, _ = metered_task(request)
    ids = [first]
    if companies == 2:
        case.intelligence.company_settings(case.manager, case.other_company["id"], enabled=True)
        event = create_other_event(case)
        job = case.queue.enqueue(
            case.actors["outsider"],
            BatchRequest(
                company_id=case.other_company["id"],
                items=[
                    {
                        "event_id": event["id"],
                        "attachment_id": event["attachments"][0]["id"],
                        "expected_version": event["version"],
                    }
                ],
                idempotency_key="ak05-second-company",
            ),
        )["jobs"][0]
        ids.append(job["id"])
    processes = []
    try:
        processes = [spawn(case, tmp_path, "hold") for _ in range(2)]
        for process in processes:
            send(process, "go")
        running = await_condition(
            lambda: (
                rows(
                    case,
                    "SELECT company_id FROM accounting.ocr_jobs WHERE id=ANY(%s) AND status='running'",
                    (ids,),
                )
                if receipts(tmp_path) == companies
                else None
            )
        )
        assert len(running) == companies and len({r[0] for r in running}) == companies
        (tmp_path / "release").touch()
        reports = [finish(p) for p in processes]
        assert len({r["pid"] for r in reports}) == 2
        assert all(r["drained"] and r["closed"] for r in reports)
        assert receipts(tmp_path) == companies
        assert (
            rows(
                case,
                "SELECT status,attempt_count FROM accounting.ocr_jobs WHERE id=ANY(%s)",
                (ids,),
            )
            == [("succeeded", 1)] * companies
        )
        assert rows(
            case, "SELECT count(*) FROM accounting.ocr_attempts WHERE job_id=ANY(%s)", (ids,)
        ) == [(companies,)]
        costs = rows(
            case,
            "SELECT c.state,c.estimated_micro,c.input_tokens,c.output_tokens FROM accounting.model_costs c JOIN accounting.ocr_jobs j ON j.current_run_id=c.id WHERE j.id=ANY(%s)",
            (ids,),
        )
        assert costs == [("estimated", 16, 2, 3)] * companies
    finally:
        cleanup(processes)


@pytest.mark.parametrize(
    "stage", ["before_marker", "after_marker", "after_receipt", "after_commit"]
)
def test_expired_process_lease_recovers_without_replaying_unknown_send(request, tmp_path, stage):
    case, job_id, _ = metered_task(request)
    processes = []
    try:
        crashed = spawn(case, tmp_path, stage)
        processes.append(crashed)
        send(crashed, "go")
        finish(crashed, expected=79)
        old_token = rows(
            case, "SELECT lease_token FROM accounting.ocr_jobs WHERE id=%s", (job_id,)
        )[0][0]
        assert receipts(tmp_path) == int(stage in {"after_receipt", "after_commit"})
        if stage != "after_commit":
            with case.psycopg.connect(ADMIN_URL) as conn:
                conn.execute(
                    "UPDATE accounting.ocr_jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE id=%s",
                    (job_id,),
                )
        restarted = spawn(case, tmp_path, "work")
        processes.append(restarted)
        send(restarted, "go")
        report = finish(restarted)
        assert report["pid"] == restarted.worker_pid != crashed.worker_pid
        state, count, run_id = rows(
            case,
            "SELECT status,attempt_count,current_run_id FROM accounting.ocr_jobs WHERE id=%s",
            (job_id,),
        )[0]
        if stage == "before_marker":
            assert (state, count, receipts(tmp_path)) == ("succeeded", 2, 1)
            expected = ("estimated", 16, 2, 3)
        elif stage == "after_commit":
            assert (state, count, receipts(tmp_path)) == ("succeeded", 1, 1)
            expected = ("estimated", 16, 2, 3)
        else:
            assert (state, count, receipts(tmp_path)) == (
                "interrupted",
                1,
                int(stage == "after_receipt"),
            )
            expected = (None, None, None, None)
        cost = rows(
            case,
            "SELECT state,estimated_micro,input_tokens,output_tokens FROM accounting.model_costs WHERE id=%s",
            (run_id,),
        )[0]
        if stage in {"before_marker", "after_commit"}:
            assert cost == expected
        else:
            assert cost[0] in {"sent", "uncertain"} and cost[1:] == expected[1:]
            assert (
                rows(
                    case, "SELECT reserved_micro FROM accounting.model_costs WHERE id=%s", (run_id,)
                )[0][0]
                > 0
            )
        assert rows(
            case,
            "SELECT count(*) FROM accounting.model_costs c JOIN accounting.ai_runs r ON r.id=c.id JOIN accounting.ocr_jobs j ON j.event_id=r.event_id WHERE j.id=%s",
            (job_id,),
        ) == [(1,)]
        assert rows(
            case, "SELECT count(*) FROM accounting.ocr_attempts WHERE job_id=%s", (job_id,)
        ) == [(count,)]
        with case.worker.queue.worker_transaction() as conn:
            assert not conn.execute(
                "SELECT accounting.worker_renew_ocr_lease(?,?,180)", (job_id, old_token)
            ).fetchone()[0]
            assert not conn.execute(
                "SELECT accounting.worker_finish_ocr_job(?,?,'succeeded','','',?,0)",
                (job_id, old_token, run_id),
            ).fetchone()[0]
        assert rows(
            case,
            "SELECT count(*) FROM accounting.ai_proposal_applications WHERE run_id=%s",
            (run_id,),
        ) == [(0,)]
    finally:
        cleanup(processes)


def test_process_shutdown_retains_pg_until_real_work_and_monitors_drain(request, tmp_path):
    case, job_id, _ = metered_task(request)
    processes = []
    try:
        process = spawn(case, tmp_path, "daemon")
        processes.append(process)
        send(process, "go")
        await_condition(lambda: receipts(tmp_path) == 1)
        send(process, "stop")
        assert json.loads(line(process)) == {"drained": False, "closed": False, "healthy": True}
        with case.psycopg.connect(ADMIN_URL) as conn:
            assert conn.execute(
                "SELECT status FROM accounting.ocr_jobs WHERE id=%s", (job_id,)
            ).fetchone() == ("running",)
        (tmp_path / "release").touch()
        send(process, "finish")
        report = finish(process)
        assert report["drained"] and report["closed"]
        assert receipts(tmp_path) == 1
        assert rows(
            case, "SELECT status,attempt_count FROM accounting.ocr_jobs WHERE id=%s", (job_id,)
        ) == [("succeeded", 1)]
        assert rows(
            case,
            "SELECT state,inflight_count FROM accounting.worker_heartbeats WHERE worker_id=%s",
            ("ak05-pg-process-" + str(report["pid"]),),
        ) == [("stopped", 0)]
    finally:
        cleanup(processes)
