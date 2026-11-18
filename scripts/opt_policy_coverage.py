#!/usr/bin/env python3
"""只读盘点本地 SQLite 中没有生效核算政策的账套。

不创建、不修改政策，也不为缺失政策的账套补默认值。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path


def inspect_database(path: str | Path, as_of: str | None = None) -> dict:
    as_of = as_of or date.today().isoformat()
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise SystemExit(f"database not found: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT l.id AS ledger_id, l.name AS ledger_name,
                   l.firm_id, l.company_id
              FROM ledgers l
              LEFT JOIN accounting_policies p
                ON p.ledger_id = l.id
               AND p.status = 'active'
               AND p.effective_from <= ?
               AND (p.effective_to IS NULL OR p.effective_to >= ?)
             WHERE p.id IS NULL
             ORDER BY l.firm_id, l.company_id, l.name
            """,
            (as_of, as_of),
        ).fetchall()
    finally:
        conn.close()
    missing = [dict(row) for row in rows]
    return {
        "database": str(resolved),
        "as_of": as_of,
        "missing_count": len(missing),
        "ledgers": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="SQLite database path")
    parser.add_argument("--as-of", help="YYYY-MM-DD; defaults to today")
    args = parser.parse_args()
    print(json.dumps(inspect_database(args.sqlite, args.as_of), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
