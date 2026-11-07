"""Bounded local observation runner. Only isolated PG config, no external actions."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "nexus_backend")]
from scripts.ak05_postgres_gate import validate_config, assert_server_and_roles
from app.accounting.database import Database
from app.accounting.domain import Actor
from app.assistant.observation import ObservationLedger, AnalysisObservation
from tools.ai_inference_lab.rsi.snapshots import atomic_create, canonical


def run(config, major, events, scope, principal, output):
    target = validate_config(json.loads(Path(config).read_text(encoding="utf-8-sig")))
    assert_server_and_roles(target, major)
    if len(events) > 100:
        raise ValueError("observation_input_limit")
    db = Database(target["app"])
    try:
        actor = Actor(principal["user_id"], principal["firm_id"])
        resolver = AnalysisObservation(db, actor, scope)
        ledger = ObservationLedger(
            directory=Path(output) / "checkpoints",
            scope=scope,
            principal=principal,
            max_events=100,
            seconds=300,
        )
        rows = [ledger.record(event, resolver=resolver) for event in events]
        # Each invocation gets an immutable report; checkpoints are resumable.
        from uuid import uuid4

        report = {
            "schema": "imp08-local-observation-v1",
            "records": rows,
            "model_calls": 0,
            "business_writes": 0,
            "production_subscription": "not_authorized",
        }
        atomic_create(Path(output) / (uuid4().hex + ".json"), canonical(report))
        return report
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--major", type=int, choices=[16, 17], required=True)
    parser.add_argument(
        "--input", required=True, help="trusted local scope/principal and event envelope"
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = run(
        args.config, args.major, data["events"], data["scope"], data["principal"], args.out
    )
    print(json.dumps({"observed": len(report["records"]), "business_writes": 0}))


if __name__ == "__main__":
    main()
