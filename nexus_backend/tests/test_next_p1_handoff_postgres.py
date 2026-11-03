"""Restricted-role P1 assignments, immutable history and source binding."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from app.handoff.service import HandoffService
from test_tax_workpaper_postgres import pg, case, native, ADMIN_URL  # noqa: F401
from test_dcp_r123_postgres import worker_db, batches, ready  # noqa: F401
from test_n03_notification_postgres import (
    p0_pg,
    p0_case,
    package_case,
    notification_case,
    native as notification_native,
)  # noqa: F401
from test_n03_worker_postgres import claim, begin, finish


def export_failure(c, worker_db):
    s = batches(c, worker_db)
    plan = s.preview_review_export(c.maker, [c.book["company_id"]], "2026-10")
    batch = s.create_review_export(
        c.maker, [c.book["company_id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    ready(c, batch)
    claim = s.claim("p1-worker")
    assert s.finish(claim, "failed", error_code="synthetic")
    return claim["item_id"]


def test_forward_guards_reapply_without_changing_history_or_privileges(case):
    c = case
    assigned = HandoffService(c.db).assign(
        c.maker, c.paper_id, 1, c.reviewer.user_id, None, "保留独立复核依据"
    )
    query = "SELECT relrowsecurity,relforcerowsecurity,relacl FROM pg_class WHERE oid='accounting.handoff_tasks'::regclass"
    with c.psycopg.connect(ADMIN_URL) as conn:
        before = conn.execute(query).fetchone()
        for _ in range(2):
            for name in ("213_handoff_source_fingerprint.sql", "214_handoff_notification_kind.sql"):
                conn.execute(
                    (Path(__file__).resolve().parents[1] / "migrations/postgres" / name).read_text(
                        encoding="utf-8"
                    )
                )
        assert conn.execute(query).fetchone() == before
    with native(c, c.maker) as conn:
        row = conn.execute(
            "SELECT assignee_id,superseded_at FROM handoff_tasks WHERE id=?", (assigned["id"],)
        ).fetchone()
    assert row["assignee_id"] == c.reviewer.user_id and row["superseded_at"] is None


def test_failed_export_without_result_can_assign_exact_frozen_book(case, worker_db):
    c = case
    item_id = export_failure(c, worker_db)
    h = HandoffService(c.db)
    source = next(x for x in h.queue(c.maker)["items"] if x["source"]["id"] == item_id)
    assigned = h.assign(
        c.maker,
        item_id,
        source["source"]["version"],
        c.maker.user_id,
        None,
        "核查导出失败",
        kind="batch_item",
        expected_fingerprint=source["source"]["fingerprint"],
    )
    assert assigned["scope"]["book_id"] == c.book["id"]


@pytest.mark.parametrize("bad", ["book", "fingerprint"])
def test_native_failed_item_cannot_bind_other_book_or_forged_digest(case, worker_db, bad):
    c = case
    item_id = export_failure(c, worker_db)
    h = HandoffService(c.db)
    source = next(x for x in h.queue(c.maker)["items"] if x["source"]["id"] == item_id)
    with pytest.raises(c.psycopg.Error) as error:
        with native(c, c.maker) as conn:
            conn.execute(
                "INSERT INTO handoff_tasks(id,firm_id,source_kind,source_id,source_version,source_fingerprint,book_id,role,assignee_id,next_action,status,reason,created_by,created_at,updated_at) VALUES(?,?,'batch_item',?,?,?,?,'ops',?,'核查','assigned','核查失败项',?,'now','now')",
                (
                    uuid4().hex,
                    c.maker.firm_id,
                    item_id,
                    source["source"]["version"],
                    "f" * 64 if bad == "fingerprint" else source["source"]["fingerprint"],
                    c.other["id"] if bad == "book" else c.book["id"],
                    c.maker.user_id,
                    c.maker.user_id,
                ),
            )
    assert error.value.sqlstate == "23514"


def test_concurrent_identical_deadline_change_has_one_current_history(case):
    c = case
    h = HandoffService(c.db)
    original = h.assign(c.maker, c.paper_id, 1, c.reviewer.user_id, "2026-11-10", "安排独立复核")
    start = Barrier(2)

    def update():
        start.wait(timeout=10)
        return h.assign(
            c.maker,
            c.paper_id,
            1,
            c.reviewer.user_id,
            "2026-11-12",
            "更新复核期限",
            expected_assignment_id=original["id"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: update(), range(2)))
    assert results[0]["id"] == results[1]["id"] != original["id"]
    with native(c, c.maker) as conn:
        rows = conn.execute(
            "SELECT id,due_on,superseded_at FROM handoff_tasks WHERE source_id=?", (c.paper_id,)
        ).fetchall()
    assert len(rows) == 2
    assert sum(row["superseded_at"] is None for row in rows) == 1


def test_unknown_notification_assignment_keeps_attempt_and_checks_native_digest(notification_case):
    c = notification_case
    sent = claim(c)
    assert begin(c, sent)
    assert finish(c, sent, "unknown")
    h = HandoffService(c.db)
    item = next(row for row in h.queue(c.reviewer)["items"] if row["source"]["id"] == sent["id"])
    with pytest.raises(c.psycopg.Error) as error:
        with notification_native(c, c.reviewer) as conn:
            conn.execute(
                "INSERT INTO handoff_tasks(id,firm_id,source_kind,source_id,source_version,source_fingerprint,book_id,role,assignee_id,next_action,status,reason,created_by,created_at,updated_at) VALUES(?,?,'notification_investigation',?,?,?,?,'ops',?,'核查','assigned','核查发送结果',?,'now','now')",
                (
                    uuid4().hex,
                    c.reviewer.firm_id,
                    sent["id"],
                    item["source"]["version"],
                    "f" * 64,
                    c.book["id"],
                    c.reviewer.user_id,
                    c.reviewer.user_id,
                ),
            )
    assert error.value.sqlstate == "23514"
    assigned = h.assign(
        c.reviewer,
        sent["id"],
        item["source"]["version"],
        c.reviewer.user_id,
        "2026-11-12",
        "人工调查外发结果",
        kind="notification_investigation",
        expected_fingerprint=item["source"]["fingerprint"],
    )
    assert "不自动重发" in assigned["next_action"]
    with notification_native(c, c.reviewer) as conn:
        state = conn.execute(
            "SELECT state,attempts FROM delivery_notifications WHERE id=?", (sent["id"],)
        ).fetchone()
    assert state["state"] == "unknown" and state["attempts"] == 1
