"""Capacity guard for the firm insights overview at 240 books."""

import statistics
import time

from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


def local_client(app):
    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 7801)
        return await call_next(request)

    return TestClient(app, base_url="http://127.0.0.1")


def test_insights_overview_stays_under_500ms_at_240_books(tmp_path):
    app = create_app(tmp_path / "insights-capacity.db", seed=True, attachment_root=tmp_path / "originals")
    period = "2031-03"
    with local_client(app) as client:
        manager = auth(client, "accountant")
        seed_book = client.get("/api/workbench", headers=manager).json()["books"][0]
        with app.state.service.db.connect() as conn:
            user_id = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
            firm_id = conn.execute("SELECT firm_id FROM books WHERE id=?", (seed_book["id"],)).fetchone()["firm_id"]
            for index in range(240):
                company_id = f"capacity-company-{index:03d}"
                book_id = f"capacity-book-{index:03d}"
                conn.execute(
                    "INSERT INTO companies(id,name,tax_id) VALUES(?,?,?)",
                    (company_id, f"容量虚构企业 {index:03d}", f"CAP{index:03d}"),
                )
                conn.execute(
                    "INSERT INTO firm_clients(firm_id,company_id) VALUES(?,?)",
                    (firm_id, company_id),
                )
                conn.execute(
                    "INSERT INTO books(id,firm_id,company_id,name,period) VALUES(?,?,?,?,?)",
                    (book_id, firm_id, company_id, f"容量账套 {index:03d}", period),
                )
                conn.execute(
                    "INSERT INTO periods(book_id,period,status) VALUES(?,?,'open')",
                    (book_id, period),
                )
                if index % 2 == 0:
                    conn.execute(
                        "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,version,"
                        "inputs_fingerprint,content_json,content_hash,created_by,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            f"capacity-report-{index:03d}", book_id, period, "synthetic", 1,
                            "reviewed", 1, "input",
                            '{"balance_sheet":{"assets":100000,"liabilities":40000,"equity":60000},'
                            '"profit_statement":{"revenue_cents":50000,"expense_cents":20000,"profit_cents":30000},'
                            '"cash_flow":{"closing_cash_cents":12000}}',
                            f"hash-{index:03d}", user_id, "2031-03-31T00:00:00+00:00",
                        ),
                    )
            conn.commit()

        params = {"period": period}
        for _ in range(3):
            assert client.get("/api/firm/observability/insights", headers=manager, params=params).status_code == 200
        samples = []
        for _ in range(30):
            started = time.perf_counter()
            response = client.get("/api/firm/observability/insights", headers=manager, params=params)
            samples.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200
        samples.sort()
        p50 = statistics.median(samples)
        p95 = samples[int(len(samples) * 0.95) - 1]
        assert response.json()["kpis"]["books"] >= 240
        assert p50 < 500, f"p50 {p50:.1f}ms"
        assert p95 < 500, f"p95 {p95:.1f}ms"
        print(f"insights capacity books=240 p50={p50:.1f}ms p95={p95:.1f}ms")
