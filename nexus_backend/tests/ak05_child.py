"""AK05 child runner: one real SQLite queue worker process."""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend" / "tests"), str(ROOT)]

from app.accounting.database import Database
from app.business.storage import AttachmentStore
from app.intelligence.provider import AISettings
from app.intelligence.service import IntelligenceService
from app.jobs.service import QueueService
from app.jobs.worker import QueueWorker
from test_intelligence_workflow import FakeProvider


class Provider(FakeProvider):
    def __init__(self, counter_path, marker_path, hold):
        super().__init__(
            settings=AISettings(
                base_url="https://supplier.example.invalid/v1",
                api_key="test-only-not-a-secret",
                vision_model="synthetic-vision",
                journal_model="synthetic-journal",
            )
        )
        self.counter_path = counter_path
        self.marker_path = marker_path
        self.hold = hold

    def extract_invoice(self, pages, **kwargs):
        if self.counter_path:
            Path(self.counter_path).write_text("called", encoding="utf-8")
        if self.hold:
            Path(self.marker_path).write_text("entered", encoding="utf-8")
            time.sleep(30)
        return super().extract_invoice(pages, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--storage", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--mode", choices=["hold", "recover"], required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--counter", required=True)
    args = parser.parse_args()
    database = Database(args.db)
    try:
        provider = Provider(
            args.counter if args.mode == "recover" else None,
            args.marker,
            args.mode == "hold",
        )
        intelligence = IntelligenceService(
            database, AttachmentStore(args.storage), provider=provider
        )
        worker = QueueWorker(
            QueueService(database, intelligence),
            worker_id=args.worker_id,
            concurrency=1,
            lease_seconds=180,
        )
        worker.run_once()
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
