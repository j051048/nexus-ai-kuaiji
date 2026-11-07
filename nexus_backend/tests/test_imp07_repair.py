"""Trusted offline runner boundaries, independent of reported candidate quality."""

from copy import deepcopy
import json

import pytest

from test_imp07_eval import CANDIDATE, corpus, rows_for
from tools.imp07_eval import EvaluationError, load_corpus, score
from tools.imp07_eval.costing import CostingError, summarize_attempts


def test_complete_baseline_is_required_for_acceptance():
    data = corpus()
    rows = rows_for(data)
    assert score(data, rows, candidate=CANDIDATE)["acceptance"] != "accepted_offline"
    assert (
        score(data, rows, candidate=CANDIDATE, baseline_rows=rows[:-1])["acceptance"]
        != "accepted_offline"
    )
    assert (
        score(data, rows, candidate=CANDIDATE, baseline_rows=rows)["acceptance"]
        == "accepted_offline"
    )


def test_scope_and_source_bindings_are_mandatory():
    data = corpus()
    for key in ("company_id", "book_id", "period", "source_sha256", "request_fingerprint"):
        rows = rows_for(data)
        rows[0][key] = "wrong"
        with pytest.raises(EvaluationError):
            score(data, rows, candidate=CANDIDATE)


def test_renamed_duplicate_source_cannot_cross_split(tmp_path):
    data = corpus()
    data["sources"][-1]["source_sha256"] = data["sources"][0]["source_sha256"]
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvaluationError):
        load_corpus(path)


def test_same_attempt_cannot_charge_two_samples():
    data = corpus()
    rows = rows_for(data)
    for row in rows[:2]:
        row["attempts"] = [{"attempt_id": "same", "status": "failed", "cost_cny": "0.01"}]
    with pytest.raises((CostingError, EvaluationError)):
        score(data, rows, candidate=CANDIDATE)


def test_cost_precision_and_invalid_boundaries():
    assert (
        summarize_attempts([{"attempt_id": "a", "status": "succeeded", "cost_cny": "0.000001"}])[
            "total"
        ]
        == "0.000001"
    )
    for value in (-1, True, "-0.01", "NaN", "Infinity"):
        with pytest.raises(CostingError):
            summarize_attempts([{"attempt_id": "a", "status": "failed", "cost_cny": value}])


def test_corpus_digest_is_portable(tmp_path):
    first = corpus()
    path = tmp_path / "corpus.json"
    path.write_text(
        json.dumps(
            {
                key: val
                for key, val in first.items()
                if key not in {"source_index", "item_index", "_path", "_frozen_sha256"}
            }
        ),
        encoding="utf-8",
    )
    second = load_corpus(path)
    assert (
        score(first, rows_for(first), candidate=CANDIDATE)["dataset_sha256"]
        == score(second, rows_for(second), candidate=CANDIDATE)["dataset_sha256"]
    )


def test_mutated_loaded_gold_is_rejected_by_judge():
    data = deepcopy(corpus())
    data["items"][0]["gold_fields"]["gross_amount"] = "1.00"
    with pytest.raises(EvaluationError):
        score(data, rows_for(data), candidate=CANDIDATE)
