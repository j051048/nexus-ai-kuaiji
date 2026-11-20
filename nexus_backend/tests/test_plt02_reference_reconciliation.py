"""PLT02-E: reference report CSV, line-level differences, append-only explanations."""

import csv
import io
import time

import pytest
from pydantic import ValidationError

from app.accounting.domain import AccountingError
from app.auth.supabase import Identity
from app.main import create_app
from app.reports.models import ReportCommand, ReportDifferenceNote, ReportReconcile
from app.reports.reconciliation import compare
from app.reports.service import ReportService
from test_business import business  # noqa: F401
from test_business_api import local_client
from test_plt02_report_modes import _prepared


def _csv(content, *, liability_plus=0, profit_plus=0, omit=()):
    rows = [
        ("资产负债表", "1", "资产合计", content["balance_sheet"]["assets"]),
        ("资产负债表", "2", "负债合计", content["balance_sheet"]["liabilities"] + liability_plus),
        ("资产负债表", "3", "所有者权益合计", content["balance_sheet"]["equity"]),
        ("利润表", "1", "营业收入", content["profit_statement"]["revenue_cents"]),
        ("利润表", "2", "成本费用", content["profit_statement"]["expense_cents"]),
        ("利润表", "3", "利润", content["profit_statement"]["profit_cents"] + profit_plus),
        ("现金流量表", "1", "期初现金", content["cash_flow"]["opening_cash_cents"]),
        ("现金流量表", "2", "期末现金", content["cash_flow"]["closing_cash_cents"]),
        ("现金流量表", "3", "净变动", content["cash_flow"]["net_change_cents"]),
        ("现金流量表", "4", "经营活动", content["cash_flow"]["operating_cents"]),
        ("现金流量表", "5", "投资活动", content["cash_flow"]["investing_cents"]),
        ("现金流量表", "6", "筹资活动", content["cash_flow"]["financing_cents"]),
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["报表", "行次", "行名", "金额"])
    for section, line_no, name, cents in rows:
        if (section, name) in omit:
            continue
        writer.writerow([section, line_no, name, f"{cents / 100:.2f}"])
    return output.getvalue()


def test_three_differences_are_explained_without_touching_report(business):
    case = business
    _, _ = _prepared(case)
    service = ReportService(case.db)
    report = service.create(case.manager, case.book["id"], "2026-10")["report"]
    with case.db.transaction() as conn:
        content = service._view(conn, report["id"])["content"]
    view = service.reconcile(
        case.manager,
        report["id"],
        ReportReconcile(
            reference_name="客户已确认报表-2026-10.csv",
            csv_text=_csv(
                content,
                liability_plus=1,
                profit_plus=-100,
                omit=(("现金流量表", "期末现金"),),
            ),
        ),
    )
    assert view["counts"]["amount_different"] == 2
    assert view["counts"]["missing_reference"] == 1
    assert view["counts"]["matched"] == 9
    assert view["counts"]["missing_system"] == 0
    assert len(view["reference_sha256"]) == 64
    changed = {row["line_key"]: row for row in view["rows"] if row["status"] == "amount_different"}
    assert changed["balance_sheet.liabilities"]["difference_cents"] == 1
    assert changed["balance_sheet.liabilities"]["drilldown"]
    assert changed["profit_statement.profit_cents"]["drilldown"]
    missing = next(row for row in view["rows"] if row["status"] == "missing_reference")
    assert missing["line_key"] == "cash_flow.closing_cash_cents"

    notes = []
    for line_key, explanation_type, note in (
        ("balance_sheet.liabilities", "rounding", "客户报表为整数元，系统按分取整"),
        ("profit_statement.profit_cents", "reclassification", "客户将一笔费用重分类至营业外"),
        ("cash_flow.closing_cash_cents", "unknown", "客户未提供该行，待查"),
    ):
        notes.append(
            service.explain_difference(
                case.manager,
                report["id"],
                ReportDifferenceNote(
                    reference_sha256=view["reference_sha256"],
                    line_key=line_key,
                    explanation_type=explanation_type,
                    note=note,
                    reason="PLT02-E 合成差异解释",
                ),
            )
        )
    assert len(notes) == 3
    listed = service.reconciliation_notes(case.manager, report["id"], view["reference_sha256"])
    assert [row["explanation_type"] for row in listed["notes"]] == [
        "rounding",
        "reclassification",
        "unknown",
    ]
    with case.db.transaction() as conn:
        assert conn.execute("SELECT status FROM report_runs WHERE id=?", (report["id"],)).fetchone()[0] == "draft"
        audit = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='report.reconciliation.explain'"
        ).fetchone()[0]
    assert audit == 3


