"""OPT01-A: all *_cents columns must end up 64-bit in PostgreSQL.

The scan follows migration order instead of checking only the CREATE TABLE
that first introduced a column, because later forward migrations may widen it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "migrations" / "postgres"
CREATE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w.]+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
)
COLUMN = re.compile(
    r"^\s*([a-z][a-z0-9_]*_cents)\s+(INTEGER|BIGINT)\b", re.IGNORECASE | re.MULTILINE
)
ALTER = re.compile(r"ALTER\s+TABLE\s+([\w.]+)(.*?);", re.IGNORECASE | re.DOTALL)
ALTER_COLUMN = re.compile(
    r"ALTER\s+COLUMN\s+([a-z][a-z0-9_]*_cents)\s+TYPE\s+(INTEGER|BIGINT)\b",
    re.IGNORECASE,
)


def final_cents_types() -> dict[tuple[str, str], str]:
    types: dict[tuple[str, str], str] = {}
    for path in sorted(ROOT.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for table, body in CREATE.findall(sql):
            name = table.split(".")[-1].lower()
            for column, kind in COLUMN.findall(body):
                types[(name, column.lower())] = kind.upper()
        for table, body in ALTER.findall(sql):
            name = table.split(".")[-1].lower()
            for column, kind in ALTER_COLUMN.findall(body):
                types[(name, column.lower())] = kind.upper()
    return types


def test_every_cents_column_is_finally_bigint():
    wrong = {
        f"{table}.{column}": kind
        for (table, column), kind in final_cents_types().items()
        if kind != "BIGINT"
    }
    assert not wrong, f"PostgreSQL *_cents columns still 32-bit: {wrong}"
