import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from test_business_api import auth


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "alias-http.db", seed=True, attachment_root=tmp_path / "private")

    @app.middleware("http")
    async def loopback(request, call_next):
        request.scope["client"] = ("127.0.0.1", 1234)
        return await call_next(request)

    with TestClient(app, base_url="http://127.0.0.1") as value:
        yield value


def test_alias_http_resolves_unknown_and_rejects_missing_subject(client):
    headers = auth(client, "accountant")
    context = client.get("/api/business/context", headers=headers)
    assert context.status_code == 200, context.text
    company_id = context.json()["companies"][0]["id"]

    resolved = client.get(
        f"/api/companies/{company_id}/aliases/resolve",
        params={"alias": "不存在", "subject_type": "transport_subject", "on_date": "2026-10-31"},
        headers=headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "not_found"

    created = client.post(
        f"/api/companies/{company_id}/aliases",
        headers=headers,
        json={
            "subject_type": "transport_subject",
            "subject_id": "missing-subject",
            "alias": "旧牌-NO",
            "source": "测试",
            "effective_from": "2026-10-01",
        },
    )
    assert created.status_code == 404, created.text


def test_alias_http_lists_manageable_aliases(client):
    headers = auth(client, "accountant")
    context = client.get("/api/business/context", headers=headers)
    assert context.status_code == 200, context.text
    company_id = context.json()["companies"][0]["id"]

    listed = client.get(
        f"/api/companies/{company_id}/aliases",
        params={"subject_type": "counterparty"},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json()["items"], list)
