"""B05 management API smoke test: endpoints and company policy."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_b05_endpoint_and_policy_routes(tmp_path):
    app = create_app(tmp_path / "b05-http.db", seed=True)
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678)
    ) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            company_id = conn.execute("SELECT id FROM companies LIMIT 1").fetchone()[0]
        listed = client.get("/api/operations/ai/endpoints", headers=headers)
        assert listed.status_code == 200 and listed.json()["endpoints"] == []
        created = client.post(
            "/api/operations/ai/endpoints",
            headers=headers,
            json={
                "host": "ai.example.test",
                "port": 443,
                "path_prefix": "/v1",
                "service_identity": "HTTP 合成模型",
                "capabilities": [
                    {
                        "scenario": "ocr",
                        "model": "vision-1",
                        "supports_image": True,
                        "data_location": "境外测试区域",
                        "reason": "B05 HTTP 端点",
                    }
                ],
                "reason": "B05 HTTP 端点",
            },
        )
        assert created.status_code == 201, created.text
        endpoint_id = created.json()["id"]
        policy = client.get(
            f"/api/companies/{company_id}/ai-policy", headers=headers
        )
        assert policy.status_code == 200
        assert policy.json()["managed"] is False
        updated = client.put(
            f"/api/companies/{company_id}/ai-policy",
            headers=headers,
            json={
                "managed": True,
                "local_only": False,
                "allowed_scenarios": ["ocr"],
                "allowed_data_categories": ["document"],
                "allowed_endpoint_ids": [endpoint_id],
                "reason": "B05 HTTP 企业政策",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 1
