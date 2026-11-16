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
        assert body["cost"]["scope"] == "ai_model_only"
        assert "不代表人工工时" in body["elapsed"]["note"]

        bad = client.get(
            "/api/firm/observability/value",
            headers=accountant,
            params={"company_id": book["company_id"], "period": "2026-13"},
        )
        assert bad.status_code == 422
