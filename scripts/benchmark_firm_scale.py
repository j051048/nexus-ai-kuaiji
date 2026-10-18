"""Synthetic cockpit benchmark; never touches a configured production database."""

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))

from app.accounting.database import Database  # noqa: E402
from app.accounting.seed import LOCAL_ACTOR, bootstrap  # noqa: E402
from app.accounting.service import AccountingService  # noqa: E402
from app.cockpit.service import FirmCockpitService  # noqa: E402


def seed(db, count):
    accounting = AccountingService(db)
    bootstrap(accounting, seed_examples=False)
    period = "2026-10"
    stamp = "2026-10-18T00:00:00+00:00"
    with db.transaction(write=True) as conn:
        for index in range(count):
            company_id = f"bench-company-{index:04d}"
            book_id = f"bench-book-{index:04d}"
            conn.execute(
                "INSERT INTO companies(id,name,tax_id) VALUES(?,?,?)",
                (company_id, f"合成客户 {index:04d}", f"BENCH{index:08d}"),
            )
            conn.execute(
                "INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')",
                (LOCAL_ACTOR.firm_id, company_id),
            )
            conn.execute(
                "INSERT INTO books(id,firm_id,company_id,name,period) VALUES(?,?,?,?,?)",
                (book_id, LOCAL_ACTOR.firm_id, company_id, f"合成账套 {index:04d}", period),
            )
            conn.execute(
                "INSERT INTO periods(book_id,period,status) VALUES(?,?,'open')",
                (book_id, period),
            )
            conn.execute(
                "INSERT INTO workspace_task_projections(firm_id,company_id,kind,ref,book_id,event_id,"
                "draft_id,batch_id,applicant_id,department_id,assignee_id,recipient_id,created_by,"
                "submission_mode,ref_kind,ref_detail,source_open,state,title,due_on,updated_at,amount_cents) "
                "VALUES(?,?,'review',?,?,NULL,?,NULL,NULL,NULL,NULL,NULL,NULL,'','review','',0,"
                "'draft',?,?,?,100)",
                (
                    LOCAL_ACTOR.firm_id,
                    company_id,
                    f"draft-{index}",
                    book_id,
                    f"draft-{index}",
                    f"合成凭证 {index}",
                    f"{period}-18",
                    stamp,
                ),
            )
    return accounting


def run_one(companies, repeats):
    with tempfile.TemporaryDirectory(prefix="nexus-cockpit-bench-") as directory:
        db = Database(Path(directory) / "benchmark.db")
        try:
            accounting = seed(db, companies)
            cockpit = FirmCockpitService(db, accounting)
            # Warm schema/query plans before measuring.
            cockpit.snapshot(LOCAL_ACTOR, "2026-10")
            samples = []
            for _ in range(repeats):
                started = perf_counter()
                value = cockpit.snapshot(LOCAL_ACTOR, "2026-10")
                samples.append((perf_counter() - started) * 1000)
            samples.sort()
            return {
                "companies": companies,
                "repeats": repeats,
                "p50_ms": round(statistics.median(samples), 1),
                "p95_ms": round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 1),
                "max_ms": round(max(samples), 1),
                "risk_clients": value["summary"]["risk_clients"],
            }
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description="合成客户事务所驾驶舱性能基线")
    parser.add_argument("--companies", default="100,500,1000")
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    sizes = [int(value) for value in args.companies.split(",") if value.strip()]
    if not sizes or any(value not in {100, 500, 1000} for value in sizes):
        parser.error("--companies 只接受 100/500/1000 的组合")
    if not 3 <= args.repeats <= 50:
        parser.error("--repeats 必须在 3–50 之间")
    for size in sizes:
        print(json.dumps(run_one(size, args.repeats), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
