from copy import deepcopy

import pytest

from scripts.evaluate_ocr_fields import evaluate


def label(key="one"):
    return {
        "sample_id": key,
        "category": "synthetic",
        "fields": {"gross_amount": "106.00", "tax_amount": "6.00", "invoice_number": "0001"},
    }


def prediction(key="one"):
    return {
        "sample_id": key,
        "status": "succeeded",
        "review_required": False,
        "fields": {"gross_amount": "106", "tax_amount": "6", "invoice_number": "0001"},
    }


def test_missing_candidates_and_unknown_costs_are_not_success_or_free():
    report = evaluate([label(), label("two")], [prediction()])
    assert report["sample_exact_rate"] == 0.5
    assert report["fields"]["gross_amount"]["missing"] == 1
    assert report["unknown_cost_samples"] == 2
    assert report["reported_cost_cny"] is None


def test_silent_wrong_amount_and_flagged_wrong_amount_count_differently():
    wrong = prediction()
    wrong["fields"]["gross_amount"] = "1060"
    flagged = deepcopy(wrong) | {"sample_id": "two", "review_required": True}
    report = evaluate([label(), label("two")], [wrong, flagged])
    assert report["error_samples"] == 2
    assert report["unflagged_error_samples"] == 1
    assert report["unflagged_error_rate_among_errors"] == 0.5


def test_null_unknown_tax_is_not_zero_and_missing_is_not_null():
    expected = label()
    expected["fields"] = {"tax_amount": None}
    result = prediction()
    result["fields"] = {"tax_amount": "0"}
    assert evaluate([expected], [result])["all_labeled_fields_exact"] == 0
    result["fields"] = {}
    assert evaluate([expected], [result])["fields"]["tax_amount"]["missing"] == 1
    result["fields"] = {"tax_amount": None}
    assert evaluate([expected], [result])["all_labeled_fields_exact"] == 1


@pytest.mark.parametrize("value", [106, 106.0, "1.06e2", "106.001", "NaN", "-106"])
def test_invalid_predicted_amount_counts_as_error(value):
    result = prediction()
    result["fields"]["gross_amount"] = value
    assert evaluate([label()], [result])["fields"]["gross_amount"]["incorrect"] == 1


def test_fixed_sample_identity_duplicates_and_extras_are_rejected():
    with pytest.raises(ValueError):
        evaluate([label(), label()], [prediction()])
    with pytest.raises(ValueError):
        evaluate([label()], [prediction(), prediction()])
    with pytest.raises(ValueError):
        evaluate([label()], [prediction("unknown")])


def test_leading_zero_invoice_number_must_not_disappear():
    result = prediction()
    result["fields"]["invoice_number"] = "1"
    assert evaluate([label()], [result])["fields"]["invoice_number"]["incorrect"] == 1


def test_failure_and_missing_review_status_cannot_be_silent_success():
    with pytest.raises(ValueError):
        evaluate([label()], [prediction() | {"status": "failed"}])
    result = prediction()
    result.pop("review_required")
    with pytest.raises(ValueError):
        evaluate([label()], [result])
    report = evaluate([label()], [prediction() | {"status": "failed", "review_required": True}])
    assert report["completed"] == 0 and report["unflagged_error_samples"] == 0


def test_duration_price_and_human_time_coverage():
    report = evaluate(
        [label(), label("two")],
        [
            prediction() | {"duration_ms": 10, "correction_seconds": 20, "cost_cny": "0.003"},
            prediction("two") | {"duration_ms": 50, "correction_seconds": 40},
        ],
    )
    assert report["duration_ms_p95"] == 50
    assert report["correction_seconds_median"] == 30
    assert report["reported_cost_cny"] == "0.003"
    assert report["known_cost_samples"] == report["unknown_cost_samples"] == 1


@pytest.mark.parametrize(
    "changes",
    [{"duration_ms": float("nan")}, {"duration_ms": True}, {"cost_cny": "-1"}, {"cost_cny": 0.3}],
)
def test_invalid_metrics_are_rejected(changes):
    with pytest.raises(ValueError):
        evaluate([label()], [prediction() | changes])
