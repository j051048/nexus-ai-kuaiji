"""付款台 HTTP 契约：入口存在、字段校验、三权分立经 HTTP 也是 403。"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.accounting.domain import AccountingError

from app.treasury.router import build_treasury_router
from app.treasury.service import PaymentService
from review_policy_fixtures import ensure_independent_reviewer
from test_business import business  # noqa: F401


def _client(business, actor):
    app = FastAPI()
    app.state.payments = PaymentService(business.db)
    app.include_router(build_treasury_router(lambda: actor))

    # 生产应用的中间件把 AccountingError 映射成 HTTP；最小测试应用需要同一条映射。
    @app.exception_handler(AccountingError)
    def _accounting_error(_request, error: AccountingError):
        return JSONResponse(status_code=error.status, content={"detail": error.detail})

    return TestClient(app)


def _body(business, *, key="http-1", amount=120_000):
    return {
        "book_id": business.book["id"],
        "period": "2026-10",
        "payee": "供应商甲",
        "payee_account": "6222 0000 0000 0001",
        "amount_cents": amount,
        "idempotency_key": key,
    }


def test_http_create_list_and_approve_separation(business):
    actor = business.actors["accountant"]
    client = _client(business, actor)
    created = client.post("/api/treasury/payment-orders", json=_body(business))
    assert created.status_code == 200, created.text
    order = created.json()
    assert order["status"] == "draft"

    listed = client.get("/api/treasury/payment-orders", params={"book_id": business.book["id"]})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [order["id"]]

    assert client.post(f"/api/treasury/payment-orders/{order['id']}/submit").status_code == 200
    denied = client.post(f"/api/treasury/payment-orders/{order['id']}/approve", json={})
    assert denied.status_code == 403
    assert "制单人不能审批自己发起的付款" in denied.json()["detail"]


def test_http_approval_by_second_person_and_illegal_transition(business):
    actor = business.actors["accountant"]
    approver = ensure_independent_reviewer(business.db, actor, business.book["id"])
    client = _client(business, actor)
    order = client.post("/api/treasury/payment-orders", json=_body(business, key="http-2")).json()
    client.post(f"/api/treasury/payment-orders/{order['id']}/submit")

    other = _client(business, approver)
    approved = other.post(f"/api/treasury/payment-orders/{order['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    illegal = client.post(
        f"/api/treasury/payment-orders/{order['id']}/advance",
        json={"target": "succeeded"},
    )
    assert illegal.status_code == 409


def test_http_rejects_unknown_fields(business):
    client = _client(business, business.actors["accountant"])
    body = _body(business, key="http-3")
    body["approver_user_id"] = "someone"
    assert client.post("/api/treasury/payment-orders", json=body).status_code == 422


CSV_LINE = """account,serial,date,amount,direction,counterparty
6222,20261001-01,2026-10-01,1200.50,income,客户甲
"""


def test_import_preview_normalizes_csv_and_matches(business):
    client = _client(business, business.actors["accountant"])
    response = client.post(
        "/api/treasury/bank-lines/import-preview",
        json={
            "format": "csv",
            "csv_text": CSV_LINE,
            "statement_lines": [
                {
                    "account": "6222",
                    "serial": "20261001-01",
                    "date": "2026-10-01",
                    "amount_cents": 120050,
                    "counterparty": "客户甲",
                    "line_id": "line-1",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines"][0]["amount_cents"] == 120050
    # CSV 只是「流水」一侧，匹配需要回单；这里只验归一化结果。
    assert body["matched"] == []
    assert body["posted"] is False


def test_import_preview_matches_receipts_against_statement_lines(business):
    client = _client(business, business.actors["accountant"])
    response = client.post(
        "/api/treasury/bank-lines/import-preview",
        json={
            "format": "receipts",
            "receipts": [
                {
                    "account": "6222",
                    "serial": "20261001-01",
                    "date": "2026-10-01",
                    "amount_cents": 120050,
                    "direction": "income",
                    "counterparty": "客户甲",
                }
            ],
            "statement_lines": [
                {
                    "account": "6222",
                    "serial": "20261001-01",
                    "date": "2026-10-01",
                    "amount_cents": 120050,
                    "counterparty": "客户甲",
                    "line_id": "line-1",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched"] == [["etax_receipt:20261001-01", "line-1"]]


def test_import_preview_rejects_bad_csv_with_400(business):
    client = _client(business, business.actors["accountant"])
    response = client.post(
        "/api/treasury/bank-lines/import-preview",
        json={"format": "csv", "csv_text": "account,serial\n1,2\n"},
    )
    assert response.status_code == 400
    assert "缺少列" in response.json()["detail"]


def test_import_preview_requires_payload(business):
    client = _client(business, business.actors["accountant"])
    response = client.post("/api/treasury/bank-lines/import-preview", json={"format": "receipts"})
    assert response.status_code == 400