def test_notes_are_append_only(business):
    case = business
    _, _ = _prepared(case)
    service = ReportService(case.db)
    report = service.create(case.manager, case.book["id"], "2026-10")["report"]
    with case.db.transaction() as conn:
        content = service._view(conn, report["id"])["content"]
    view = service.reconcile(
        case.manager,
        report["id"],
        ReportReconcile(reference_name="r.csv", csv_text=_csv(content)),
    )
    note = service.explain_difference(
        case.manager,
        report["id"],
        ReportDifferenceNote(
            reference_sha256=view["reference_sha256"],
            line_key="balance_sheet.assets",
            explanation_type="unknown",
            note="待查",
            reason="PLT02-E 追加测试",
        ),
    )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="不可修改"):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE report_reconciliation_notes SET note='改' WHERE id=?", (note["id"],)
            )
    with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
        with case.db.transaction(write=True) as conn:
            conn.execute("DELETE FROM report_reconciliation_notes WHERE id=?", (note["id"],))


def test_unknown_csv_line_and_explanation_type_are_rejected():
    with pytest.raises(AccountingError, match="无法识别"):
        compare(
            "报表,行次,行名,金额\n资产负债表,1,未定义行,0.00\n",
            {"balance_sheet": {}, "profit_statement": {}, "cash_flow": {}},
        )
    with pytest.raises(ValidationError):
        ReportDifferenceNote(
            reference_sha256="a" * 64,
            line_key="balance_sheet.assets",
            explanation_type="ignore",
            note="忽略",
            reason="不允许忽略差异",
        )


def test_missing_system_line_is_reported_when_reference_has_it():
    view = compare(
        "报表,行次,行名,金额\n现金流量表,2,期末现金,100.00\n",
        {"balance_sheet": {}, "profit_statement": {}, "cash_flow": {}},
    )
    assert view["rows"][0]["status"] == "missing_system"
    assert view["rows"][0]["reference_cents"] == 10000
    assert view["counts"]["missing_system"] == 1


def test_reconciliation_routes_are_wired(business):
    case = business
    _prepared(case)
    app = create_app(case.db.path, seed=False)
    token = "plt02e-route-token"
    with local_client(app, 45682) as client:
        app.state.tokens[token] = time.monotonic() + 60
        app.state.token_users[token] = Identity(
            case.manager.user_id, "manager@example.test", "PLT02 经理"
        )
        actor = app.state.directory.resolve_actor(case.manager.user_id, case.manager.firm_id)
        report = app.state.reports.create(actor, case.book["id"], "2026-10")["report"]
        with app.state.service.db.transaction(actor=actor) as conn:
            content = app.state.reports._view(conn, report["id"])["content"]
        headers = {"Authorization": "Bearer " + token, "X-Firm-Id": case.manager.firm_id}
        csv_text = _csv(content, liability_plus=1)
        reconciled = client.post(
            f"/api/reports/{report['id']}/reconcile",
            headers=headers,
            json={"reference_name": "客户参考报表.csv", "csv_text": csv_text},
        )
        assert reconciled.status_code == 200, reconciled.text
        view = reconciled.json()
        assert view["counts"]["amount_different"] == 1
        digest = view["reference_sha256"]
        note = client.post(
            f"/api/reports/{report['id']}/reconciliation-notes",
            headers=headers,
            json={
                "reference_sha256": digest,
                "line_key": "balance_sheet.liabilities",
                "explanation_type": "rounding",
                "note": "客户报表按元整数填报",
                "reason": "PLT02-E HTTP 合成",
            },
        )
        assert note.status_code == 201, note.text
        listed = client.get(
            f"/api/reports/{report['id']}/reconciliation-notes",
            headers=headers,
            params={"reference_sha256": digest},
        )
        assert listed.status_code == 200, listed.text
        assert [row["explanation_type"] for row in listed.json()["notes"]] == ["rounding"]


