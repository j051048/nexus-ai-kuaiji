"""DCP02: the setup wizard must derive every step from facts, never from intent."""

from fastapi.testclient import TestClient

from app.main import create_app


def _factory(tmp_path, name):
    app = create_app(tmp_path / name, seed=True)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678))
    return app, client


def _context(app):
    with app.state.service.db.transaction() as conn:
        book = conn.execute(
            "SELECT b.id,b.company_id,b.period,b.firm_id,lp.ledger_id FROM books b "
            "JOIN ledger_periods lp ON lp.book_id=b.id ORDER BY b.id LIMIT 1"
        ).fetchone()
    return dict(book)


def test_setup_lists_the_seven_steps_and_starts_incomplete(tmp_path):
    app, client = _factory(tmp_path, "setup-contract.db")
    with client:
        headers = {"Authorization": "Bearer " + client.get("/api/session").json()["token"]}
        book = _context(app)
        response = client.get(
            "/api/firm/onboarding/setup",
            headers=headers,
            params={
                "company_id": book["company_id"],
                "book_id": book["id"],
                "period": book["period"],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [step["id"] for step in body["steps"]] == [
            "template",
            "accounts",
            "control",
            "bank",
            "opening",
            "staffing",
            "first",
        ]
        assert body["ready"] is False
        # The seed staffs both an editor and a reviewer, so only that step is
        # already satisfied; everything else needs a real record.
        assert {step["id"] for step in body["steps"] if step["complete"]} == {"staffing"}
        for step in body["steps"]:
            assert step["action_target"] == {
                "kind": step["action_target"]["kind"],
                "book_id": book["id"],
                "period": book["period"],
            }
            assert step["owner_role"] and step["next_action"]
        assert body["book_id"] == book["id"] and body["period"] == book["period"]


def test_setup_refuses_foreign_books_and_bad_periods(tmp_path):
    app, client = _factory(tmp_path, "setup-guard.db")
    with client:
        headers = {"Authorization": "Bearer " + client.get("/api/session").json()["token"]}
        book = _context(app)
        params = {"company_id": book["company_id"], "book_id": book["id"], "period": "2026-13"}
        assert (
            client.get("/api/firm/onboarding/setup", headers=headers, params=params).status_code
            == 422
        )
        foreign = client.get(
            "/api/firm/onboarding/setup",
            headers=headers,
            params={"company_id": "no-such-company", "book_id": book["id"], "period": "2026-10"},
        )
        assert foreign.status_code in {403, 404}, foreign.text
        missing = client.get(
            "/api/firm/onboarding/setup",
            headers=headers,
            params={
                "company_id": book["company_id"],
                "book_id": "no-such-book",
                "period": "2026-10",
            },
        )
        assert missing.status_code in {403, 404}, missing.text


def test_setup_steps_flip_only_when_the_underlying_record_exists(tmp_path):
    app, client = _factory(tmp_path, "setup-facts.db")
    with client:
        headers = {"Authorization": "Bearer " + client.get("/api/session").json()["token"]}
        book = _context(app)
        params = {
            "company_id": book["company_id"],
            "book_id": book["id"],
            "period": book["period"],
        }
        with app.state.service.db.transaction(write=True) as conn:
            uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO accounting_policies(id,firm_id,company_id,ledger_id,standard_kind,"
                "taxpayer_kind,industry,fiscal_year_start_month,currency,version,status,"
                "effective_from,effective_to,reason,created_by,created_at) VALUES "
                "('pol1',?,?,?,'small_enterprise','small_scale','',1,'CNY',1,'active',"
                "'2026-01-01',NULL,'开通检查',?,'2026-10-01T00:00:00+08:00')",
                (book["firm_id"], book["company_id"], book["ledger_id"], uid),
            )
            conn.execute(
                "INSERT INTO ledger_accounts(id,firm_id,company_id,ledger_id,current_version,"
                "status,created_by,created_at) VALUES "
                "('la1',?,?,?,1,'active',?, '2026-10-01T00:00:00+08:00')",
                (book["firm_id"], book["company_id"], book["ledger_id"], uid),
            )
            conn.execute(
                "INSERT INTO subledger_control_settings(ledger_id,firm_id,company_id,status,"
                "reason,created_at,updated_at) VALUES (?,?,?,'not_applicable','本月无往来科目',"
                "'2026-10-01T00:00:00+08:00','2026-10-01T00:00:00+08:00')",
                (book["ledger_id"], book["firm_id"], book["company_id"]),
            )
        partial = client.get("/api/firm/onboarding/setup", headers=headers, params=params).json()
        done = {step["id"] for step in partial["steps"] if step["complete"]}
        # Legacy ledger-wide settings are not a current-period declaration.
        assert done == {"template", "accounts", "staffing"}

        # A bank mapping is only accepted when the account resolves to a leaf
        # asset auxiliary mapping; that full fixture lives in the DCP01 browser
        # acceptance, so here we assert the step stays incomplete without one.
        assert "bank" not in done
        assert partial["ready"] is False
