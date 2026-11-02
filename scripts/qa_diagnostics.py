"""Small public failure envelope; never serializes exception text or environment."""

import argparse
import json
from pathlib import Path
import re


def write_failure(path, *, phase, error):
    if not re.fullmatch(r"[a-z0-9_-]{1,60}", phase):
        raise ValueError("invalid phase")
    value = {
        "status": "failed",
        "phase": phase,
        "exception": type(error).__name__,
        "next_action": "Inspect the corresponding private log; this is not acceptance evidence.",
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, indent=2)
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--phase", required=True)
    args = parser.parse_args()
    write_failure(args.out, phase=args.phase, error=RuntimeError("ci failure"))
