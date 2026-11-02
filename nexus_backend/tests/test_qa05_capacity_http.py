"""Pool exhaustion after an earlier commit cannot authorize blind write retries."""

import pytest

from app.accounting.database import DatabaseCapacityError
from app.accounting.models import BookCreate
from app.accounting.seed import LOCAL_ACTOR
from test_business_api import auth, client  # noqa: F401


@pytest.mark.parametrize("reason", ["queue_full", "wait_timeout"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_capacity_response_does_not_claim_whole_mutation_was_unsent(client, method, reason):
    committed = []

    @client.app.api_route("/api/qa05-after-commit", methods=[method])
    def fail():
        if method == "POST":
            book = client.app.state.service.create_book(
                LOCAL_ACTOR, BookCreate(name="合成已提交后繁忙", period="2026-11")
            )
            committed.append(book["id"])
        raise DatabaseCapacityError("private host / password", reason=reason)

    response = client.request(method, "/api/qa05-after-commit", headers=auth(client, "accountant"))
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert "private" not in response.text
    if method == "GET":
        assert response.json()["code"] == "database_busy"
        assert response.headers["retry-after"] == "2"
        assert committed == []
    else:
        assert response.json()["code"] == "result_unknown"
        assert "retry-after" not in response.headers
        assert "核对" in response.json()["detail"]
        assert len(committed) == 1
        assert (
            client.app.state.service.get_book(LOCAL_ACTOR, committed[0])["book"]["id"]
            == committed[0]
        )
