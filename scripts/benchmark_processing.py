"""Synthetic local supplier throughput benchmark; never loads real credentials.

python scripts/benchmark_processing.py --requests 12 --latency-ms 150
This measures bounded transport overlap, not model/accounting correctness or a
promise of 200–500 real companies. Real PG gate proof is in the matching tests.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Lock, Thread
from time import perf_counter, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nexus_backend"))
from pydantic import BaseModel  # noqa: E402
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError  # noqa: E402


class SyntheticResult(BaseModel):
    synthetic_only: bool


def benchmark(count=12, latency_ms=150):
    if not 2 <= count <= 100 or not 10 <= latency_ms <= 1000:
        raise ValueError("合成任务数必须为 2–100，延迟为 10–1000ms")
    state = {"running": 0, "peak": 0, "requests": 0}
    guard = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            # Consume bounded synthetic prompt and hold a reproducible latency.
            body = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 65536))
            assert b"synthetic" in body
            with guard:
                state["running"] += 1
                state["peak"] = max(state["peak"], state["running"])
                state["requests"] += 1
            try:
                sleep(latency_ms / 1000)
                payload = json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": '{"synthetic_only":true}'},
                            }
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            finally:
                with guard:
                    state["running"] -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    results = []
    try:
        for limit in (1, 2, 4):
            with guard:
                state.update(running=0, peak=0, requests=0)
            settings = AISettings(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="synthetic-local-only",
                vision_model="synthetic-local",
                allow_http=True,
                max_concurrency=limit,
            )
            provider = OpenAICompatibleProvider(settings)
            waits = []

            def invoke(_):
                started = perf_counter()
                while True:
                    try:
                        result = provider._request(
                            model="synthetic-local",
                            output_type=SyntheticResult,
                            scenario="ocr",
                            prompt_version="synthetic-benchmark",
                            system="Only return the synthetic fixture",
                            user_content="synthetic",
                        )
                        assert result.data.synthetic_only is True
                        with guard:
                            waits.append((perf_counter() - started) * 1000)
                        return
                    except ProviderError as failure:
                        if failure.code != "busy":
                            raise
                        sleep(0.005)

            started = perf_counter()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(invoke, range(count)))
            elapsed = perf_counter() - started
            with guard:
                assert state["requests"] == count and state["peak"] <= limit
                results.append(
                    {
                        "concurrency": limit,
                        "completed": count,
                        "supplier_peak": state["peak"],
                        "elapsed_seconds": round(elapsed, 3),
                        "throughput_per_second": round(count / elapsed, 3),
                        "completion_p95_ms": round(
                            sorted(waits)[max(0, int(len(waits) * 0.95) - 1)], 1
                        ),
                    }
                )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    return {
        "fixture": "synthetic_local_http_no_financial_evidence",
        "requests": count,
        "latency_ms": latency_ms,
        "runs": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--latency-ms", type=int, default=150)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = benchmark(args.requests, args.latency_ms)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered)
