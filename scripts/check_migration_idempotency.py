"""Lint new PostgreSQL migrations for unsafe, non-idempotent object changes."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "nexus_backend" / "migrations" / "postgres"
BASELINE_VERSION = 135


def violations(name: str, sql: str) -> list[str]:
    result = []
    upper = sql.upper()
    for match in re.finditer(r"\bCREATE\s+FUNCTION\b", upper):
        prefix = upper[max(0, match.start() - 12):match.end()]
        if "OR REPLACE" not in prefix:
            result.append(f"{name}: CREATE FUNCTION 必须使用 CREATE OR REPLACE")
    if "PG_GET_FUNCTIONDEF" in upper and re.search(r"\bREPLACE\s*\(", upper):
        result.append(f"{name}: 禁止用 pg_get_functiondef + replace 改写函数")
    if re.search(r"\bCREATE\s+TRIGGER\b", upper) and "DROP TRIGGER IF EXISTS" not in upper:
        result.append(f"{name}: CREATE TRIGGER 前必须有 DROP TRIGGER IF EXISTS")
    return result


def check(version_gt: int = BASELINE_VERSION) -> list[str]:
    result = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        if version <= version_gt:
            continue
        result.extend(violations(path.name, path.read_text(encoding="utf-8")))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", type=int, default=BASELINE_VERSION)
    args = parser.parse_args()
    problems = check(args.after)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
