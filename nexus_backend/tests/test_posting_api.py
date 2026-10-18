"""B03 HTTP contract for binding, manual posting, lookup and trial balance."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.accounting.domain import Actor
from app.accounting.models import FirmMemberGrant
from app.accounting.seed import LOCAL_ACTOR
from app.main import create_app
from app.opening.models import (
    AccountCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)


def test_http_bind_manual_reverse_and_balance(tmp_path):
    app = create_app(tmp_path / "posting-http.db", seed=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 45678)) as client:
        token = client.get("/api/session").json()["token"]
        headers = {"Authorization": "Bearer " + token}
        with app.state.service.db.transaction() as conn:
            book = conn.execute("SELECT id,period FROM books LIMIT 1").fetchone()
            ledger_id = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
            ).fetchone()["ledger_id"]
        opening = app.state.opening
        policy = opening.create_policy(
            LOCAL_ACTOR,
            PolicyCreate(
                ledger_id=ledger_id,
                standard_kind="small_enterprise",
                taxpayer_kind="general",
                effective_from="2026-01",
                effective_to="2026-12",
                reason="B03 HTTP 政策",
            ),
        )

        def account(code, name, category="asset", normal="debit"):
            return opening.create_account(
                LOCAL_ACTOR,
                AccountCreate(
                    ledger_id=ledger_id,
                    code=code,
                    name=name,
                    category=category,
                    normal_balance=normal,
                    effective_from="2026-01",
                    effective_to="2026-12",
                    reason="B03 HTTP 科目",
                ),
            )

        bank = account("1002", "银行存款")
        payable = account("2202", "应付账款", "liability", "credit")
        package = opening.create_package(
            LOCAL_ACTOR,
            OpeningPackageCreate(
                ledger_id=ledger_id, target_period=book["period"], policy_id=policy["id"]
            ),
        )
        version = opening.import_version(
            LOCAL_ACTOR,
            package["package"]["id"],
            OpeningImport(
                idempotency_key="b03-http-opening",
                reason="B03 HTTP 期初",
                lines=[
                    OpeningLineInput(
                        account_id=bank["id"],
                        account_version=1,
                        year_opening="0.00",
                        ytd_debit="10000.00",
                        ytd_credit="0.00",
                        opening="10000.00",
                    ),
                    OpeningLineInput(
                        account_id=payable["id"],
                        account_version=1,
                        year_opening="0.00",
                        ytd_debit="0.00",
                        ytd_credit="10000.00",
                        opening="-10000.00",
                    ),
                ],
            ),
        )
        opening.validate_version(LOCAL_ACTOR, version["version"]["id"])
        opening.submit_version(
            LOCAL_ACTOR,
            version["version"]["id"],
            OpeningCommand(expected_version=1, reason="提交 HTTP 期初"),
        )
        identity = SimpleNamespace(
            user_id="b03-http-admin", display_name="HTTP 复核", email="b03-http-admin@example.test"
        )
        app.state.directory.register_identity(identity)
        app.state.directory.grant_firm_member(
            LOCAL_ACTOR, FirmMemberGrant(email=identity.email, role="admin")
        )
        admin = Actor(identity.user_id, LOCAL_ACTOR.firm_id)
        opening.approve_version(
            admin,
            version["version"]["id"],
            OpeningCommand(expected_version=1, reason="独立核准 HTTP 期初"),
        )
        opening.freeze_version(
            admin,
            version["version"]["id"],
            OpeningCommand(expected_version=1, reason="冻结 HTTP 期初"),
        )
        bound = client.post(
            "/api/posting/opening-bindings",
            headers=headers,
            json={
                "book_id": book["id"],
                "package_version_id": version["version"]["id"],
                "reason": "HTTP 绑定期初",
            },
        )
        assert bound.status_code == 201, bound.text
        manual = client.post(
            "/api/posting/manual",
            headers=headers,
            json={
                "command_key": "http-manual-001",
                "book_id": book["id"],
                "voucher_date": "2026-10-15",
                "description": "HTTP 手工凭证",
                "lines": [
                    {
                        "account_id": bank["id"],
                        "account_version": 1,
                        "debit": "100.00",
                        "credit": "0.00",
                    },
                    {
                        "account_id": payable["id"],
                        "account_version": 1,
                        "debit": "0.00",
                        "credit": "100.00",
                    },
                ],
            },
        )
        assert manual.status_code == 201, manual.text
        voucher_id = manual.json()["voucher"]["id"]
        assert manual.json()["voucher"]["voucher_no"] == 1
        replay = client.get(
            f"/api/books/{book['id']}/posting-commands/http-manual-001", headers=headers
        )
        assert replay.status_code == 200
        assert replay.json()["voucher"]["id"] == voucher_id
        balance = client.get(
            f"/api/books/{book['id']}/trial-balance", headers=headers
        )
        assert balance.status_code == 200, balance.text
        rows = {row["code"]: row for row in balance.json()["rows"]}
        assert rows["1002"]["closing_cents"] == 1010000
        assert rows["2202"]["closing_cents"] == -1010000
        reversed_voucher = client.post(
            f"/api/posting/vouchers/{voucher_id}/reverse",
            headers=headers,
            json={
                "command_key": "http-reverse-001",
                "voucher_date": "2026-10-16",
                "reason": "HTTP 冲销",
            },
        )
        assert reversed_voucher.status_code == 201, reversed_voucher.text
        assert reversed_voucher.json()["voucher"]["voucher_no"] == 2
        assert reversed_voucher.json()["relations"][0]["kind"] == "reversal"
