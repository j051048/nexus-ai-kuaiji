"""Minimal B04 HTTP route/permission smoke test."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_b04_routes_are_mounted_and_require_auth(tmp_path):
    app = create_app(tmp_path / "b04-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678)) as client:
        assert client.get("/api/reports").status_code in {401, 403, 422}
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            book_id = conn.execute("SELECT id FROM books LIMIT 1").fetchone()[0]
        reports = client.get("/api/reports", headers=headers, params={"book_id": book_id})
        assert reports.status_code == 200, reports.text
        assert reports.json()["reports"] == []
        subledger = client.get("/api/subledger/items", headers=headers, params={"book_id": book_id})
        assert subledger.status_code == 200, subledger.text
        close = client.get("/api/b04-close", headers=headers, params={"book_id": book_id})
        assert close.status_code == 200, close.text


def test_bank_batches_lists_only_confirmed_batches_for_the_authorized_book(tmp_path):
    """A coverage can only cite a confirmed import batch of the same book."""
    app = create_app(tmp_path / "b04-batches.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            book_id = conn.execute("SELECT id FROM books LIMIT 1").fetchone()[0]
        response = client.get(
            "/api/subledger/bank-batches", headers=headers, params={"book_id": book_id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["batches"] == []
        unauthenticated = client.get("/api/subledger/bank-batches", params={"book_id": book_id})
        assert unauthenticated.status_code in {401, 403}
        foreign = client.get(
            "/api/subledger/bank-batches", headers=headers, params={"book_id": "missing-book"}
        )
        assert foreign.status_code in {403, 404}, foreign.text
