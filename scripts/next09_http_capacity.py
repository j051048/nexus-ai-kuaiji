"""NEXT09 HTTP ceiling probe against an explicitly local API.

The probe creates synthetic companies/books through the public API, then issues
bounded concurrent reads and writes. It never imports synthetic rows with SQL and
never contacts a remote host. Reports contain latency and candidate identity only.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def summary(rows):
    ok = [row for row in rows if row["ok"]]
    return {
        "total": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "status_counts": {
            str(code): sum(1 for row in rows if row["status"] == code)
            for code in sorted({row["status"] for row in rows})
        },
        "latency_ms": {
            "p50": percentile([row["ms"] for row in ok], 0.50),
            "p95": percentile([row["ms"] for row in ok], 0.95),
            "p99": percentile([row["ms"] for row in ok], 0.99),
            "max": round(max((row["ms"] for row in ok), default=0), 2),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18005")
    parser.add_argument("--companies", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--reads", type=int, default=200)
    parser.add_argument("--writes", type=int, default=50)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not args.base_url.startswith("http://127.0.0.1:"):
        raise SystemExit("only loopback API is allowed")

    started = time.time()
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=30.0) as client:
        session = client.get("/api/session", params={"persona": "accountant"})
        session.raise_for_status()
        token = session.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        tag = uuid4().hex[:8]
        books = []
        for index in range(args.companies):
            company = client.post(
                "/api/companies",
                headers=headers,
                json={"name": f"Capacity {tag} {index}", "tax_id": ""},
            )
            company.raise_for_status()
            created = client.post(
                "/api/books",
                headers=headers,
                json={
                    "name": f"Capacity {tag} {index} 2026-10",
                    "company_id": company.json()["id"],
                    "period": "2026-10",
                },
            )
            created.raise_for_status()
            books.append(created.json()["id"])
        # Warm the route once so the first request does not absorb import/JIT cost.
        client.get(f"/api/books/{books[0]}", headers=headers)

    def read_once(index):
        book = books[index % len(books)]
        begin = time.perf_counter()
        try:
            with httpx.Client(base_url=args.base_url, trust_env=False, timeout=30.0) as client:
                response = client.get(f"/api/books/{book}", headers=headers)
            elapsed = (time.perf_counter() - begin) * 1000
            return {
                "kind": "read",
                "status": response.status_code,
                "ok": response.status_code == 200,
                "ms": round(elapsed, 2),
            }
        except Exception as error:  # diagnostic only; no credentials in report
            return {
                "kind": "read",
                "status": 0,
                "ok": False,
                "ms": round((time.perf_counter() - begin) * 1000, 2),
                "error": type(error).__name__,
            }

    read_rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for row in pool.map(read_once, range(args.reads)):
            read_rows.append(row)

    def write_once(index):
        begin = time.perf_counter()
        try:
            with httpx.Client(base_url=args.base_url, trust_env=False, timeout=30.0) as client:
                response = client.post(
                    "/api/books",
                    headers=headers,
                    json={
                        "name": f"Capacity {tag} write {index}",
                        "period": "2026-11",
                        "company_id": None,
                    },
                )
            elapsed = (time.perf_counter() - begin) * 1000
            return {
                "kind": "write",
                "status": response.status_code,
                "ok": response.status_code == 201,
                "ms": round(elapsed, 2),
            }
        except Exception as error:
            return {
                "kind": "write",
                "status": 0,
                "ok": False,
                "ms": round((time.perf_counter() - begin) * 1000, 2),
                "error": type(error).__name__,
            }

    write_rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for row in pool.map(write_once, range(args.writes)):
            write_rows.append(row)

    from scripts.release_candidate import candidate_fingerprint

    report = {
        "schema": "next09-http-v1",
        "base_url": args.base_url,
        "companies": args.companies,
        "concurrency": args.concurrency,
        "reads": summary(read_rows),
        "writes": summary(write_rows),
        "elapsed_seconds": round(time.time() - started, 2),
        "candidate": candidate_fingerprint(),
        "storage": "local sqlite via public HTTP; PG capacity is separate",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
