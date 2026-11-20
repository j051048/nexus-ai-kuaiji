"""FSC12 HTTP capacity probe with reusable per-thread connections."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentile(values, fraction):
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
            str(code): sum(1 for r in rows if r["status"] == code)
            for code in sorted({r["status"] for r in rows})
        },
        "latency_ms": {
            "p50": percentile([r["ms"] for r in ok], 0.50),
            "p95": percentile([r["ms"] for r in ok], 0.95),
            "p99": percentile([r["ms"] for r in ok], 0.99),
            "max": round(max((r["ms"] for r in ok), default=0), 2),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18005")
    parser.add_argument("--companies", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--reads", type=int, default=300)
    parser.add_argument("--writes", type=int, default=100)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not args.base_url.startswith("http://127.0.0.1:"):
        raise SystemExit("only loopback API is allowed")

    started = time.time()
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=60.0) as seed:
        session = seed.get("/api/session", params={"persona": "accountant"})
        session.raise_for_status()
        headers = {"Authorization": f"Bearer {session.json()['token']}"}
        tag = uuid4().hex[:8]
        companies, books = [], []
        for index in range(args.companies):
            company = seed.post(
                "/api/companies",
                headers=headers,
                json={"name": f"FSC12 {tag} {index}", "tax_id": ""},
            )
            company.raise_for_status()
            companies.append(company.json()["id"])
            created = seed.post(
                "/api/books",
                headers=headers,
                json={
                    "name": f"FSC12 {tag} {index} 2026-10",
                    "company_id": companies[-1],
                    "period": "2026-10",
                },
            )
            created.raise_for_status()
            books.append(created.json()["id"])
        seed.get(f"/api/books/{books[0]}?summaries=true&limit=100", headers=headers)

    def read_once(index):
        begin = time.perf_counter()
        try:
            response = client.get(
                f"/api/books/{books[index % len(books)]}?summaries=true&limit=100", headers=headers
            )
            return {
                "kind": "read",
                "status": response.status_code,
                "ok": response.status_code == 200,
                "ms": round((time.perf_counter() - begin) * 1000, 2),
            }
        except Exception as error:
            return {
                "kind": "read",
                "status": 0,
                "ok": False,
                "ms": round((time.perf_counter() - begin) * 1000, 2),
                "error": type(error).__name__,
            }

    client = httpx.Client(
        base_url=args.base_url,
        trust_env=False,
        timeout=60.0,
        limits=httpx.Limits(
            max_connections=max(100, args.concurrency),
            max_keepalive_connections=max(20, args.concurrency),
        ),
    )
    read_rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for row in pool.map(read_once, range(args.reads)):
            read_rows.append(row)

    def write_once(index):
        begin = time.perf_counter()
        try:
            response = client.post(
                "/api/books",
                headers=headers,
                json={
                    "name": f"FSC12 {tag} write {index}",
                    "company_id": companies[index % len(companies)],
                    "period": "2026-11",
                },
            )
            return {
                "kind": "write",
                "status": response.status_code,
                "ok": response.status_code == 201,
                "ms": round((time.perf_counter() - begin) * 1000, 2),
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

    client.close()
    from scripts.release_candidate import candidate_fingerprint

    report = {
        "schema": "fsc12-http-v1",
        "base_url": args.base_url,
        "companies": args.companies,
        "concurrency": args.concurrency,
        "reads": summary(read_rows),
        "writes": summary(write_rows),
        "elapsed_seconds": round(time.time() - started, 2),
        "candidate": candidate_fingerprint(),
        "storage": "local sqlite via public HTTP; PG capacity is separate",
        "client": "one shared httpx client with bounded connection pool",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
