"""HTTP contract and permission checks for B02 opening preparation."""

from fastapi.testclient import TestClient

from app.main import create_app


def auth(client, persona="accountant"):
    response = client.get("/api/session", params={"persona": persona})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_opening_http_policy_account_package_and_permission(tmp_path):
    app = create_app(tmp_path / "opening-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678)) as client:
        headers = auth(client)
        with app.state.service.db.transaction() as conn:
            ledger_id = conn.execute(
                "SELECT ledger_id FROM ledger_periods ORDER BY book_id LIMIT 1"
            ).fetchone()[0]
        policy = client.post(
            "/api/opening/policies",
            headers=headers,
            json={
                "ledger_id": ledger_id,
                "standard_kind": "small_enterprise",
                "taxpayer_kind": "general",
                "effective_from": "2026-01",
                "effective_to": "2026-12",
                "reason": "HTTP 隔离政策",
            },
        )
        assert policy.status_code == 201, policy.text
        policy_id = policy.json()["id"]
        bank = client.post(
            "/api/opening/accounts",
            headers=headers,
            json={
                "ledger_id": ledger_id,
                "code": "1002",
                "name": "银行存款",
                "effective_from": "2026-01",
                "effective_to": "2026-12",
                "reason": "HTTP 隔离科目",
            },
        )
        assert bank.status_code == 201, bank.text
        payable = client.post(
            "/api/opening/accounts",
            headers=headers,
            json={
                "ledger_id": ledger_id,
                "code": "2202",
                "name": "应付账款",
                "category": "liability",
                "normal_balance": "credit",
                "effective_from": "2026-01",
                "effective_to": "2026-12",
                "reason": "HTTP 隔离科目",
            },
        )
        assert payable.status_code == 201, payable.text
        package = client.post(
            "/api/opening/packages",
            headers=headers,
            json={"ledger_id": ledger_id, "target_period": "2026-10", "policy_id": policy_id},
        )
        assert package.status_code == 201, package.text
        version = client.post(
            f"/api/opening/packages/{package.json()['package']['id']}/versions",
            headers=headers,
            json={
                "idempotency_key": "http-opening-001",
                "reason": "HTTP 期初导入",
                "lines": [
                    {
                        "account_id": bank.json()["id"],
                        "account_version": 1,
                        "year_opening": "0.00",
                        "ytd_debit": "10000.00",
                        "ytd_credit": "0.00",
                        "opening": "10000.00",
                    },
                    {
                        "account_id": payable.json()["id"],
                        "account_version": 1,
                        "year_opening": "0.00",
                        "ytd_debit": "0.00",
                        "ytd_credit": "10000.00",
                        "opening": "-10000.00",
                    },
                ],
            },
        )
        assert version.status_code == 201, version.text
        validated = client.post(
            f"/api/opening/versions/{version.json()['version']['id']}/validate",
            headers=headers,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["version"]["status"] == "validated"

        employee = auth(client, "employee")
        denied = client.get(
            "/api/opening/policies", headers=employee, params={"ledger_id": ledger_id}
        )
        assert denied.status_code in {401, 403, 404}
