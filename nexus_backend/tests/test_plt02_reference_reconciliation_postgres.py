"""PLT02-E: reconciliation notes on PostgreSQL, with RLS and append-only guard."""

import csv
import io

import psycopg
import pytest

from app.reports.models import ReportDifferenceNote, ReportReconcile
from app.reports.service import ReportService
from test_n03_notification_postgres import p0_case, p0_pg  # noqa: F401
from test_p02_formal_read_postgres import formal_case  # noqa: F401


def test_reference_reconciliation_and_explanation_postgres(formal_case):
    c = formal_case
    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")["report"]
    with c.db.transaction(actor=c.manager) as conn:
        content = reports._view(conn, report["id"])["content"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["报表", "行次", "行名", "金额"])
    writer.writerow(
        ["资产负债表", "1", "资产合计", f"{content['balance_sheet']['assets'] / 100:.2f}"]
    )
    writer.writerow(
        ["资产负债表", "2", "负债合计", f"{content['balance_sheet']['liabilities'] / 100 + 0.01:.2f}"]
    )
    view = reports.reconcile(
        c.manager,
        report["id"],
        ReportReconcile(reference_name="pg-reference.csv", csv_text=output.getvalue()),
    )
    assert view["counts"]["amount_different"] == 1
    assert view["counts"]["missing_reference"] == 10
    difference = next(row for row in view["rows"] if row["status"] == "amount_different")
    assert difference["difference_cents"] == 1
    assert difference["drilldown"]

    note = reports.explain_difference(
        c.manager,
        report["id"],
        ReportDifferenceNote(
            reference_sha256=view["reference_sha256"],
            line_key="balance_sheet.liabilities",
            explanation_type="rounding",
            note="PG 合成一分钱舍入差",
            reason="PLT02-E PG 演练",
        ),
    )
    assert note["created_by"] == c.manager.user_id
    listed = reports.reconciliation_notes(c.manager, report["id"], view["reference_sha256"])
    assert len(listed["notes"]) == 1
    with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE report_reconciliation_notes SET note='改' WHERE id=?", (note["id"],)
            )
