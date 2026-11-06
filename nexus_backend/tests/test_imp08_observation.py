import pytest

from app.assistant.observation import ObservationConflict, ObservationLedger, observe


def event(**overrides):
    base = {
        "event_id": "evt-1",
        "event_version": 3,
        "authorized": True,
        "budget_exhausted": False,
        "analysis": {"status": "complete", "handoff": None},
    }
    base.update(overrides)
    return base


def test_complete_analysis_has_no_action():
    result = observe(event())
    assert result["reason_code"] == "no_change"
    assert result["would_handoff"] is False
    assert result["would_notify"] is False


def test_partial_analysis_projects_existing_handoff_without_notifying():
    result = observe(event(analysis={"status": "partial", "handoff": {"source_ref": "expense_bridge:b:p:6602.02", "action_target": "/workspace/accounting?book=b"}}))
    assert result["reason_code"] == "handoff"
    assert result["would_handoff"] is True
    assert result["would_notify"] is False
    assert result["source_ref"] == "expense_bridge:b:p:6602.02"


def test_revoked_event_never_projects_a_handoff():
    result = observe(event(revoked=True, analysis={"status": "partial", "handoff": {"source_ref": "x"}}))
    assert result["reason_code"] == "authorization_revoked"
    assert result["would_handoff"] is False


def test_unknown_notification_only_opens_existing_verification():
    result = observe(event(notification_state="unknown"))
    assert result["reason_code"] == "verify_unknown"
    assert result["action_target"] == "existing-verification"
    assert result["would_notify"] is False


def test_budget_exhaustion_does_not_turn_into_action():
    result = observe(event(budget_exhausted=True))
    assert result["reason_code"] == "budget_exhausted"
    assert result["would_handoff"] is False


def test_ledger_is_idempotent_and_rejects_same_id_different_content():
    ledger = ObservationLedger()
    first = ledger.record(event())
    assert ledger.record(event()) == first
    with pytest.raises(ObservationConflict):
        ledger.record(event(event_version=4))
