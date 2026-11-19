"""PLT04-B: HTTP wiring for fee variance registration, handling and reversal."""

import json
import time

from app.auth.supabase import Identity
from app.main import create_app
from test_business import business  # noqa: F401
from test_business_api import local_client
from test_plt02_report_modes import _prepared

TENANT = "test-tenant"


def _configure_reason(case, code="bank_fee"):
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "UPDATE accounting_policies SET fee_variance_reasons_json=? "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?) "
            "AND status='active'",
            (
                json.dumps(
                    [
                        {
                            "code": code,
                            "label": "银行手续费",
                            "blocks_close": True,
                            "debit_account": "1002",
                            "credit_account": "6602.02",
                        }
                    ]
                ),
                case.book["id"],
            ),
        )


def test_fee_variance_routes_are_wired(business):
    case = business
    _prepared(case)
    _configure_reason(case)
    app = create_app(case.db.path, seed=False)
    token = "plt04b-route-token"
    with local_client(app, 45704) as client:
        app.state.tokens[token] = time.monotonic() + 60
        app.state.token_users[token] = Identity(
            case.manager.user_id, "manager@example.test", "PLT04 经理"
        )
        headers = {
            "Authorization": "Bearer " + token,
            "X-Firm-Id": case.manager.firm_id,
            "X-Tenant-Id": TENANT,
        }
        reasons = client.get(
            f"/api/tax/books/{case.book['id']}/fee-variance-reasons",
            headers=headers,
        )
        assert reasons.status_code == 200, reasons.text
        assert reasons.json() == {
            "items": [
                {
                    "code": "bank_fee",
                    "label": "银行手续费",
                    "blocks_close": True,
                    "debit_account": "1002",
                    "credit_account": "6602.02",
                }
            ],
            "default": "unresolved",
        }

        created = client.post(
            f"/api/tax/books/{case.book['id']}/fee-variances",
            headers=headers,
            json={
                "kind": "underpaid",
                "expected_cents": 10_000,
                "actual_cents": 9_000,
                "evidence_ref": "synthetic://variance/http-under-1",
                "idempotency_key": "http-variance-under-1",
            },
        )
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["status"] == "pending"
        assert item["reason_code"] == "unresolved"

        listed = client.get(
            f"/api/tax/books/{case.book['id']}/fee-variances?period=2026-10",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["id"] == item["id"]

        started = client.post(
            f"/api/tax/fee-variances/{item['id']}/start",
            headers=headers,
        )
        assert started.status_code == 200, started.text
        assert started.json()["state"] == "processing"
        assert started.json()["status"] == "pending"

        assignees = client.get(
            f"/api/tax/books/{case.book['id']}/fee-variance-assignees",
            headers=headers,
        )
        assert assignees.status_code == 200, assignees.text
        assert any(
            row["id"] == case.actors["finance"].user_id
            for row in assignees.json()["items"]
        )

        missing_assignee = client.post(
            f"/api/tax/fee-variances/{item['id']}/assign",
            headers=headers,
            json={"assignee_id": "missing-user", "reason": "转交财务核对"},
        )
        assert missing_assignee.status_code == 409, missing_assignee.text
        assigned = client.post(
            f"/api/tax/fee-variances/{item['id']}/assign",
            headers=headers,
            json={
                "assignee_id": case.actors["finance"].user_id,
                "reason": "转交财务核对",
            },
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["processing_started_by"] == case.actors["finance"].user_id

        unresolved = client.post(
            f"/api/tax/fee-variances/{item['id']}/handle",
            headers=headers,
            json={"reason_code": "unresolved"},
        )
        assert unresolved.status_code == 422, unresolved.text

        handled = client.post(
            f"/api/tax/fee-variances/{item['id']}/handle",
            headers=headers,
            json={
                "reason_code": "bank_fee",
                "evidence_ref": "synthetic://policy/bank-fee",
            },
        )
        assert handled.status_code == 200, handled.text
        assert handled.json()["status"] == "handled"
        assert handled.json()["draft_id"]

        with_draft = client.post(
            f"/api/tax/fee-variances/{item['id']}/reverse",
            headers=headers,
            json={"reason": "重复登记，追加撤销"},
        )
        assert with_draft.status_code == 409, with_draft.text

        pending = client.post(
            f"/api/tax/books/{case.book['id']}/fee-variances",
            headers=headers,
            json={
                "kind": "underpaid",
                "expected_cents": 2_000,
                "actual_cents": 1_900,
                "evidence_ref": "synthetic://variance/http-pending-reverse",
                "idempotency_key": "http-variance-pending-reverse",
            },
        )
        assert pending.status_code == 201, pending.text
        pending_item = pending.json()
        assert (
            client.post(
                f"/api/tax/fee-variances/{pending_item['id']}/handle",
                headers=headers,
                json={
                    "reason_code": "unresolved",
                    "evidence_ref": "synthetic://variance/evidence",
                },
            ).status_code
            == 200
        )

        reversed_item = client.post(
            f"/api/tax/fee-variances/{pending_item['id']}/reverse",
            headers=headers,
            json={"reason": "重复登记，追加撤销"},
        )
        assert reversed_item.status_code == 200, reversed_item.text
        assert reversed_item.json()["status"] == "reversed"
