"""Batched intelligence lookups keep real PostgreSQL RLS and current reviews."""

import pytest

from app.accounting.domain import AccountingError
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
from test_intelligence_workflow import create_event, extract
from test_intelligence_workflow_postgres import pg_ai  # noqa: F401

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接"
)


def assessment(case, actor, event):
    with case.db.transaction(actor=actor) as conn:
        current = case.business._event(conn, actor, event["id"])
        return case.intelligence._assessment(conn, actor, current)


def test_pg_duplicate_scan_keeps_employee_scope_and_current_reviews(pg_ai):
    c = pg_ai
    event, peer = create_event(c, amount="0.40"), create_event(c, actor="peer")
    extract(c, event)
    peer_run = extract(c, peer, actor="peer")
    employee = assessment(c, c.actors["employee"], event)
    assert not any(row["code"] == "duplicate_invoice_other_event" for row in employee["findings"])
    detail = c.intelligence.reconcile(c.actors["finance"], event["id"])
    duplicate = next(
        row for row in detail["findings"] if row["code"] == "duplicate_invoice_other_event"
    )
    assert duplicate["payload"]["match_count"] == 1
    assert peer_run["id"] not in str(duplicate)
    mismatch = next(row for row in detail["findings"] if row["code"] == "document_business_amount")
    c.intelligence.resolve(c.actors["finance"], mismatch["id"], "合成差异已独立核对")
    current = assessment(c, c.actors["finance"], event)
    reviewed = next(row for row in current["findings"] if row["id"] == mismatch["id"])
    assert reviewed["resolved"] and reviewed["resolution_reason"] == "合成差异已独立核对"
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.department_approvers WHERE user_id=%s",
            (c.actors["approver"].user_id,),
        )
    with pytest.raises(AccountingError) as revoked:
        assessment(c, c.actors["approver"], event)
    assert revoked.value.status == 404
