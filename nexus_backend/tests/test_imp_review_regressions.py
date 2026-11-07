"""Independent read-only adversarial probes; files only in review artifacts."""

from copy import deepcopy
import pytest
from conftest import database_resources
from test_business import business
from test_imp08_analysis import posted
from test_imp07_eval import corpus, rows_for, CANDIDATE, bound_attempt
from tools.imp07_eval import score, EvaluationError
from tools.imp07_eval.costing import summarize_attempts, CostingError
from scripts.imp07_run_eval import rows_for as execute_rows
from app.assistant import analysis
from app.assistant.observation import observe, ObservationLedger, ObservationError
from app.accounting.domain import AccountingError
from types import SimpleNamespace


def test_all_wrong_fields_cannot_be_accepted():
    data = corpus()
    rows = rows_for(data)
    for row in rows:
        row["fields"] = {key: "WRONG" for key in row["fields"]}
    result = score(data, rows, candidate=CANDIDATE)
    assert result["field_correct_total"] == 0
    assert result["acceptance"] != "accepted_offline", result["acceptance"]


def test_same_version_wrong_candidate_hash_is_rejected():
    data = corpus()
    rows = rows_for(data)
    for row in rows:
        row["candidate_fingerprint"] = "b" * 64
        row["input_sha256"] = "0" * 64
    with pytest.raises(EvaluationError):
        score(data, rows, candidate=CANDIDATE)


def test_negative_attempt_cost_is_rejected():
    with pytest.raises(CostingError):
        summarize_attempts([{"attempt_id": "a", "status": "succeeded", "cost_cny": "-1.00"}])


def test_known_cost_preserved_when_another_attempt_unknown():
    data = corpus()
    rows = rows_for(data, cost="0")
    rows[0]["attempts"] = [
        bound_attempt(rows[0], {"attempt_id": "known", "status": "failed", "cost_cny": "1.00"}),
        bound_attempt(rows[0], {"attempt_id": "unknown", "status": "unknown", "cost_cny": None}),
    ]
    result = score(data, rows, candidate=CANDIDATE)
    assert result["cost_summary"]["total"] is None
    assert result["cost_summary"]["known_subtotal"] == "1.00"


def test_executor_does_not_use_gold_abstention_flag():
    data = corpus()
    first = execute_rows(data, "same")
    changed = deepcopy(data)
    changed["items"][0]["abstention_expected"] = not changed["items"][0]["abstention_expected"]
    second = execute_rows(changed, "same")
    assert first == second


def test_empty_last_page_is_not_complete_inspection(posted):
    case = posted.case
    result = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
        offset=2,
        limit=1,
    )
    assert result["coverage"]["total_rows"] == 2
    assert result["coverage"]["detail_rows"] == 0
    assert result["coverage"]["detail_complete"] is False, result


def test_claim_amount_is_bound_to_specific_fact(posted):
    case = posted.case
    result = analysis.analyze(
        case.db,
        case.manager,
        case.book["id"],
        "2026-10",
        "invoice_voucher_diff",
        target_voucher_id=posted.first["voucher"]["id"],
    )
    with pytest.raises(AccountingError):
        analysis.validate_claim(
            result,
            {
                "company_id": case.company["id"],
                "book_id": case.book["id"],
                "period": "2026-10",
                "amount_cents": 10000,
                "fact_ids": ["invoice_voucher_diff:difference_cents"],
                "text": "差额为100元",
            },
        )


def test_revocation_after_in_transaction_check_is_not_published(posted):
    case = posted.case
    actor = case.actors["accountant"]

    class Revoke:
        calls = 0

        def book(self, conn, current, book_id, mode):
            self.calls += 1
            value = analysis.AccessControl().book(conn, current, book_id, mode)
            if self.calls == 2:
                conn.execute(
                    "DELETE FROM book_access WHERE user_id=? AND book_id=?",
                    (current.user_id, book_id),
                )
            return value

    wrapper = Revoke()
    with pytest.raises(AccountingError):
        analysis.analyze(
            SimpleNamespace(db=case.db, access=wrapper),
            actor,
            case.book["id"],
            "2026-10",
            "invoice_voucher_diff",
            target_voucher_id=posted.first["voucher"]["id"],
        )


def test_observation_missing_authorization_fails_closed():
    event = {
        "event_id": "e",
        "event_version": 1,
        "analysis": {
            "status": "partial",
            "handoff": {
                "source_ref": "foreign",
                "action_target": "/workspace/accounting?book=foreign",
            },
        },
    }
    result = observe(event)
    assert result["would_handoff"] is False, result


def test_observation_external_mutation_does_not_poison_replay():
    event = {
        "event_id": "e",
        "event_version": 1,
        "authorized": True,
        "notification_state": "unknown",
    }
    ledger = ObservationLedger()
    result = ledger.record(event)
    result["reason_code"] = "already_sent"
    assert ledger.record(event)["reason_code"] == "verify_unknown"


def test_enabled_http_missing_kind_parameter_returns_422(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setenv("ACCOUNTING_ASSISTANT_INVESTIGATION", "1")
    app = create_app(tmp_path / "review-http.db", seed=True)
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 45901), raise_server_exceptions=False
    ) as client:
        with app.state.service.db.transaction() as conn:
            book = conn.execute("SELECT id,period FROM books LIMIT 1").fetchone()
        token = client.get("/api/session").json()["token"]
        response = client.post(
            "/api/assistant/analyses",
            headers={"Authorization": "Bearer " + token},
            json={"book_id": book["id"], "period": book["period"], "kind": "expense_bridge"},
        )
        assert response.status_code == 422, response.text
