"""The early advisory and message receipt use the real HTTP services."""

from test_business_api import client, auth


def test_preparation_http_uses_employee_scope_and_does_not_require_book_read(client):
    headers = auth(client, "employee")
    context = client.get("/api/business/context", headers=headers).json()
    company = context["companies"][0]["id"]
    response = client.get(
        "/api/business/preparation",
        headers=headers,
        params={"company_id": company, "occurred_on": "2026-10-03"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["advisory"] is True
    for date, expected in [("2035-01-03", 200), ("2026-02-30", 422)]:
        assert (
            client.get(
                "/api/business/preparation",
                headers=headers,
                params={"company_id": company, "occurred_on": date},
            ).status_code
            == expected
        )
    assert (
        client.get(
            "/api/business/preparation",
            headers=headers,
            params={"company_id": "other-company", "occurred_on": "2026-10-03"},
        ).status_code
        == 404
    )


def test_message_receipt_roundtrip_real_http(client):
    headers = auth(client, "employee")
    company = client.get("/api/business/context", headers=headers).json()["companies"][0]["id"]
    with client.app.state.service.db.transaction(write=True) as conn:
        firm = conn.execute(
            "SELECT firm_id FROM firm_clients WHERE company_id=?", (company,)
        ).fetchone()[0]
        creator = conn.execute(
            "SELECT user_id FROM memberships WHERE firm_id=? AND role='owner'", (firm,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO client_messages(id,firm_id,company_id,kind,subject,body,created_by,created_at) "
            "VALUES('http-broadcast',?,?,'system','虚构通知','补充资料',?,'2026-11-03T00:00:00Z')",
            (firm, company, creator),
        )
    before = client.get("/api/client/messages", headers=headers).json()
    assert next(m for m in before["messages"] if m["id"] == "http-broadcast")["read_at"] is None
    for _ in range(2):
        response = client.post("/api/client/messages/http-broadcast/read", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"id": "http-broadcast", "read": True}
    after = client.get("/api/client/messages", headers=headers).json()
    assert next(m for m in after["messages"] if m["id"] == "http-broadcast")["read_at"]
