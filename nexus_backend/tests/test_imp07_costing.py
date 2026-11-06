"""IMP07 costing contract: attempt identity, unknown usage and comparison."""

import pytest

from tools.imp07_eval.costing import CostingError, compare_cost, summarize_attempts


def test_same_physical_attempt_is_counted_once_and_conflict_is_rejected():
    attempt = {"attempt_id": "a-1", "status": "succeeded", "cost_cny": "0.01"}
    duplicate = summarize_attempts([attempt, dict(attempt)])
    assert duplicate["physical_attempts"] == 1
    assert duplicate["total"] == "0.01"
    with pytest.raises(CostingError, match="attempt_conflict"):
        summarize_attempts([attempt, {**attempt, "cost_cny": "0.02"}])


def test_failed_call_keeps_known_cost_and_missing_cost_stays_unknown():
    summary = summarize_attempts(
        [
            {"attempt_id": "a-1", "status": "failed", "cost_cny": "0.02"},
            {"attempt_id": "a-2", "status": "unknown", "cost_cny": None},
        ]
    )
    assert summary["failed_calls"] == 2
    assert summary["known_calls"] == 1
    assert summary["unknown_calls"] == 1
    assert summary["known_subtotal"] == "0.02"
    assert summary["total"] is None
    assert summary["complete"] is False


def test_explicit_zero_is_known_and_does_not_become_unknown():
    summary = summarize_attempts([{"attempt_id": "a-1", "status": "succeeded", "cost_cny": "0"}])
    assert summary["known_calls"] == 1
    assert summary["unknown_calls"] == 0
    assert summary["total"] == "0.00"


def test_baseline_zero_is_not_a_fake_saving_and_currency_mismatch_is_not_comparable():
    zero = summarize_attempts([{"attempt_id": "b-1", "status": "succeeded", "cost_cny": "0"}])
    candidate = summarize_attempts([{"attempt_id": "c-1", "status": "succeeded", "cost_cny": "0.01"}])
    assert compare_cost(candidate, zero)["reason"] == "baseline_zero"
    assert compare_cost(candidate, zero)["saving"] is None
    other = summarize_attempts(
        [{"attempt_id": "c-2", "status": "succeeded", "cost_cny": "0.01", "currency": "USD"}],
        currency="USD",
    )
    assert compare_cost(candidate, other)["reason"] == "currency_mismatch"


def test_complete_same_currency_totals_compare_exactly():
    baseline = summarize_attempts(
        [{"attempt_id": "b-1", "status": "succeeded", "cost_cny": "0.10"}]
    )
    candidate = summarize_attempts(
        [{"attempt_id": "c-1", "status": "succeeded", "cost_cny": "0.07"}]
    )
    compared = compare_cost(candidate, baseline)
    assert compared == {
        "comparable": True,
        "saving": "0.03",
        "saving_ratio": "0.300000",
        "reason": None,
    }
