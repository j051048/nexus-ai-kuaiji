"""DCP10 capacity harness: batch period opening for a bookkeeping firm.

Local-only. Seeds a synthetic firm with N client companies on an isolated
PostgreSQL cluster, runs one monthly-open batch through the real worker loop,
and reports throughput plus conservation checks. No production database, no
real customers, no external calls.

Usage (from the repository root, with the project venv):

    python tools/capacity/dcp10_capacity.py seed --admin DSN --app DSN --companies 50
    python tools/capacity/dcp10_capacity.py run  --admin DSN --app DSN --companies 50 \
        --out tmp_test/dcp10-50.json
    python tools/capacity/dcp10_capacity.py lease --admin DSN --app DSN --companies 10
    python tools/capacity/dcp10_capacity.py cancel --admin DSN --app DSN --companies 10
"""

import argparse
import json
from datetime import datetime
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nexus_backend"))

import psycopg  # noqa: E402

from app.accounting.database import Database  # noqa: E402
from app.accounting.directory import DirectoryService  # noqa: E402
from app.accounting.domain import Actor  # noqa: E402
from app.accounting.models import BookCreate, CompanyCreate  # noqa: E402
from app.accounting.service import AccountingService  # noqa: E402
from app.batch_ops.models import CreateMonthlyOpenBatch  # noqa: E402
from app.batch_ops.service import BatchOperationsService  # noqa: E402
from app.batch_ops.worker import BatchWorker  # noqa: E402
from app.business.service import BusinessService  # noqa: E402
from app.ledgers.service import LedgersService  # noqa: E402
from app.monthly.batch import MonthlyBatchService  # noqa: E402
from app.service_plans.service import ServicePlansService  # noqa: E402

STAMP = "DCP10"


def connect_admin(dsn):
    return psycopg.connect(dsn, autocommit=True)


def seed(admin_dsn, companies):
    """Create one firm with N clients, each with a 2026-10 book."""
    suffix = uuid4().hex[:8]
    firm = f"dcp10-firm-{suffix}"
    owner = f"dcp10-owner-{suffix}"
    with connect_admin(admin_dsn) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name,email) VALUES(%s,'DCP10负责人',%s)",
            (owner, f"{owner}@demo.example.invalid"),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'DCP10虚构事务所',%s)",
            (firm, owner),
        )
        conn.execute("INSERT INTO accounting.memberships VALUES(%s,%s,'owner')", (firm, owner))

    manager = Actor(owner, firm)
    db = Database(APP_URL)
    accounting = AccountingService(db)
    ledger = DirectoryService(db)
    accounting.directory = ledger
    companies_created = []
    started = time.perf_counter()
    for index in range(companies):
        company = ledger.create_company(
            manager, CompanyCreate(name=f"DCP10客户{index:03d}-{suffix}")
        )
        book = accounting.create_book(
            manager,
            BookCreate(
                name=f"DCP10客户{index:03d} 2026-10", period="2026-10", company_id=company["id"]
            ),
        )
        companies_created.append({"company_id": company["id"], "book_id": book["id"]})
    elapsed = (time.perf_counter() - started) * 1000
    return SimpleNamespace(
        firm=firm,
        owner=owner,
        manager=manager,
        companies=companies_created,
        seed_ms=round(elapsed, 2),
        app_url=APP_URL,
    )


def services():
    db = Database(APP_URL)
    accounting = AccountingService(db)
    ledgers = LedgersService(db)
    business = BusinessService(db)
    plans = ServicePlansService(db, accounting, business)
    monthly = MonthlyBatchService(db, accounting, ledgers, plans, business)
    return db, accounting, BatchOperationsService(db, monthly)


def batch_request(case, key):
    return CreateMonthlyOpenBatch(
        period="2026-11",
        scope="companies",
        companies=[row["company_id"] for row in case.companies],
        generate_materials=False,
        reason="DCP10 本地容量测试",
        idempotency_key=key,
    )


def items_of(db, batch_id, actor):
    with db.transaction(actor=actor) as conn:
        return conn.execute(
            "SELECT * FROM batch_operation_items WHERE batch_id=? ORDER BY created_at", (batch_id,)
        ).fetchall()


