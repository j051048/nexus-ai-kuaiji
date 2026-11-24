"""PLT02-E: reconciliation notes on PostgreSQL, with RLS and append-only guard."""

import csv
import io
from uuid import uuid4

import psycopg
import pytest

from app.accounting.domain import Actor
from app.reports.models import ReportDifferenceNote, ReportReconcile
from app.reports.service import ReportService
from test_n03_notification_postgres import p0_case, p0_pg  # noqa: F401
from test_p02_formal_read_postgres import formal_case  # noqa: F401
import os

pytestmark = pytest.mark.skipif(
    os.getenv("ACCOUNTING_AK05_ISOLATED_TARGET") != "1",
    reason="需要显式 AK05 四角色隔离 PostgreSQL 运行器",
)


def _book_editor(c):
    """A genuine editor: firm operator with book editor access, never a manager."""
    actor = Actor("plt02-editor-" + uuid4().hex, c.manager.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name) VALUES(%s,%s)",
            (actor.user_id, actor.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships VALUES(%s,%s,'operator')",
            (c.manager.firm_id, actor.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.book_access VALUES(%s,%s,%s,'editor')",
            (c.book["id"], c.manager.firm_id, actor.user_id),
        )
    return actor

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
        ["资产负债表", "1", "资产合计", f"{content['balance_sheet']['assets'] / 100 + 0.01:.2f}"]
    )
    writer.writerow(
        ["资产负债表", "2", "负债合计", f"{content['balance_sheet']['liabilities'] / 100:.2f}"]
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


def test_reconciliation_note_insert_requires_review_authority_and_same_report(formal_case):
    """P1-4: writing an explanation is a review act, and the report must match the book."""
    c = formal_case
    reports = ReportService(c.db)
    report = reports.create(c.manager, c.book["id"], "2026-10")["report"]
    with c.db.transaction(actor=c.manager) as conn:
        content = reports._view(conn, report["id"])["content"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["报表", "行次", "行名", "金额"])
    writer.writerow(["资产负债表", "1", "资产合计", f"{content['balance_sheet']['assets'] / 100:.2f}"])
    view = reports.reconcile(
        c.manager,
        report["id"],
        ReportReconcile(reference_name="pg-rls.csv", csv_text=output.getvalue()),
    )
    sha = view["reference_sha256"]
    stamp = "2026-10-31T12:00:00+00:00"

    def insert(actor, report_id, book_id, created_by, note_id):
        with c.db.transaction(write=True, actor=actor) as conn:
            conn.execute(
                "INSERT INTO report_reconciliation_notes"
                "(id,report_id,book_id,firm_id,reference_sha256,line_key,explanation_type,note,created_by,created_at) "
                "VALUES(?,?,?,?,?,'balance_sheet.assets','rounding','PG 层权限测试',?,?)",
                (note_id, report_id, book_id, actor.firm_id, sha, created_by, stamp),
            )

    # 编辑员没有复核权：数据库层直接拒绝写入解释。
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        insert(_book_editor(c), report["id"], c.book["id"], c.manager.user_id, "note-editor-" + uuid4().hex)

    # 复核员把解释挂到别的账套报表上：同样拒绝。
    other_book = "note-other-book-" + uuid4().hex
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.books(id,firm_id,company_id,name,period) "
            "VALUES(%s,%s,%s,'解释跨账套','2026-10')",
            (other_book, c.reader.firm_id, c.book["company_id"]),
        )
        other_report = "note-other-report-" + uuid4().hex
        conn.execute(
            "INSERT INTO accounting.report_runs(id,book_id,period,template_id,template_version,status,version,"
            "inputs_fingerprint,content_json,content_hash,created_by,created_at) "
            "VALUES(%s,%s,'2026-10','t',1,'validated',1,'fp','{}','h',%s,%s)",
            (other_report, other_book, c.manager.user_id, stamp),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        insert(c.reader, other_report, c.book["id"], c.reader.user_id, "note-cross-" + uuid4().hex)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        insert(c.reader, report["id"], c.book["id"], c.manager.user_id, "note-forged-" + uuid4().hex)

    # 授权复核员写自己的解释：允许，且仍不可改不可删（append-only 触发器）。
    note_id = "note-reviewer-" + uuid4().hex
    insert(c.reader, report["id"], c.book["id"], c.reader.user_id, note_id)
    with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)):
        with c.db.transaction(write=True, actor=c.reader) as conn:
            conn.execute("UPDATE report_reconciliation_notes SET note='改' WHERE id=?", (note_id,))
