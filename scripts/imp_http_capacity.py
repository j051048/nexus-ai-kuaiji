"""PG HTTP acceptance using existing P02 signed identities and NEXT09 metrics.

Only explicit isolated PG configurations; no real model or notification calls.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests")]
from scripts.next09_http_capacity import summary
from scripts.release_candidate import candidate_fingerprint
from scripts.ak05_postgres_gate import validate_config, assert_server_and_roles
from tools.capacity.process_usage import process_usage


def validate_rows(rows, expected):
    if (
        len(rows) != expected
        or len({r["id"] for r in rows}) != expected
        or not all(r["ok"] for r in rows)
    ):
        raise ValueError("HTTP capacity requests missing, duplicated or failed")


def run(config, major, output):
    import socket
    import httpx
    import uvicorn
    from p02_session_fixture import create_session

    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "tmp_test") or output.exists():
        raise ValueError("Require a fresh local output directory")
    target = validate_config(json.loads(Path(config).read_text(encoding="utf-8-sig")))
    assert_server_and_roles(target, major)
    output.mkdir(parents=True)
    before = candidate_fingerprint(ROOT)
    report = {
        "status": "failed",
        "candidate": before,
        "postgres": major,
        "concurrency": 50,
        "companies": 50,
        "reads": 200,
        "writes": 50,
        "model_calls": 0,
        "notification_calls": 0,
        "identity": "P02 local signed JWT fixture; live loopback API; restricted PG runtime",
        "database_cpu": "not_sampled",
        "application_processes": 1,
    }
    with create_session(config, output / "private", major=major) as session:
        # Bind and retain the socket; no port check/rebind race or foreign process.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(
                session.app, host="127.0.0.1", lifespan="off", access_log=False, log_level="warning"
            )
        )
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("local API startup failed")
                time.sleep(0.05)
            headers = session.headers["owner"]
            tag = uuid4().hex
            with httpx.Client(
                base_url=base, trust_env=False, timeout=30, headers=headers
            ) as client:
                books = []
                for i in range(50):
                    response = client.post(
                        "/api/companies", json={"name": f"IMP capacity {tag} {i}", "tax_id": ""}
                    )
                    response.raise_for_status()
                    response = client.post(
                        "/api/books",
                        json={
                            "company_id": response.json()["id"],
                            "name": f"Book {i}",
                            "period": "2026-10",
                        },
                    )
                    response.raise_for_status()
                    books.append(response.json()["id"])
                client.get("/api/books/" + books[0]).raise_for_status()

                def operation(i):
                    start = time.perf_counter()
                    book = books[i % 50]
                    try:
                        if i % 5 == 0:
                            response = client.post(
                                f"/api/books/{book}/imports",
                                json={
                                    "source_name": "IMP synthetic HTTP",
                                    "rows": [
                                        {
                                            "external_id": f"{tag}-{i}",
                                            "date": "2026-10-01",
                                            "description": "虚构办公费",
                                            "counterparty": "虚构商户",
                                            "amount": "0.30",
                                            "direction": "expense",
                                            "evidence_ref": "synthetic",
                                        }
                                    ],
                                },
                            )
                            expected = 200
                        else:
                            response = client.get("/api/books/" + book)
                            expected = 200
                        return {
                            "id": str(i),
                            "book_id": book,
                            "kind": "write" if i % 5 == 0 else "read",
                            "status": response.status_code,
                            "ok": response.status_code == expected,
                            "ms": (time.perf_counter() - start) * 1000,
                        }
                    except Exception as error:
                        return {
                            "id": str(i),
                            "status": 0,
                            "ok": False,
                            "class": type(error).__name__,
                            "ms": (time.perf_counter() - start) * 1000,
                        }

                with ThreadPoolExecutor(max_workers=50) as pool:
                    rows = list(pool.map(operation, range(250)))
            report.update(
                http=summary(rows),
                operations=rows,
                pool=session.db.pool_snapshot(),
                transactions=session.db.metrics.snapshot(firm_id=session.ids["firm"]),
                resources=process_usage(),
            )
            validate_rows(rows, 250)
            if candidate_fingerprint(ROOT) != before:
                raise RuntimeError("candidate_changed")
            report["status"] = "passed"
        except Exception as error:
            report["error_class"] = type(error).__name__
        finally:
            server.should_exit = True
            thread.join(30)
            if thread.is_alive():
                raise RuntimeError("local API did not drain")
            sock.close()
            (output / "summary.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(
        json.dumps(
            {"status": report["status"], "major": major, "report": str(output / "summary.json")}
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--major", type=int, choices=[16, 17], required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.config, args.major, args.out))