def test_profit_differences_drill_into_the_matching_profit_accounts():
    content = {
        "profit_statement": {
            "revenue_cents": 10000,
            "expense_cents": 5000,
            "profit_cents": 5000,
            "items": [
                {"code": "6001", "name": "主营业务收入", "amount_cents": 10000, "line_code": "revenue"},
                {"code": "6401", "name": "主营业务成本", "amount_cents": 5000, "line_code": "cost"},
            ],
        },
        "cash_flow": {
            "opening_cash_cents": 100,
            "closing_cash_cents": 1000,
            "net_change_cents": 900,
        },
    }
    view = compare(
        "报表,行次,行名,金额\n利润表,1,营业收入,100.01\n利润表,2,成本费用,50.01\n",
        content,
    )
    rows = {row["line_key"]: row for row in view["rows"]}
    income = rows["profit_statement.revenue_cents"]
    assert income["section"] == "profit_statement"
    assert [item["code"] for item in income["drilldown"]] == ["6001"]
    expense = rows["profit_statement.expense_cents"]
    assert expense["section"] == "profit_statement"
    assert [item["code"] for item in expense["drilldown"]] == ["6401"]


def test_mapping_change_invalidates_existing_formal_report(business):
    case = business
    _prepared(case)
    service = ReportService(case.db)
    report = service.create(case.manager, case.book["id"], "2026-10")["report"]
    with case.db.transaction() as conn:
        content = service._view(conn, report["id"])["content"]
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO report_line_mappings(id,firm_id,template_id,template_version,version,"
            "line_code,line_name,line_kind,account_prefix,sign,active,created_by,created_at) "
            "VALUES(?,?,?,?,2,'revenue','营业收入','revenue','6001',1,1,?,?)",
            (
                "mapping-change-1",
                case.manager.firm_id,
                "small_enterprise_basic",
                1,
                case.manager.user_id,
                "2026-10-31T00:00:00Z",
            ),
        )
    with pytest.raises(AccountingError, match="映射已变化"):
        service.reconcile(
            case.manager,
            report["id"],
            ReportReconcile(reference_name="r.csv", csv_text=_csv(content)),
        )
    with pytest.raises(AccountingError, match="映射已变化"):
        service.explain_difference(
            case.manager,
            report["id"],
            ReportDifferenceNote(
                reference_sha256="a" * 64,
                line_key="balance_sheet.assets",
                explanation_type="unknown",
                note="待查",
                reason="PLT02-E 映射变化",
            ),
        )
    with pytest.raises(AccountingError, match="映射已变化"):
        service.review(
            case.manager,
            report["id"],
            ReportCommand(expected_version=report["version"], reason="PLT02-E 映射变化"),
        )


def test_explanation_is_bound_to_report_book_and_review_permission(business):
    case = business
    _prepared(case)
    service = ReportService(case.db)
    report = service.create(case.manager, case.book["id"], "2026-10")["report"]
    with case.db.transaction() as conn:
        content = service._view(conn, report["id"])["content"]
    view = service.reconcile(
        case.manager,
        report["id"],
        ReportReconcile(reference_name="r.csv", csv_text=_csv(content)),
    )
    note = ReportDifferenceNote(
        reference_sha256=view["reference_sha256"],
        line_key="balance_sheet.assets",
        explanation_type="unknown",
        note="待查",
        reason="PLT02-E 权限绑定",
    )
    with pytest.raises(AccountingError):
        service.explain_difference(case.actors["employee"], report["id"], note)
    saved = service.explain_difference(case.manager, report["id"], note)
    with case.db.transaction() as conn:
        row = conn.execute(
            "SELECT report_id,book_id,firm_id FROM report_reconciliation_notes WHERE id=?",
            (saved["id"],),
        ).fetchone()
    assert row["report_id"] == report["id"]
    assert row["book_id"] == case.book["id"]
    assert row["firm_id"] == case.manager.firm_id
