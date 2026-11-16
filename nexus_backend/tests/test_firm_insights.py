"""Firm insights are a read-only, traceable management projection."""

from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


def local_client(app):
    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 7799)
        return await call_next(request)

    return TestClient(app, base_url="http://127.0.0.1")


def test_insights_overview_is_traceable_and_cost_separated(tmp_path):
    app = create_app(tmp_path / "insights.db", seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager = auth(client, "accountant")
        book = client.get("/api/workbench", headers=manager).json()["books"][0]
        response = client.get(
            "/api/firm/observability/insights",
            headers=manager,
            params={"period": book["period"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["period"] == book["period"]
        assert body["stage_projection"] is True
        assert "不自动关账" in body["note"]
        assert set(body["kpis"]) >= {
            "companies", "books", "blocked", "overdue", "closed",
            "review_pending", "known_reports", "missing_reports",
            "ai_cost_micro", "ai_cost_unknown",
        }
        assert len(body["stages"]) == 6
        assert [row["code"] for row in body["stages"]] == [
            "intake", "reconcile", "prepare", "review", "delivery", "archive"
        ]
        assert sum(row["count"] for row in body["stages"]) == body["kpis"]["books"]
        assert len(body["trends"]) == 6
        assert body["trends"][-1]["period"] == body["period"]
        assert body["financial"]["known_books"] + body["financial"]["missing_books"] == body["kpis"]["books"]
        assert body["financial"]["known_books"] == body["kpis"]["known_reports"]
        assert len(body["staff"]) <= 12
        assert all("owner_name" in item and "report_status" in item for item in body["risk"])

        employee = auth(client, "employee")
        denied = client.get(
            "/api/firm/observability/insights",
            headers=employee,
            params={"period": book["period"]},
        )
        assert denied.status_code == 403


def test_insights_empty_month_does_not_invent_zero_reports(tmp_path):
    app = create_app(tmp_path / "insights-empty.db", seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager = auth(client, "accountant")
        response = client.get(
            "/api/firm/observability/insights",
            headers=manager,
            params={"period": "2031-01"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kpis"]["books"] == 0
        assert body["kpis"]["known_reports"] == 0
        assert body["financial"]["known_books"] == 0
        assert body["trends"] == []
        assert body["note"].startswith("当前月份没有可汇总的账套")


def test_insights_uses_only_latest_reviewed_report_for_financials(tmp_path):
    app = create_app(tmp_path / "insights-report.db", seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager = auth(client, "accountant")
        book = client.get("/api/workbench", headers=manager).json()["books"][0]
        with app.state.service.db.connect() as conn:
            user_id = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
                "inputs_fingerprint,content_json,content_hash,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "insight-report-1", book["id"], book["period"], "synthetic", 1,
                    "reviewed", 1, "input", '{"balance_sheet":{"assets":12300,"liabilities":2300,'
                    '"equity":10000},"profit_statement":{"revenue_cents":50000,"expense_cents":20000,'
                    '"profit_cents":30000},"cash_flow":{"closing_cash_cents":7000,'
                    '"unclassified_cents":100}}', "hash", user_id, "2026-10-31T00:00:00+00:00",
                ),
            )
            conn.commit()
        response = client.get(
            "/api/firm/observability/insights", headers=manager,
            params={"period": book["period"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kpis"]["known_reports"] >= 1
        assert body["financial"]["known_books"] >= 1
        assert body["kpis"]["known_reports"] + body["kpis"]["missing_reports"] == body["kpis"]["books"]
        assert body["financial"]["assets_cents"] == 12300
        assert body["financial"]["profit_cents"] == 30000
        assert body["financial"]["unclassified_cash_cents"] == 100


def test_phase_two_and_three_drilldowns_are_traceable(tmp_path):
    app = create_app(tmp_path / "insights-drilldown.db", seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        manager = auth(client, "accountant")
        book = client.get("/api/workbench", headers=manager).json()["books"][0]
        with app.state.service.db.connect() as conn:
            user_id = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute(
                "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
                "inputs_fingerprint,content_json,content_hash,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "insight-drill-report", book["id"], book["period"], "synthetic", 1,
                    "reviewed", 1, "input",
                    '{"balance_sheet":{"assets":30000,"liabilities":10000,"equity":20000,'
                    '"items":[{"section":"asset","code":"1122","name":"应收账款","amount_cents":4000},'
                    '{"section":"liability","code":"2202","name":"应付账款","amount_cents":2500}]},'
                    '"profit_statement":{"revenue_cents":50000,"expense_cents":20000,"profit_cents":30000,'
                    '"items":[{"code":"6001","name":"收入","amount_cents":50000}]},'
                    '"cash_flow":{"closing_cash_cents":7000},"checks":{"unclassified_cash_cents":0}}',
                    "hash", user_id, "2026-10-31T00:00:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO books(id,firm_id,company_id,period,name) "
                "SELECT ?,firm_id,company_id,'2026-09',name FROM books WHERE id=?",
                ("insight-prev-book", book["id"]),
            )
            conn.execute(
                "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
                "inputs_fingerprint,content_json,content_hash,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "insight-prev-report", "insight-prev-book", "2026-09", "synthetic", 1,
                    "reviewed", 1, "input",
                    '{"balance_sheet":{"assets":0,"liabilities":0,"equity":0,"items":[]},'
                    '"profit_statement":{"revenue_cents":40000,"expense_cents":15000,"profit_cents":25000,'
                    '"items":[]},"cash_flow":{"closing_cash_cents":0},"checks":{}}',
                    "hash", user_id, "2026-09-30T00:00:00+00:00",
                ),
            )
            conn.commit()
        detail = client.get(
            "/api/firm/observability/insights/company",
            headers=manager, params={"book_id": book["id"]},
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["company_name"] == book["company_name"]
        assert body["balance"]["assets_cents"] == 30000
        assert body["working_capital"] == {"receivables_cents": 4000, "payables_cents": 2500}
        assert len(body["history"]) >= 1

        comparison = client.get(
            "/api/firm/observability/insights/companies",
            headers=manager, params={"period": book["period"]},
        )
        assert comparison.status_code == 200, comparison.text
        rows = comparison.json()["rows"]
        assert any(row["book_id"] == book["id"] and row["assets_cents"] == 30000 for row in rows)
        current = next(row for row in rows if row["book_id"] == book["id"])
        assert comparison.json()["compare_period"] == "2026-09"
        assert current["previous_known"] is True
        assert current["revenue_delta_cents"] == 10000
        assert current["profit_delta_cents"] == 5000

        timeline = client.get(
            "/api/firm/observability/insights/timeline",
            headers=manager, params={"period": book["period"]},
        )
        assert timeline.status_code == 200, timeline.text
        assert any(row["book_id"] == book["id"] for row in timeline.json()["rows"])

        employee = auth(client, "employee")
        denied = client.get(
            "/api/firm/observability/insights/company",
            headers=employee, params={"book_id": book["id"]},
        )
        assert denied.status_code == 403
