"""B05 HTTP contract for the tax preparation workbench."""

import json

from fastapi.testclient import TestClient

from app.main import create_app


def _book(app):
    with app.state.service.db.transaction() as conn:
        return conn.execute("SELECT id,company_id,firm_id,period FROM books LIMIT 1").fetchone()


def _user(app):
    with app.state.service.db.transaction() as conn:
        return conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]


def _invoice_batch(app, book, day="05"):
    rows = [
        {
            "date": f"{book['period']}-{day}",
            "direction": "output",
            "amount_cents": 100_000,
            "tax_cents": 3_000,
            "total_cents": 103_000,
            "counterparty": "客户甲",
        }
    ]
    with app.state.service.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO invoice_import_batches(id,book_id,firm_id,company_id,filename,storage_key,"
            "sha256,size_bytes,row_count,direction,rows_json,notes_json,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "batch-http",
                book["id"],
                book["firm_id"],
                book["company_id"],
                "2026-10.xlsx",
                "invoice/http.xlsx",
                "a" * 64,
                1024,
                len(rows),
                "mixed",
                json.dumps(rows, ensure_ascii=False),
                "[]",
                _user(app),
                "2026-10-05T00:00:00+00:00",
            ),
        )


def test_http_profile_workpaper_review_and_csv(tmp_path):
    app = create_app(tmp_path / "tax-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45681)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        book = _book(app)

        without_profile = client.post(
            "/api/tax/workpapers",
            headers=headers,
            json={"book_id": book["id"], "period": book["period"]},
        )
        assert without_profile.status_code == 409
        assert "税务档案" in without_profile.json()["detail"]

        profile = client.put(
            "/api/tax/profile",
            headers=headers,
            json={
                "book_id": book["id"],
                "taxpayer_type": "small_scale",
                "vat_rate_bp": 300,
                "surtax_rate_bp": 1200,
                "filing_cycle": "monthly",
                "effective_from": "2026-01",
                "reason": "小规模征收率3%",
            },
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["taxpayer_type"] == "small_scale"
        assert (
            client.get("/api/tax/profile", headers=headers, params={"book_id": book["id"]}).json()[
                "profile"
            ]["vat_rate_bp"]
            == 300
        )

        without_invoices = client.post(
            "/api/tax/workpapers",
            headers=headers,
            json={"book_id": book["id"], "period": book["period"]},
        )
        assert without_invoices.status_code == 409, "无发票数据不得凭推测生成底稿"
        assert "发票" in without_invoices.json()["detail"]

        _invoice_batch(app, book)
        created = client.post(
            "/api/tax/workpapers",
            headers=headers,
            json={"book_id": book["id"], "period": book["period"], "reason": "生成底稿"},
        )
        assert created.status_code == 201, created.text
        workpaper = created.json()
        assert workpaper["status"] == "draft"
        assert workpaper["tax"] == "30.00"

        listed = client.get(
            "/api/tax/workpapers",
            headers=headers,
            params={"book_id": book["id"], "period": book["period"]},
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["workpapers"]] == [workpaper["id"]]

        draft_csv = client.get(
            f"/api/tax/workpapers/{workpaper['id']}/csv",
            headers=headers,
            params={
                "book_id": book["id"],
                "expected_version": workpaper["version"],
                "source_digest": workpaper["source_digest"],
            },
        )
        assert draft_csv.status_code == 409, "未复核底稿不得导出"

        blocked = client.post(
            f"/api/tax/workpapers/{workpaper['id']}/review",
            headers=headers,
            json={"book_id": book["id"], "reason": "本人复核", "expected_version": 1},
        )
        assert blocked.status_code == 403, "制单人不得复核自己的底稿"
        assert "本人" in blocked.json()["detail"] or "复核" in blocked.json()["detail"]


def test_http_readiness_blocks_before_estimating(tmp_path):
    app = create_app(tmp_path / "tax-ready.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45691)) as client:
        book = _book(app)
        anonymous = client.get(f"/api/tax/readiness?book_id={book['id']}")
        assert anonymous.status_code in (401, 403)
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        ready = client.get(
            f"/api/tax/readiness?book_id={book['id']}&period={book['period']}", headers=headers
        )
        assert ready.status_code == 200
        body = ready.json()
        assert body["supported"] is False
        assert "纳税人档案" in body["blocked_reason"]
        assert body["external_result"] == "none"
        assert body["kinds"][0]["readiness"] == "blocked"
