"""HTTP failure semantics through the real middleware; synthetic loopback only."""

import sqlite3

import pytest

from test_business_api import auth, client  # noqa: F401


@pytest.mark.parametrize(
    "headers,status", [({}, 401), ({"Origin": "https://untrusted.invalid"}, 403)]
)
def test_early_rejection_has_private_security_headers(client, headers, status):
    response = client.get("/api/workbench", headers=headers)
    assert response.status_code == status
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("x-request-id")


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_database_connection_loss_does_not_promise_write_rollback(client, method):
    @client.app.api_route("/api/qa-db-fault", methods=[method])
    def fail():
        raise sqlite3.OperationalError("private database credentials must never be echoed")

    response = client.request(method, "/api/qa-db-fault", headers=auth(client, "accountant"))
    assert response.status_code == 503
    assert "private database" not in response.text
    assert "整体回滚" not in response.text
    assert response.json()["code"] == (
        "database_unavailable" if method == "GET" else "result_unknown"
    )
    if method == "POST":
        assert "核对" in response.json()["detail"]
        assert "retry-after" not in response.headers


@pytest.mark.parametrize("status", [401, 503])
def test_auth_failure_is_not_reclassified_or_replayed(status):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.auth import AuthSettings, AuthError

    class UnavailableAuth:
        calls = 0

        def authenticate(self, token):
            self.calls += 1
            raise AuthError("登录验证暂不可用" if status == 503 else "登录已失效", status)

    # No lifespan/database is started: authentication must terminate before any
    # database access, even if that database would itself be unavailable.
    app = create_app(
        "postgresql://unused@127.0.0.1:1/unused",
        seed=False,
        auth_settings=AuthSettings(
            "supabase", "https://synthetic.invalid", "sb_publishable_synthetic"
        ),
    )
    app.state.authenticator = UnavailableAuth()
    http = TestClient(app, base_url="http://127.0.0.1")
    try:
        response = http.get("/api/workbench", headers={"Authorization": "Bearer synthetic"})
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert app.state.authenticator.calls == 1
    finally:
        http.close()


def test_request_id_cannot_inject_log_delimiters(client):
    response = client.get("/api/health", headers={"X-Request-Id": "unsafe\tcredential=value"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] != "unsafe\tcredential=value"
    assert len(response.headers["x-request-id"]) == 32


def test_unexpected_failure_is_controlled_and_has_trace_security_headers(client):
    from fastapi.testclient import TestClient

    @client.app.post("/api/qa-unexpected")
    def fail():
        raise ValueError("SECRET provider detail")

    http = TestClient(
        client.app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 5678),
        raise_server_exceptions=False,
    )
    try:
        response = http.post("/api/qa-unexpected", headers=auth(client, "accountant"))
        assert response.status_code == 500
        assert response.headers.get("content-type", "").startswith("application/json")
        assert response.json()["code"] == "result_unknown"
        assert response.headers.get("cache-control") == "no-store"
        assert response.headers.get("x-request-id")
        assert "SECRET" not in response.text
    finally:
        http.close()
