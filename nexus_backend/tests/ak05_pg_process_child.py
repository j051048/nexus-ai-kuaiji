"""Isolated AK05 subprocess. Only deterministic fixture providers may execute."""

import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit


def main():
    payload = json.loads(sys.stdin.readline())
    target = urlsplit(payload["worker_url"])
    assert target.hostname == "127.0.0.1" and target.port != 5432
    assert target.path.startswith("/ak01_local_ak05_")
    root = Path(payload["root"])
    sys.path[:0] = [str(root / "nexus_backend"), str(root / "nexus_backend/tests"), str(root)]
    import psycopg

    connect = psycopg.connect

    def worker_only(url, *args, **kwargs):
        assert url == payload["worker_url"], "child rejected another database"
        return connect(url, *args, **kwargs)

    def deny_network(*args, **kwargs):
        raise RuntimeError("AK05 child providers must remain offline")

    psycopg.connect = worker_only
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    from app.accounting.database import Database
    from app.business.storage import AttachmentStore
    from app.intelligence.models import ProviderUsage
    from app.intelligence.service import IntelligenceService
    from app.jobs.service import QueueService
    from app.jobs.worker import QueueWorker
    from app.operations.meter import MeterService
    from app.operations.pricing import Pricing, Rate
    from app.unified_worker import UnifiedWorker
    from test_intelligence_workflow import FakeProvider

    db = Database(payload["worker_url"])
    provider = FakeProvider(document=payload["document"])
    provider.usage = ProviderUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)
    receipt = Path(payload["receipts"]) / f"{os.getpid()}.txt"
    release = Path(payload["release"])
    action = payload["action"]

    def received():
        with receipt.open("ab") as stream:
            stream.write(b"received\n")
            stream.flush()
            os.fsync(stream.fileno())
        if action == "after_receipt":
            os._exit(79)
        if action in {"hold", "daemon"}:
            deadline = time.monotonic() + 30
            while not release.exists():
                assert time.monotonic() < deadline, "synthetic supplier guard expired"
                time.sleep(0.01)

    provider.extract_hook = received
    service = IntelligenceService(db, AttachmentStore(payload["storage"]), provider)
    service.meter = MeterService(db, pricing=Pricing(ocr=Rate("2.000000", "4.000000")))
    queue = QueueService(db, service)
    worker = QueueWorker(queue, worker_id="ak05-pg-process-" + str(os.getpid()))
    worker.heartbeat_seconds = 0.05
    unified = UnifiedWorker(queue_worker=worker, queue_budget=1, idle_seconds=0.05)
    unified.monitor_interval = 0.05
    if action == "before_marker":
        worker.process = lambda job: os._exit(79)
    elif action == "after_marker":
        provider.extract_invoice = lambda *args, **kwargs: os._exit(79)
    print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)
    assert sys.stdin.readline().strip() == "go"
    drained = False
    try:
        if action == "daemon":
            unified.start()
            assert sys.stdin.readline().strip() == "stop"
            unified.stop()
            drained = unified.wait_for_idle(0.05)
            with queue.worker_transaction() as conn:
                healthy = conn.execute("SELECT 1").fetchone()[0] == 1
            print(
                json.dumps({"drained": drained, "closed": db._closed, "healthy": healthy}),
                flush=True,
            )
            assert sys.stdin.readline().strip() == "finish"
        else:
            unified.run_until_idle(20)
            if action == "after_commit":
                os._exit(79)
    finally:
        unified.stop()
        drained = unified.wait_for_idle(10)
        if drained:
            db.close()
    print(json.dumps({"pid": os.getpid(), "drained": drained, "closed": db._closed}), flush=True)
    assert drained and db._closed


if __name__ == "__main__":
    main()
