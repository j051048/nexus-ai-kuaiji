"""Owned loopback PG only: measured mixed reads/writes and two OS workers.

Synthetic data is retained in its disposable DB. No model/notification calls,
no schema changes, no production URLs, no DSNs in reports or command lines.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend")]

from scripts.ak05_postgres_gate import validate_config, assert_server_and_roles  # noqa: E402
from scripts.release_candidate import candidate_fingerprint  # noqa: E402
from tools.capacity.process_usage import process_usage  # noqa: E402


def distribution(values):
    values = sorted(values)
    if not values:
        raise ValueError("No measurements")
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 2),
        "p95_ms": round(values[max(0, (95 * len(values) + 99) // 100 - 1)], 2),
        "p99_ms": round(values[max(0, (99 * len(values) + 99) // 100 - 1)], 2),
        "max_ms": round(values[-1], 2),
    }


def require_complete(rows, expected, *, allow_shed=False):
    def acceptable(row):
        return row.get("ok") is True or (
            allow_shed
            and row.get("class") == "DatabaseCapacityError"
            and row.get("no_write") is True
        )

    if not rows or len(rows) != expected or not all(acceptable(row) for row in rows):
        raise ValueError("Incomplete or failed workload; never report it as passed")


def load_target(config, major):
    try:
        target = validate_config(config)
    except Exception:
        raise ValueError("Only an explicit isolated local test configuration is accepted") from None
    assert_server_and_roles(target, major)
    return target


def batch_services(config):
    from app.accounting.database import Database
    from app.accounting.service import AccountingService
    from app.business.service import BusinessService
    from app.ledgers.service import LedgersService
    from app.service_plans.service import ServicePlansService
    from app.monthly.batch import MonthlyBatchService
    from app.batch_ops.service import BatchOperationsService

    app, worker = Database(config["app"]), Database(config["worker"])
    accounting, business = AccountingService(app), BusinessService(app)
    monthly = MonthlyBatchService(
        app,
        accounting,
        LedgersService(app),
        ServicePlansService(app, accounting, business),
        business,
    )
    return app, worker, BatchOperationsService(worker, monthly)


def drain(config, batch_id, user_id, firm_id):
    from app.accounting.domain import Actor
    from app.batch_ops.worker import BatchWorker

    app, db, batches = batch_services(config)
    worker = BatchWorker(batches, worker_id="qa-process-" + uuid4().hex)
    actor = Actor(user_id, firm_id)
    durations = []
    deadline = time.monotonic() + 300
    try:
        while time.monotonic() < deadline:
            with app.transaction(actor=actor) as conn:
                active = conn.execute(
                    "SELECT count(*) FROM batch_operation_items WHERE batch_id=? AND status IN ('queued','running','retry_wait')",
                    (batch_id,),
                ).fetchone()[0]
            if not active:
                return {
                    "ok": True,
                    "pid": os.getpid(),
                    "tasks": len(durations),
                    "durations_ms": durations,
                    "pool": db.pool_snapshot(),
                    "resources": process_usage(),
                }
            start = time.perf_counter()
            if worker.run_once():
                durations.append((time.perf_counter() - start) * 1000)
            else:
                time.sleep(0.03)
        raise TimeoutError("Worker drain timed out")
    finally:
        worker.stop()
        if not worker.wait_for_idle(10):
            raise RuntimeError("Worker did not drain")
        db.close()
        app.close()


def run(config, major, out, *, companies=200, levels=(5, 20, 50), rounds=2, allow_shed=False):
    from tools.capacity import dcp10_capacity as existing
    from app.accounting.database import Database
    from app.accounting.service import AccountingService
    from app.accounting.models import ApprovalRequest, ExportRequest, ImportRequest
    import psycopg

    target = load_target(config, major)
    if not 2 <= companies <= 200 or not 1 <= rounds <= 20 or any(not 1 <= n <= 50 for n in levels):
        raise ValueError("Bounded local workload required")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    candidate = candidate_fingerprint(ROOT)
    report = {
        "schema": "qa-capacity-v1",
        "status": "failed",
        "candidate": candidate,
        "major": major,
        "companies": companies,
        "rounds": rounds,
        "acceptance_mode": "overload_measurement" if allow_shed else "all_requests_completed",
        "host": {"os": platform.system(), "cpu_count": os.cpu_count()},
        "phases": [],
        "resources_at_start": process_usage(),
    }
    db, monitor_stop, monitor_rows = Database(target["app"]), threading.Event(), []

    def monitor():
        with psycopg.connect(target["admin"], autocommit=True) as conn:
            while not monitor_stop.wait(0.1):
                row = conn.execute(
                    "SELECT count(*), count(*) FILTER (WHERE state='active'), count(*) FILTER (WHERE wait_event_type='Lock') FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"
                ).fetchone()
                monitor_rows.append(row)

    watcher = threading.Thread(target=monitor, daemon=True)
    try:
        # Reuse DCP10 fixture generation; all measured business writes use the
        # restricted application/worker logins, not this fixture-only admin.
        existing.APP_URL = target["app"]
        case = existing.seed(target["admin"], companies)
        service = AccountingService(db)
        watcher.start()
        for hotspot in (False, True):
            for concurrency in levels:
                count = max(companies, concurrency * 2) * rounds

                def operation(index):
                    book = case.companies[0 if hotspot else index % companies]["book_id"]
                    start = time.perf_counter()
                    kind = "import" if index % 4 == 0 else "read"
                    external_id = uuid4().hex
                    try:
                        if kind == "import":
                            result = service.import_sources(
                                case.manager,
                                book,
                                ImportRequest(
                                    source_name="QA合成负载",
                                    rows=[
                                        {
                                            "external_id": external_id,
                                            "date": "2026-10-01",
                                            "description": "合成办公费",
                                            "counterparty": "虚构供应商",
                                            "amount": "0.30",
                                            "direction": "expense",
                                            "evidence_ref": "合成证据索引",
                                        }
                                    ],
                                ),
                            )
                            assert result["imported"] == 1
                        else:
                            result = service.get_book(case.manager, book)
                            assert result["book"]["id"] == book
                        return {
                            "ok": True,
                            "kind": kind,
                            "ms": (time.perf_counter() - start) * 1000,
                        }
                    except Exception as error:
                        return {
                            "ok": False,
                            "kind": kind,
                            "ms": (time.perf_counter() - start) * 1000,
                            "class": type(error).__name__,
                            "admission_reason": getattr(error, "reason", None),
                            "external_id": external_id,
                            "book": book,
                        }

                started = time.perf_counter()
                pool_before = db._postgres_pool().get_stats()
                with ThreadPoolExecutor(concurrency) as pool:
                    rows = list(pool.map(operation, range(count)))
                for row in rows:
                    if row.get("class") == "DatabaseCapacityError":
                        with db.transaction(actor=case.manager) as conn:
                            assert (
                                conn.execute(
                                    "SELECT count(*) FROM sources WHERE book_id=? AND external_id=?",
                                    (row["book"], row["external_id"]),
                                ).fetchone()[0]
                                == 0
                            )
                        row["no_write"] = True
                phase = {
                    "hotspot": hotspot,
                    "clients": concurrency,
                    "requests": count,
                    "seconds": round(time.perf_counter() - started, 3),
                    "errors": sum(not r["ok"] for r in rows),
                    "admission_rejections": sum(r.get("no_write") is True for r in rows),
                    "slo_all_requests_completed": all(r["ok"] for r in rows),
                    "error_classes": sorted({r["class"] for r in rows if not r["ok"]}),
                    "admission_reasons": sorted(
                        {r["admission_reason"] for r in rows if r.get("admission_reason")}
                    ),
                    "latency": {
                        kind: distribution([r["ms"] for r in rows if r["kind"] == kind and r["ok"]])
                        for kind in ("read", "import")
                    },
                    "pool": db.pool_snapshot(),
                    # Local benchmark only; don't expose cross-tenant request
                    # counters through the application's public pool snapshot.
                    "pool_delta": {
                        key: db._postgres_pool().get_stats().get(key, 0) - pool_before.get(key, 0)
                        for key in (
                            "requests_num",
                            "requests_queued",
                            "requests_wait_ms",
                            "requests_errors",
                            "returns_bad",
                            "connections_lost",
                        )
                    },
                    "transactions": db.metrics.snapshot(firm_id=case.firm),
                }
                report["phases"].append(phase)
                require_complete(rows, count, allow_shed=allow_shed)
        # After load, verify review/export invariants on the actual imported data.
        book = case.companies[0]["book_id"]
        service.suggestions(case.manager, book)
        drafts = service.get_book(case.manager, book)["drafts"]
        assert drafts
        from app.accounting.domain import AccountingError, Actor

        command = ExportRequest(draft_ids=[drafts[0]["id"]], idempotency_key=uuid4().hex)
        try:
            service.export(case.manager, book, command)
            raise AssertionError("Unreviewed export allowed")
        except AccountingError as error:
            assert error.status == 409
        reviewer = Actor("qa-review-" + uuid4().hex, case.firm)
        with psycopg.connect(target["admin"]) as conn:
            conn.execute(
                "INSERT INTO accounting.users(id,name) VALUES(%s,'合成独立复核员')",
                (reviewer.user_id,),
            )
            conn.execute(
                "INSERT INTO accounting.memberships VALUES(%s,%s,'reviewer')",
                (case.firm, reviewer.user_id),
            )
            conn.execute(
                "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'reviewer')",
                (book, case.firm, reviewer.user_id),
            )
        service.approve(
            reviewer, book, drafts[0]["id"], ApprovalRequest(expected_version=drafts[0]["version"])
        )
        receipt = service.export(case.manager, book, command)
        assert service.export(case.manager, book, command) == receipt
        with db.transaction(actor=Actor("absent-user", "absent-firm")) as conn:
            assert conn.execute("SELECT id FROM books WHERE id=?", (book,)).fetchall() == []
        report["review_export_isolation"] = True
        app, worker_db, batches = batch_services(target)
        try:
            batch = batches.create_monthly_open(
                case.manager, existing.batch_request(case, uuid4().hex)
            )
            started = time.perf_counter()
            with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
                workers = list(
                    pool.map(
                        drain, [target] * 2, [batch["id"]] * 2, [case.owner] * 2, [case.firm] * 2
                    )
                )
            require_complete(workers, 2)
            assert len({row["pid"] for row in workers}) == 2
            check = existing.conservation(app, case, batch)
            assert check["items_by_status"] == {"succeeded": companies}, check
            assert check["new_books"] == companies and check["duplicate_books"] == 0
            report["workers"] = {
                "seconds": round(time.perf_counter() - started, 3),
                "processes": workers,
                "conservation": check,
            }
        finally:
            worker_db.close()
            app.close()
        assert candidate_fingerprint(ROOT) == candidate, "Candidate changed during workload"
        report["status"] = "passed"
    except Exception as error:
        report["diagnostic"] = {"class": type(error).__name__}
        raise
    finally:
        monitor_stop.set()
        if watcher.is_alive():
            watcher.join(5)
        db.close()
        report["pg_samples"] = {
            "count": len(monitor_rows),
            "max_connections": max((r[0] for r in monitor_rows), default=0),
            "max_active": max((r[1] for r in monitor_rows), default=0),
            "max_lock_waiters": max((r[2] for r in monitor_rows), default=0),
        }
        report["resources_at_end"] = process_usage()
        report["database_cpu_measured"] = False
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--major", type=int, choices=[16, 17], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--companies", type=int, default=200)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--allow-shed",
        action="store_true",
        help="Explicit overload study, not successful-request capacity acceptance",
    )
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
        run(
            config,
            args.major,
            args.out,
            companies=args.companies,
            rounds=args.rounds,
            allow_shed=args.allow_shed,
        )
        print(json.dumps({"status": "passed", "report": str(Path(args.out) / "report.json")}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "class": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
