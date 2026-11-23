"""FDE value baseline: deterministic rates and explicit labour-time boundary."""

from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


def local_client(app):
    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 7788)
        return await call_next(request)

    return TestClient(app, base_url="http://127.0.0.1")


def test_value_baseline_reports_rates_and_refuses_to_invent_labour_time(tmp_path):
    app = create_app(tmp_path / "value.db", seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        accountant = auth(client, "accountant")
        book = client.get("/api/workbench", headers=accountant).json()["books"][0]
        response = client.get(
            "/api/firm/observability/value",
            headers=accountant,
            params={"company_id": book["company_id"], "period": book["period"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"] == {
            "submitted_events": 0,
            "mapped_drafts": 0,
            "approved_drafts": 0,
            "returned_events": 0,
            "return_actions": 0,
        }
        assert body["rates"] == {
            "draft_mapping_rate": None,
            "first_pass_rate": None,
            "exception_rate": None,
            "review_conversion_rate": None,
        }
        assert body["elapsed"]["labour_time_verified"] is False
        assert body["elapsed"]["system_elapsed_hours_median"] is None
        assert body["cost"]["ai_model_cost_micro"] == 0
        assert body["cost"]["ai_model_cost_complete"] is True
        assert body["cost"]["scope"] == "ai_model_only"
        assert "不代表人工工时" in body["elapsed"]["note"]

        bad = client.get(
            "/api/firm/observability/value",
            headers=accountant,
            params={"company_id": book["company_id"], "period": "2026-13"},
        )
        assert bad.status_code == 422


def test_value_baseline_uses_distinct_return_objects_and_keeps_exported_approvals(tmp_path):
    from app.accounting.database import Database

    path = tmp_path / "value-cohort.db"
    app = create_app(path, seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        accountant = auth(client, "accountant")
        book = client.get("/api/workbench", headers=accountant).json()["books"][0]
        import sqlite3
        with sqlite3.connect(path) as raw:
            raw.execute('DROP TRIGGER IF EXISTS rr_draft_insert_guard')
            raw.execute('DROP TRIGGER IF EXISTS rr_draft_insert')
        with Database(path).connect() as conn:
            employee = conn.execute("SELECT id FROM users WHERE name LIKE '%员工%' LIMIT 1").fetchone()[0]
            firm_id, company_id, book_id, period = conn.execute(
                "SELECT firm_id,company_id,id,period FROM books WHERE id=?", (book["id"],)
            ).fetchone()
            for index in (1, 2, 3):
                event_id = f"evt-{index}"
                conn.execute(
                    "INSERT INTO business_events(id,firm_id,company_id,applicant_id,template_id,"
                    "template_version,version,status,payload,submitted_version,created_at,updated_at) "
                    "VALUES(?,?,?,?,'reimbursement',1,1,'submitted','{}',1,?,?)",
                    (event_id, firm_id, company_id, employee, f"{period}-05T09:00:00+00:00", f"{period}-05T09:00:00+00:00"),
                )
                conn.execute(
                    "INSERT INTO business_versions(event_id,firm_id,company_id,event_version,payload,"
                    "template_snapshot,attachment_snapshot,created_at,created_by) VALUES(?,?,?,1,'{}','{}','[]',?,?)",
                    (event_id, firm_id, company_id, f"{period}-05T09:00:00+00:00", employee),
                )
            for index in range(3):
                conn.execute(
                    "INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,"
                    "decision,reason,created_by,created_at) VALUES(?,?,?,?,1,'return','return','补资料',?,?)",
                    (f"act-{index}", "evt-1", firm_id, company_id, employee, f"{period}-10T09:00:00+00:00"),
                )
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,"
                "amount_cents,direction,evidence_ref,fingerprint) VALUES('src-metric',?, 'metric-1','demo','2026-10-01','income','customer',10000,'income','ev','fp')",
                (book_id,),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation,approved_by,approved_at,exported_at) "
                "VALUES('draft-metric',?, 'src-metric','测试','exported',1,'','?',?,?)"
                .replace("'?'", "?"),
                (book_id, employee, f"{period}-20T09:00:00+00:00", f"{period}-21T09:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO business_mappings(event_id,firm_id,company_id,book_id,source_id,draft_id,"
                "submitted_version,idempotency_key,request_fingerprint,created_by,created_at) "
                "VALUES('evt-2',?,?,?, 'src-metric','draft-metric',1,'map-metric','fp',?,?)",
                (firm_id, company_id, book_id, employee, f"{period}-11T09:00:00+00:00"),
            )
            conn.commit()
        response = client.get(
            "/api/firm/observability/value",
            headers=accountant,
            params={"company_id": book["company_id"], "period": book["period"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"]["submitted_events"] == 3
        assert body["counts"]["mapped_drafts"] == 1
        assert body["counts"]["approved_drafts"] == 1
        assert body["counts"]["returned_events"] == 1
        assert body["counts"]["return_actions"] == 3
        assert body["rates"]["first_pass_rate"] == 0.6667
        assert body["rates"]["exception_rate"] == 0.3333


def test_value_baseline_does_not_mix_prior_period_mapping_or_return(tmp_path):
    from app.accounting.database import Database

    path = tmp_path / "value-cross-period.db"
    app = create_app(path, seed=True, attachment_root=tmp_path / "originals")
    with local_client(app) as client:
        accountant = auth(client, "accountant")
        book = client.get("/api/workbench", headers=accountant).json()["books"][0]
        import sqlite3
        with sqlite3.connect(path) as raw:
            raw.execute("DROP TRIGGER IF EXISTS rr_draft_insert_guard")
            raw.execute("DROP TRIGGER IF EXISTS rr_draft_insert")
        with Database(path).connect() as conn:
            employee = conn.execute(
                "SELECT id FROM users WHERE name LIKE '%员工%' LIMIT 1"
            ).fetchone()[0]
            firm_id, company_id, book_id, period = conn.execute(
                "SELECT firm_id,company_id,id,period FROM books WHERE id=?", (book["id"],)
            ).fetchone()
            old_created = "2026-09-05T09:00:00+00:00"
            conn.execute(
                "INSERT INTO business_events(id,firm_id,company_id,applicant_id,template_id,"
                "template_version,version,status,payload,submitted_version,created_at,updated_at) "
                "VALUES('evt-old',?,?,?,'reimbursement',1,1,'submitted','{}',1,?,?)",
                (firm_id, company_id, employee, old_created, old_created),
            )
            conn.execute(
                "INSERT INTO business_versions(event_id,firm_id,company_id,event_version,payload,"
                "template_snapshot,attachment_snapshot,created_at,created_by) "
                "VALUES('evt-old',?,?,1,'{}','{}','[]',?,?)",
                (firm_id, company_id, old_created, employee),
            )
            conn.execute(
                "INSERT INTO sources(id,book_id,external_id,source_name,date,description,counterparty,"
                "amount_cents,direction,evidence_ref,fingerprint) "
                "VALUES('src-old',?, 'old-1','demo','2026-10-01','income','customer',10000,'income','ev','fp')",
                (book_id,),
            )
            conn.execute(
                "INSERT INTO drafts(id,book_id,source_id,description,status,version,explanation,"
                "approved_by,approved_at,exported_at) "
                "VALUES('draft-old',?, 'src-old','测试','exported',1,'', ?, ?, ?)",
                (book_id, employee, f"{period}-20T09:00:00+00:00", f"{period}-21T09:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO business_mappings(event_id,firm_id,company_id,book_id,source_id,draft_id,"
                "submitted_version,idempotency_key,request_fingerprint,created_by,created_at) "
                "VALUES('evt-old',?,?,?, 'src-old','draft-old',1,'map-old','fp',?,?)",
                (firm_id, company_id, book_id, employee, f"{period}-11T09:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO business_actions(id,event_id,firm_id,company_id,submitted_version,stage,"
                "decision,reason,created_by,created_at) "
                "VALUES('act-old','evt-old',?,?,1,'return','return','补资料',?,?)",
                (firm_id, company_id, employee, f"{period}-12T09:00:00+00:00"),
            )
            conn.commit()
        response = client.get(
            "/api/firm/observability/value",
            headers=accountant,
            params={"company_id": book["company_id"], "period": period},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"]["submitted_events"] == 0
        assert body["counts"]["mapped_drafts"] == 0
        # 上期事项本期审批也不能进本期分子：分母仍按当期创建对象固定。
        assert body["counts"]["approved_drafts"] == 0
        assert body["rates"]["review_conversion_rate"] is None
        assert body["counts"]["returned_events"] == 0
        assert body["counts"]["return_actions"] == 0
