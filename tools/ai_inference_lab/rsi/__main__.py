"""Offline-only CLI. Database rule staging is deliberately not a command."""

import argparse
import json
from pathlib import Path

from .snapshots import Store, SnapshotError
from .experiment import Experiment, freeze


def main(argv=None):
    parser = argparse.ArgumentParser(description="RSI 离线候选与回放，不接模型或数据库")
    parser.add_argument("--store", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot")
    snap.add_argument("--resources", type=Path, required=True)
    snap.add_argument("--parent")
    snap.add_argument("--reason", required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("candidate")
    diff = commands.add_parser("diff")
    diff.add_argument("first")
    diff.add_argument("second")
    plan = commands.add_parser("freeze")
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--baseline", required=True)
    plan.add_argument("--candidate", action="append", required=True)
    plan.add_argument("--out", type=Path, required=True)
    plan.add_argument("--max-calls", type=int, default=100)
    plan.add_argument("--max-seconds", type=int, default=60)
    plan.add_argument("--archives", type=Path)
    plan.add_argument("--recorded-model", default="none")
    run = commands.add_parser("run")
    run.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    store = Store(args.store)
    try:
        if args.command == "snapshot":
            result = store.from_directory(args.resources, parent=args.parent, reason=args.reason)
            output = {k: result[k] for k in ("id", "root_id", "changed_paths")}
        elif args.command == "inspect":
            row = store.load(args.candidate)
            output = {
                k: row[k] for k in ("id", "parent_id", "root_id", "content_sha256", "changed_paths")
            }
        elif args.command == "diff":
            output = store.diff(args.first, args.second)
        elif args.command == "freeze":
            result = freeze(
                store,
                args.manifest,
                args.baseline,
                args.candidate,
                args.out,
                max_calls=args.max_calls,
                max_seconds=args.max_seconds,
                archives=args.archives,
                recorded_model=args.recorded_model,
            )
            output = {
                "baseline": result["baseline"],
                "candidates": result["candidates"],
                "out": str(args.out),
            }
        else:
            result = Experiment(store, args.out).run()
            output = {
                k: result[k]
                for k in (
                    "selected",
                    "acceptance",
                    "attempts",
                    "model_verified",
                    "publication_authorized",
                )
            }
    except (ValueError, OSError) as error:
        # Do not echo raw evidence, prompt, endpoint credentials or SQL.
        print(json.dumps({"status": "failed", "kind": type(error).__name__}))
        return 1
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
