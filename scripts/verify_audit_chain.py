"""Read-only audit hash-chain verifier (OPT03-B).

Prints only the first broken row id and short digest prefixes; never audit details.
Exit code 0 means every per-firm chain re-computes, 1 means a break was found.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class Break:
    firm_id: str
    row_id: object
    reason: str
    prev_prefix: str
    hash_prefix: str


def _prefix(value) -> str:
    return (value or "")[:8]


def _sqlite_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _sqlite_row_hash(conn, prev, row) -> str:
    return conn.execute(
        "SELECT accounting_sha256(json_array(?,?,?,?,?,?,?,?,?,?))",
        (
            prev,
            row["id"],
            row["firm_id"],
            row["book_id"],
            row["company_id"],
            row["user_id"],
            row["action"],
            row["object_id"],
            row["details"],
            row["created_at"],
        ),
    ).fetchone()[0]


def verify_sqlite(path: str) -> list[Break]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.create_function("accounting_sha256", 1, _sqlite_sha256, deterministic=True)
    try:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY firm_id,id").fetchall()
    finally:
        conn.close()
    breaks = []
    last_firm = None
    expected_prev = ""
    for row in rows:
        if row["firm_id"] != last_firm:
            last_firm = row["firm_id"]
            expected_prev = ""
        if row["prev_hash"] != expected_prev:
            breaks.append(
                Break(
                    row["firm_id"],
                    row["id"],
                    "prev-hash-mismatch",
                    _prefix(row["prev_hash"]),
                    _prefix(row["row_hash"]),
                )
            )
            continue
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.create_function("accounting_sha256", 1, _sqlite_sha256, deterministic=True)
        expected = _sqlite_row_hash(conn, expected_prev, row)
        conn.close()
        if row["row_hash"] != expected:
            breaks.append(
                Break(
                    row["firm_id"],
                    row["id"],
                    "row-hash-mismatch",
                    _prefix(row["prev_hash"]),
                    _prefix(row["row_hash"]),
                )
            )
            continue
        expected_prev = row["row_hash"]
    return breaks


def verify_postgres(dsn: str, firm_id: str | None = None) -> list[Break]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        where = "WHERE firm_id=%s" if firm_id else ""
        params = (firm_id,) if firm_id else ()
        rows = conn.execute(
            "SELECT id,firm_id,book_id,company_id,user_id,action,object_id,details,created_at,prev_hash,row_hash "
            f"FROM accounting.audit_events {where} ORDER BY firm_id,id",
            params,
        ).fetchall()
        result = []
        last_firm = None
        expected_prev = ""
        for row in rows:
            (
                row_id,
                firm,
                book,
                company,
                user,
                action,
                object_id,
                details,
                created,
                prev_hash,
                row_hash,
            ) = row
            if firm != last_firm:
                last_firm = firm
                expected_prev = ""
            if prev_hash != expected_prev:
                result.append(
                    Break(firm, row_id, "prev-hash-mismatch", _prefix(prev_hash), _prefix(row_hash))
                )
                continue
            expected = conn.execute(
                "SELECT accounting.audit_event_hash(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    expected_prev,
                    row_id,
                    firm,
                    book,
                    company,
                    user,
                    action,
                    object_id,
                    details,
                    created,
                ),
            ).fetchone()[0]
            if row_hash != expected:
                result.append(
                    Break(firm, row_id, "row-hash-mismatch", _prefix(prev_hash), _prefix(row_hash))
                )
                continue
            expected_prev = row_hash
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sqlite", help="local SQLite database path")
    group.add_argument("--postgres", help="read-only PostgreSQL DSN")
    parser.add_argument("--firm", help="limit PG verification to one firm id")
    args = parser.parse_args(argv)
    try:
        breaks = (
            verify_sqlite(args.sqlite) if args.sqlite else verify_postgres(args.postgres, args.firm)
        )
    except Exception as exc:  # noqa: BLE001 - CLI reports a compact diagnostic
        print(f"ERROR {type(exc).__name__}: {exc}")
        return 2
    if not breaks:
        print("OK audit chain")
        return 0
    for item in breaks[:1]:
        print(
            f"BREAK id={item.row_id} firm={item.firm_id[:8]} reason={item.reason} "
            f"prev={item.prev_prefix} hash={item.hash_prefix}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
