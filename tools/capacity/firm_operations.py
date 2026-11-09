"""Disposable PG business capacity, reusing QA workers and C01 setup helpers.

Two synthetic cohorts: reviewed exports and zero-activity closed monthly books.
This measures service/storage boundaries, not internet delivery or real accounting
throughput. No provider, notification channel or production URL is used.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests")]
from tools.capacity.qa_mixed import (
    load_target,
    batch_services,
    drain,
    require_complete,
    distribution,
)  # noqa: E402
from tools.capacity.process_usage import process_usage  # noqa: E402
from scripts.release_candidate import candidate_fingerprint  # noqa: E402


def validate_results(rows, companies):
    require_complete(rows, len(companies))
    if (
        {r.get("company_id") for r in rows} != companies
        or len({r.get("output_id") for r in rows}) != len(companies)
        or any(not r.get("output_id") for r in rows)
    ):
        raise ValueError("Business outputs must conserve the exact reviewed company set")
    return len(rows)


def validate_mixed_results(rows, companies):
    if not rows or any(
        r.get("company_id") not in companies or not r.get("draft_id") or not r.get("export_id")
        for r in rows
    ):
        raise ValueError("Mixed workload needs actual scoped reviewed exports")
    for key in ("draft_id", "export_id"):
        if len({r[key] for r in rows}) != len(rows):
            raise ValueError("Mixed workload duplicated a reviewed output")
    return {
        "count": len(rows),
        "companies": len({r["company_id"] for r in rows}),
        "output_sha256": hashlib.sha256(
            json.dumps(sorted(rows, key=lambda r: r["draft_id"]), sort_keys=True).encode()
        ).hexdigest(),
    }


def process_batch(config, service, manager, batch, output_key):
    started = time.perf_counter()
    with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
        workers = list(
            pool.map(
                drain, [config] * 2, [batch["id"]] * 2, [manager.user_id] * 2, [manager.firm_id] * 2
            )
        )
    require_complete(workers, 2)
    assert len({w["pid"] for w in workers}) == 2
    result = service.get(manager, batch["id"])
    rows = [
        {
            "company_id": r["company_id"],
            "output_id": r["result"].get(output_key),
            "ok": r["status"] == "succeeded",
        }
        for r in result["items"]
    ]
    expected = {r["company_id"] for r in batch["items"]}
    count = validate_results(rows, expected)
    return {
        "seconds": round(time.perf_counter() - started, 3),
        "count": count,
        "workers": workers,
    }, result


def run(config, major, out, companies=200, seconds=60, mixed=False):
    from PIL import Image
    import psycopg
    from app.accounting.domain import Actor, AccountingError
    from app.accounting.service import AccountingService
    from app.accounting.models import ExportRequest
    from app.business.service import BusinessService
    from app.business.storage import AttachmentStore
    from app.business.models import BusinessEventCreate
    from app.cockpit.service import FirmCockpitService
    from app.delivery.monthly_package import MonthlyPackageService
    from tools.capacity import dcp10_capacity as seed_tool
    from c01_bank_fixture import reviewed_export_draft
    from test_monthly_delivery_postgres import package_case
    from pytest import MonkeyPatch

    if (
        type(companies) is not int
        or not 2 <= companies <= 200
        or type(seconds) is not int
        or not 1 <= seconds <= 1800
    ):
        raise ValueError("Explicit bounded workload required")
    target = load_target(config, major)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    candidate = candidate_fingerprint(ROOT)
    report = {
        "schema": "firm-operations-capacity-v1",
        "status": "failed",
        "major": major,
        "candidate": candidate,
        "companies_per_cohort": companies,
        "requested_sustained_seconds": seconds,
        "boundary": "restricted PG service and private local storage; two synthetic cohorts",
        "resources_start": process_usage(),
        "phases": {},
    }
    db, worker_db, batches = batch_services(target)
    ledger, business = AccountingService(db), BusinessService(db)
    phase = "seed"
    try:
        seed_tool.APP_URL = target["app"]
        exports = seed_tool.seed(target["admin"], companies)
        packages = seed_tool.seed(target["admin"], companies)
        reviewers = []
        # Fixture identities only. Every measured business transition is restricted.
        with psycopg.connect(target["admin"]) as conn:
            for cohort in (exports, packages):
                reviewer = Actor("ux11-reviewer-" + uuid4().hex, cohort.firm)
                conn.execute(
                    "INSERT INTO accounting.users(id,name,email) VALUES(%s,'虚构独立复核员',%s)",
                    (reviewer.user_id, reviewer.user_id + "@example.invalid"),
                )
                conn.execute(
                    "INSERT INTO accounting.memberships VALUES(%s,%s,'admin')",
                    (cohort.firm, reviewer.user_id),
                )
                reviewers.append(reviewer)
            for row in packages.companies:
                conn.execute(
                    "INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'admin')",
                    (row["company_id"], packages.manager.user_id),
                )
        phase = "prepare_reviewed_exports"
        for row in exports.companies:
            reviewed_export_draft(
                SimpleNamespace(
                    db=db,
                    ledger=ledger,
                    manager=exports.manager,
                    reviewer=reviewers[0],
                    book={"id": row["book_id"]},
                    company={"id": row["company_id"]},
                )
            )
        phase = "batch_export"
        ids = [row["company_id"] for row in exports.companies]
        preview = batches.preview_review_export(exports.manager, ids, "2026-10")
        assert preview["ready"] == companies
        batch = batches.create_review_export(
            exports.manager, ids, "2026-10", preview["manifest"], uuid4().hex
        )
        report["phases"][phase], export_result = process_batch(
            target, batches, exports.manager, batch, "export_id"
        )
        phase = "prepare_closed_packages"
        for number, row in enumerate(packages.companies):
            book = {"id": row["book_id"], "company_id": row["company_id"], "period": "2026-10"}
            c = SimpleNamespace(
                db=db,
                manager=packages.manager,
                reviewer=reviewers[1],
                accounting=ledger,
                business=business,
                book=book,
            )
            # Existing acceptance fixture interleaves real applicability after
            # opening approval and before report/close; no guard is bypassed.
            with MonkeyPatch.context() as patch:
                package_case.__wrapped__(c, patch)
            if number % 25 == 0:
                print(
                    json.dumps({"phase": phase, "prepared": number + 1, "total": companies}),
                    flush=True,
                )
        phase = "batch_package"
        ids = [row["company_id"] for row in packages.companies]
        preview = batches.preview_package_drafts(packages.manager, ids, "2026-10")
        assert preview["ready"] == companies, "Package readiness did not pass for every company"
        batch = batches.create_package_drafts(
            packages.manager, ids, "2026-10", preview["manifest"], uuid4().hex
        )
        report["phases"][phase], package_result = process_batch(
            target, batches, packages.manager, batch, "package_id"
        )
        phase = "private_original"
        company_id = packages.companies[0]["company_id"]
        employee = Actor("ux11-employee-" + uuid4().hex, packages.firm)
        with psycopg.connect(target["admin"]) as conn:
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,'虚构员工',%s)",
                (employee.user_id, employee.user_id + "@example.invalid"),
            )
            conn.execute(
                "INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'employee')",
                (company_id, employee.user_id),
            )
        event = business.create(
            employee,
            BusinessEventCreate(
                company_id=company_id,
                template_id="reimbursement",
                payload={
                    "occurred_on": "2026-10-10",
                    "description": "容量原件测试",
                    "counterparty": "虚构商户",
                    "amount": "1.00",
                    "direction": "expense",
                    "payment_status": "unpaid",
                    "category": "office",
                },
            ),
        )
        buffer = io.BytesIO()
        Image.frombytes("RGB", (1536, 1536), os.urandom(1536 * 1536 * 3)).save(buffer, format="PNG")
        original = buffer.getvalue()
        store = AttachmentStore(out / "private-originals")
        metadata = store.put("synthetic-original.png", "image/png", original)
        business.add_attachment(employee, event["id"], event["version"], metadata)
        store.retain(metadata)
        with_error = False
        try:
            business.download_metadata(exports.manager, metadata["id"])
        except AccountingError as error:
            with_error = error.status == 404
        assert with_error, "Foreign original access unexpectedly succeeded"
        phase = "sustained_mixed" if mixed else "sustained_reads"
        cockpit = FirmCockpitService(db, ledger)
        monthly = MonthlyPackageService(db)
        start = time.monotonic()
        measurements = {"clients": [], "package": [], "original": []}
        if mixed:
            measurements["review_export"] = []
        written = []
        largest_response = 0
        rounds = 0

        def read(index):
            kinds = (
                ("clients", "package", "original", "review_export")
                if mixed
                else ("clients", "package", "original")
            )
            kind = kinds[index % len(kinds)]
            begun = time.perf_counter()
            if kind == "clients":
                result = cockpit.snapshot(
                    exports.manager,
                    "2026-10",
                    limit=20,
                    offset=(index % max(1, (companies + 19) // 20)) * 20,
                )
                assert result["pagination"]["total"] == companies and len(result["companies"]) <= 20
                size = len(json.dumps(result, ensure_ascii=False).encode())
            elif kind == "package":
                item = package_result["items"][index % companies]
                payload = monthly.download(
                    packages.manager, item["result"]["book_id"], item["result"]["package_id"]
                )
                assert payload.startswith(b"PK")
                size = len(payload)
            elif kind == "original":
                payload = store.read(business.download_metadata(employee, metadata["id"]))
                assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]
                size = len(payload)
            else:
                row = exports.companies[(rounds * 6 + index // 4) % companies]
                draft = reviewed_export_draft(
                    SimpleNamespace(
                        db=db,
                        ledger=ledger,
                        manager=exports.manager,
                        reviewer=reviewers[0],
                        book={"id": row["book_id"]},
                        company={"id": row["company_id"]},
                    )
                )
                result = ledger.export(
                    exports.manager,
                    row["book_id"],
                    ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex),
                )
                assert result["count"] == 1
                record = {
                    "company_id": row["company_id"],
                    "draft_id": draft["id"],
                    "export_id": result["id"],
                }
                return (
                    kind,
                    (time.perf_counter() - begun) * 1000,
                    len(json.dumps(result).encode()),
                    record,
                )
            return kind, (time.perf_counter() - begun) * 1000, size, None

        with ThreadPoolExecutor(8) as pool:
            while time.monotonic() - start < seconds:
                for kind, elapsed, size, record in pool.map(read, range(24)):
                    measurements[kind].append(elapsed)
                    if record:
                        written.append(record)
                    if kind == "clients":
                        largest_response = max(size, largest_response)
                rounds += 1
        report["phases"][phase] = {
            "seconds": round(time.monotonic() - start, 3),
            "rounds": rounds,
            "concurrency": 8,
            "latency": {k: distribution(v) for k, v in measurements.items()},
            "largest_client_response_bytes": largest_response,
            "original_bytes": len(original),
            "pool": db.pool_snapshot(),
            "transactions": db.metrics.snapshot(firm_id=exports.firm),
        }
        assert largest_response < 100_000
        if mixed:
            report["phases"][phase]["reviewed_outputs"] = validate_mixed_results(
                written, {r["company_id"] for r in exports.companies}
            )
        assert candidate_fingerprint(ROOT) == candidate, "Candidate changed during capacity run"
        report["status"] = "passed"
    except Exception as error:
        report["diagnostic"] = {"phase": phase, "class": type(error).__name__}
        raise
    finally:
        report["resources_end"] = process_usage()
        db.close()
        worker_db.close()
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--major", required=True, type=int, choices=(16, 17))
    parser.add_argument("--out", required=True)
    parser.add_argument("--companies", type=int, default=200)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument(
        "--mixed",
        action="store_true",
        help="Read, independently review/export and download concurrently",
    )
    args = parser.parse_args()
    try:
        run(
            json.loads(Path(args.config).read_text(encoding="utf-8")),
            args.major,
            args.out,
            args.companies,
            args.seconds,
            args.mixed,
        )
        print(json.dumps({"status": "passed", "report": str(Path(args.out) / "report.json")}))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "class": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
