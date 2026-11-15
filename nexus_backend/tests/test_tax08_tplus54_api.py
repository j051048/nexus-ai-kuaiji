import csv
import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from test_business_api import auth


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "tplus54-api.db", seed=True, attachment_root=tmp_path / "private")

    @app.middleware("http")
    async def loopback(request, call_next):
        # Starlette 0.35's TestClient sends client=None; current CI pins a newer
        # Starlette. Force the same loopback identity the real server sees.
        request.scope["client"] = ("127.0.0.1", 5678)
        return await call_next(request)

    with TestClient(app, base_url="http://127.0.0.1") as value:
        yield value


def prepared(client):
    headers = auth(client, "accountant")
    response = client.post(
        "/api/books",
        headers=headers,
        json={"name": "第五十四列合成企业", "period": "2026-10"},
    )
    assert response.status_code == 201, response.text
    book = response.json()
    reviewer = auth(client, "reviewer")
    reviewer_id = client.get("/api/me", headers=reviewer).json()["user"]["id"]
    granted = client.put(
        f"/api/books/{book['id']}/members/{reviewer_id}",
        headers=headers,
        json={"role": "reviewer"},
    )
    assert granted.status_code == 200, granted.text
    base = f"/api/books/{book['id']}/integrations/tplus"
    config = {
        "expected_version": 0,
        "external_book_id": "synthetic-54",
        "accounts": [
            {"local_code": "1002", "external_code": "100201"},
            {"local_code": "6602.02", "external_code": "660202"},
        ],
        "reason": "合成 54 列协议映射",
        "idempotency_key": uuid4().hex,
    }
    assert client.post(base + "/configuration", headers=headers, json=config).status_code == 200
    return headers, reviewer, book, base


def export_intent(client, maker, reviewer, book, base):
    bid = book["id"]
    imported = client.post(
        f"/api/books/{bid}/imports",
        headers=maker,
        json={
            "source_name": "合成回单",
            "rows": [
                {
                    "external_id": uuid4().hex,
                    "date": "2026-10-03",
                    "description": "办公用品",
                    "counterparty": "合成供应商",
                    "amount": "80.00",
                    "direction": "expense",
                    "evidence_ref": "合成核验资料",
                }
            ],
        },
    )
    assert imported.status_code == 200, imported.text
    assert client.post(f"/api/books/{bid}/suggestions", headers=maker).status_code == 200
    draft = client.get(f"/api/books/{bid}", headers=maker).json()["drafts"][0]
    approval = client.post(
        f"/api/books/{bid}/drafts/{draft['id']}/approve",
        headers=reviewer,
        json={"expected_version": draft["version"]},
    )
    assert approval.status_code == 200, approval.text
    exported = client.post(
        f"/api/books/{bid}/exports",
        headers=reviewer,
        json={"draft_ids": [draft["id"]], "idempotency_key": uuid4().hex},
    )
    assert exported.status_code == 200, exported.text
    intent = client.post(
        base + "/outbox",
        headers=reviewer,
        json={
            "export_id": exported.json()["id"],
            "configuration_version": 1,
            "reason": "复核后合成交付",
            "idempotency_key": uuid4().hex,
        },
    )
    assert intent.status_code == 201, intent.text
    return intent.json()


def receipt_content(content: bytes, status="accepted", reason=""):
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    codes = list(dict.fromkeys(row["外部凭证编号"] for row in rows))
    lines = ["行号,外部凭证编号,结果,原因"]
    lines += [f"{i},{code},{status},{reason}" for i, code in enumerate(codes, 1)]
    return "\n".join(lines) + "\n"


def test_http_artifact_download_and_receipt_are_auditable(client):
    maker, reviewer, book, base = prepared(client)
    intent = export_intent(client, maker, reviewer, book, base)
    created = client.post(base + f"/outbox/{intent['id']}/artifacts", headers=reviewer)
    assert created.status_code == 201, created.text
    artifact = created.json()
    assert artifact["row_count"] >= 1 and artifact["voucher_count"] == 1
    assert artifact["synthetic"] is True and artifact["native_tplus_format"] is False
    assert client.post(base + f"/outbox/{intent['id']}/artifacts", headers=reviewer).json() == artifact
    listed = client.get(base + f"/outbox/{intent['id']}/artifacts", headers=reviewer)
    assert listed.status_code == 200 and listed.json()["artifacts"] == [artifact]

    download = client.get(base + f"/artifacts/{artifact['id']}/download", headers=reviewer)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"].startswith("text/csv")
    assert artifact["filename"] in download.headers["content-disposition"]
    header, first = download.content.decode("utf-8-sig").splitlines()[:2]
    assert len(header.split(",")) == 54 and len(first.split(",")) == 54

    employee = auth(client, "employee")
    assert client.post(base + f"/outbox/{intent['id']}/artifacts", headers=employee).status_code == 404
    assert client.get(base + f"/artifacts/{artifact['id']}/download", headers=employee).status_code == 404

    body = {
        "receipt_code": "R-API-1",
        "content": receipt_content(download.content),
        "reason": "客户文件逐行回传",
    }
    received = client.post(base + f"/artifacts/{artifact['id']}/receipts", headers=reviewer, json=body)
    assert received.status_code == 201, received.text
    value = received.json()
    assert value["result_status"] == "accepted" and value["accepted_count"] == 1
    assert value["external_posting"] is False
    assert client.post(base + f"/artifacts/{artifact['id']}/receipts", headers=reviewer, json=body).json() == value
    assert client.get(base + f"/artifacts/{artifact['id']}/receipts", headers=reviewer).json()["receipts"] == [value]
    assert client.post(base + f"/outbox/{intent['id']}/dispatch", headers=reviewer).status_code == 409