def distributions(values):
    values = sorted(values)
    if not values:
        return {}
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 1),
        "p95_ms": round(values[max(0, (95 * len(values) + 99) // 100 - 1)], 1),
        "max_ms": round(values[-1], 1),
    }


def conservation(db, case, batch):
    with db.transaction(actor=case.manager) as conn:
        rows = conn.execute(
            "SELECT company_id, status, COUNT(*) AS n FROM batch_operation_items "
            "WHERE batch_id=? GROUP BY company_id, status",
            (batch["id"],),
        ).fetchall()
        new_books = conn.execute(
            "SELECT company_id, COUNT(*) AS n FROM books WHERE firm_id=? AND period='2026-11' "
            "GROUP BY company_id",
            (case.firm,),
        ).fetchall()
        open_periods = conn.execute(
            "SELECT COUNT(*) AS n FROM periods WHERE book_id IN "
            "(SELECT id FROM books WHERE firm_id=? AND period='2026-11') AND status='open'",
            (case.firm,),
        ).fetchone()["n"]
    duplicates = [row for row in new_books if int(row["n"]) > 1]
    by_status = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + int(row["n"])
    return {
        "items_by_status": by_status,
        "new_books": sum(int(row["n"]) for row in new_books),
        "duplicate_books": len(duplicates),
        "open_periods": int(open_periods),
    }


def run(admin_dsn, companies, out, workers=1):
    case = seed(admin_dsn, companies)
    db, accounting, batches = services()
    request = batch_request(case, f"dcp10-{companies}-{uuid4().hex[:8]}")
    preview_started = time.perf_counter()
    preview = batches.preview_monthly_open(case.manager, request)
    preview_ms = (time.perf_counter() - preview_started) * 1000
    started = time.perf_counter()
    batch = batches.create_monthly_open(case.manager, request)
    enqueue_ms = (time.perf_counter() - started) * 1000

    def active():
        with db.transaction(actor=case.manager) as conn:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM batch_operation_items WHERE batch_id=? "
                "AND status IN ('queued','running','retry_wait')",
                (batch["id"],),
            ).fetchone()["n"]

    executed = 0
    started = time.perf_counter()
    if workers == 1:
        worker = BatchWorker(batches)
        idle = 0
        while active():
            if worker.run_once():
                executed += 1
                idle = 0
            else:
                idle += 1
                if idle > 200:
                    raise RuntimeError("批处理没有进展，疑似租约或可用时间异常")
                time.sleep(0.05)
    else:
        from concurrent.futures import ThreadPoolExecutor

        def drain():
            count = 0
            worker = BatchWorker(batches)
            idle = 0
            while active():
                if worker.run_once():
                    count += 1
                    idle = 0
                else:
                    idle += 1
                    if idle > 200:
                        raise RuntimeError("批处理没有进展，疑似租约或可用时间异常")
                    time.sleep(0.05)
            return count

        with ThreadPoolExecutor(max_workers=workers) as pool:
            executed = sum(pool.map(lambda _: drain(), range(workers)))
    wall_ms = (time.perf_counter() - started) * 1000
    items = items_of(db, batch["id"], case.manager)

    def moment(value):
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))

    durations = [
        (moment(row["finished_at"]) - moment(row["started_at"])).total_seconds() * 1000
        for row in items
        if row.get("started_at") and row.get("finished_at")
    ]
    counts = {}
    for row in items:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    report = {
        "stamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "companies": companies,
        "workers": workers,
        "seed_ms": case.seed_ms,
        "preview_ms": round(preview_ms, 2),
        "preview_rows": len(preview.get("rows", preview.get("companies", []))),
        "enqueue_ms": round(enqueue_ms, 2),
        "wall_ms": round(wall_ms, 2),
        "item_ms": distributions(durations),
        "items": counts,
        "executed_calls": executed,
        "conservation": conservation(db, case, batch),
    }
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def lease(admin_dsn, companies):
    """A crashed worker's item must be reclaimed without opening the period twice."""
    case = seed(admin_dsn, companies)
    db, accounting, batches = services()
    batch = batches.create_monthly_open(
        case.manager, batch_request(case, f"dcp10-lease-{uuid4().hex[:6]}")
    )
    first = batches.claim("worker-a", lease_seconds=30)
    assert first, "expected a claimable item"
    second_early = batches.claim("worker-b", lease_seconds=30)
    print("second_worker_waited=", second_early is None)
    # Simulate the crashed worker's lease expiring, then let another worker take it.
    with connect_admin(admin_dsn) as conn:
        conn.execute(
            "UPDATE accounting.batch_operation_items SET lease_until='2000-01-01T00:00:00+00:00' "
            "WHERE id=%s",
            (first["item_id"],),
        )
    reclaimed = batches.claim("worker-b", lease_seconds=60)
    assert reclaimed, "expected the expired lease to be reclaimed"
    # Let every still-leased item expire so one worker can drain the rest.
    with connect_admin(admin_dsn) as conn:
        conn.execute(
            "UPDATE accounting.batch_operation_items SET lease_until='2000-01-01T00:00:00+00:00' "
            "WHERE status='running'"
        )
    worker = BatchWorker(batches, worker_id="worker-c")
    while worker.run_once():
        pass
    items = items_of(db, batch["id"], case.manager)
    checks = conservation(db, case, batch)
    print(
        json.dumps(
            {
                "reclaimed_after_expiry": bool(reclaimed),
                "items": len(items),
                "duplicate_books": checks["duplicate_books"],
                "new_books": checks["new_books"],
            },
            ensure_ascii=False,
        )
    )


def cancel(admin_dsn, companies):
    case = seed(admin_dsn, companies)
    db, accounting, batches = services()
    batch = batches.create_monthly_open(
        case.manager, batch_request(case, f"dcp10-cancel-{uuid4().hex[:6]}")
    )
    worker = BatchWorker(batches)
    assert worker.run_once()
    queued = [row for row in items_of(db, batch["id"], case.manager) if row["status"] == "queued"]
    assert queued, "expected a queued item after one run"
    batches.cancel_item(case.manager, batch["id"], queued[0]["id"], "客户暂缓开期")
    while worker.run_once():
        pass
    items = items_of(db, batch["id"], case.manager)
    counts = {}
    for row in items:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print(
        json.dumps(
            {"items": counts, "new_books": conservation(db, case, batch)["new_books"]},
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["seed", "run", "lease", "cancel"])
    parser.add_argument("--admin", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--companies", type=int, default=10)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--out", default="tmp_test/dcp10-report.json")
    args = parser.parse_args()
    global APP_URL
    APP_URL = args.app
    if args.mode == "seed":
        case = seed(args.admin, args.companies)
        print(
            json.dumps(
                {"firm": case.firm, "companies": len(case.companies), "seed_ms": case.seed_ms}
            )
        )
    elif args.mode == "run":
        run(args.admin, args.companies, args.out, args.workers)
    elif args.mode == "lease":
        lease(args.admin, args.companies)
    else:
        cancel(args.admin, args.companies)


APP_URL = ""

if __name__ == "__main__":
    main()
