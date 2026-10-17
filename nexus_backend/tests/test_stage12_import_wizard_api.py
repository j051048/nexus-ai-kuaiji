import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.import_wizard.parser import MAX_BYTES
from test_stage12_import_wizard import CONTENT, MAPPING


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(
            tmp_path / "api.db",
            seed=False,
            attachment_root=tmp_path / "originals",
            queue_worker_enabled=False,
        ),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 1234),
    ) as value:
        yield value


def prepare(client):
    session = client.get("/api/session").json()
    headers = {"Authorization": "Bearer " + session["token"]}
    response = client.post(
        "/api/books", headers=headers, json={"name": "中文导入HTTP合成企业", "period": "2026-10"}
    )
    assert response.status_code == 201
    return headers, response.json()["id"]


def test_http_chinese_upload_mapping_preview_confirm_and_original_download(client):
    headers, book = prepare(client)
    base = f"/api/books/{book}/import-wizard"
    response = client.post(
        base + "/files",
        headers=headers,
        files={"file": ("中文流水.csv", CONTENT.encode("gbk"), "text/csv")},
    )
    assert response.status_code == 201
    info = response.json()
    response = client.post(
        base + "/previews",
        headers=headers,
        json={
            "file_id": info["file_id"],
            "namespace": "bank",
            "bank_account": "工行1234",
            "mapping": MAPPING.model_dump(),
        },
    )
    assert response.status_code == 201
    preview = response.json()
    response = client.post(
        base + f"/previews/{preview['id']}/confirm",
        headers=headers,
        json={"remember_mapping": True},
    )
    assert response.status_code == 200 and response.json()["imported"] == 2
    original = client.get(base + "/files/" + info["file_id"], headers=headers)
    assert original.status_code == 200 and original.content == CONTENT.encode("gbk")
    assert (
        original.headers["cache-control"] == "no-store"
        and "attachment;" in original.headers["content-disposition"]
    )
    assert (
        client.get(base + "/profiles", headers=headers).json()["profiles"][0]["bank_account"]
        == "工行1234"
    )


def test_http_paste_old_english_csv_field_mapping_remains_compatible(client):
    headers, book = prepare(client)
    base = f"/api/books/{book}/import-wizard"
    content = "external_id,date,description,counterparty,amount,direction,evidence_ref\nold1,2026-10-01,办公用品,供应商,0.30,expense,原发票\n"
    info = client.post(base + "/paste", headers=headers, json={"content": content}).json()
    mapping = {
        "columns": info["suggested_columns"],
        "date_format": "ymd",
        "amount_mode": "direction",
        "allow_grouping": False,
    }
    preview = client.post(
        base + "/previews",
        headers=headers,
        json={
            "file_id": info["file_id"],
            "namespace": "legacy-bank",
            "bank_account": "账号1234",
            "mapping": mapping,
        },
    ).json()
    assert preview["rows"][0]["source"]["amount"] == "0.30"
    assert (
        client.post(base + f"/previews/{preview['id']}/confirm", headers=headers, json={}).json()[
            "imported"
        ]
        == 1
    )


def test_http_request_and_file_size_limits_and_bad_mapping_are_enforced(client):
    headers, book = prepare(client)
    base = f"/api/books/{book}/import-wizard"
    response = client.post(
        base + "/files",
        headers=headers,
        files={"file": ("large.csv", b"x" * (MAX_BYTES + 1), "text/csv")},
    )
    assert response.status_code == 413
    response = client.post(
        base + "/paste",
        headers={**headers, "Content-Type": "application/json"},
        content=b"x" * (MAX_BYTES + 128 * 1024 + 1),
    )
    assert response.status_code == 413
    info = client.post(base + "/paste", headers=headers, json={"content": CONTENT}).json()
    response = client.post(
        base + "/previews",
        headers=headers,
        json={
            "file_id": info["file_id"],
            "namespace": "a",
            "bank_account": "b",
            "mapping": {"columns": {"date": 0, "description": 0, "amount": 2, "direction": 3}},
        },
    )
    assert response.status_code == 422


def test_http_accepts_valid_bank_file_above_two_megabytes(client):
    headers, book = prepare(client)
    base = f"/api/books/{book}/import-wizard"
    header = "交易编号,交易日期,摘要,交易对方,金额,方向,凭据引用"
    rows = [
        f"L{index:06d},2026/10/01,第{index}笔{'备注' * 120},虚构供应商,0.30,支出,回单{index}"
        for index in range(3000)
    ]
    data = ("\n".join([header, *rows]) + "\n").encode()
    assert 2 * 1024 * 1024 < len(data) < MAX_BYTES
    response = client.post(
        base + "/files", headers=headers, files={"file": ("大额流水.csv", data, "text/csv")}
    )
    assert response.status_code == 201
    assert response.json()["row_count"] == 3000


def test_http_cross_book_original_and_preview_cannot_be_read_or_confirmed(client):
    headers, book = prepare(client)
    base = f"/api/books/{book}/import-wizard"
    info = client.post(base + "/paste", headers=headers, json={"content": CONTENT}).json()
    preview = client.post(
        base + "/previews",
        headers=headers,
        json={
            "file_id": info["file_id"],
            "namespace": "bank",
            "bank_account": "1234",
            "mapping": MAPPING.model_dump(),
        },
    ).json()
    other = client.post(
        "/api/books", headers=headers, json={"name": "另一隔离企业", "period": "2026-10"}
    ).json()["id"]
    wrong = f"/api/books/{other}/import-wizard"
    assert client.get(wrong + "/files/" + info["file_id"], headers=headers).status_code == 404
    assert (
        client.post(
            wrong + "/previews/" + preview["id"] + "/confirm", headers=headers, json={}
        ).status_code
        == 404
    )
