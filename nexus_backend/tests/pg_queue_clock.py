"""Wait for a synthetic job's DB due time without changing its stored state.

Windows API fixtures and WSL PostgreSQL can differ by sub-second wall time.
Immediate-claim assertions require eligible jobs, not synchronized host clocks.
Only the fixture's exact IDs are read; no status, lease or timestamp is patched.
"""

import time


def wait_until_due(connect, url, table, ids, *, timeout=3, clock=time.monotonic, sleep=time.sleep):
    if table not in {"ocr_jobs", "proposal_jobs", "assistant_turns"}:
        raise ValueError("Unknown queue table")
    if (
        not ids
        or any(not isinstance(value, str) or not value for value in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Require distinct synthetic job IDs")
    deadline = clock() + timeout
    with connect(url) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        while True:
            due = conn.execute(
                "SELECT count(*) FROM accounting."
                + table
                + " WHERE id = ANY(%s) AND available_at <= accounting.ocr_utc_now()",
                (list(ids),),
            ).fetchone()[0]
            if due == len(ids):
                return
            if clock() >= deadline:
                raise AssertionError("Synthetic queue jobs did not reach database due time")
            sleep(0.01)
