from copy import deepcopy
import json

import pytest

from scripts import imp07_run_eval as runner
from tools.imp07_eval import load_corpus, score, EvaluationError
from tools.imp07_eval.costing import CostingError


def test_policy_is_part_of_actual_execution_identity():
    corpus = load_corpus(runner.CORPUS)
    safe = runner.rows_for(corpus, "same", policy="safe")
    bad = runner.rows_for(corpus, "same", policy="strip_zeros")
    assert safe[0]["candidate_fingerprint"] != bad[0]["candidate_fingerprint"]


def test_selection_freezes_dev_winner_before_holdout(tmp_path, monkeypatch):
    corpus = load_corpus(runner.CORPUS)
    holdout = {row["item_id"] for row in corpus["items"] if row["split"] == "holdout"}
    original = runner.execute
    seen = []

    def execute(request, **kwargs):
        assert not {"gold_fields", "risk_required", "abstention_expected", "split"} & request.keys()
        if request["item_id"] in holdout:
            frozen = json.loads((tmp_path / "run/frozen.json").read_text())
            assert frozen["selected"]["policy"] == "safe"
        seen.append((request["item_id"], kwargs["policy"]))
        return original(request, **kwargs)

    monkeypatch.setattr(runner, "execute", execute)
    report = runner.run_comparison(corpus, ["strip_zeros", "safe"], tmp_path / "run")
    assert report["acceptance"] == "accepted_offline"
    assert {policy for item, policy in seen if item in holdout} == {"safe"}
    assert report["denominator"] == 120
    with pytest.raises((FileExistsError, EvaluationError)):
        runner.run_comparison(corpus, ["safe"], tmp_path / "run")


def test_cost_attempt_requires_pricing_and_request_binding():
    corpus = load_corpus(runner.CORPUS)
    rows = runner.rows_for(corpus, "test")
    candidate = {
        key.removeprefix("candidate_"): rows[0][key]
        for key in ("candidate_version", "candidate_fingerprint")
    }
    rows[0].pop("model_calls")
    rows[0]["attempts"] = [{"attempt_id": "foreign", "status": "failed", "cost_cny": "0.01"}]
    with pytest.raises((CostingError, EvaluationError)):
        score(corpus, rows, candidate=candidate)


def test_deterministic_executor_does_not_follow_gold():
    corpus = load_corpus(runner.CORPUS)
    original = runner.rows_for(corpus, "test")
    changed = deepcopy(corpus)
    changed["items"][0]["abstention_expected"] = not changed["items"][0]["abstention_expected"]
    assert runner.rows_for(changed, "test") == original


def test_cli_never_executes_holdout_before_candidate_freeze(tmp_path, monkeypatch):
    import sys

    corpus = load_corpus(runner.CORPUS)
    holdout = {row["item_id"] for row in corpus["items"] if row["split"] == "holdout"}
    output = tmp_path / "cli"
    original = runner.execute
    seen = []

    def execute(request, **kwargs):
        if request["item_id"] in holdout:
            assert (output / "selection/frozen.json").is_file()
            assert kwargs.get("policy", "safe") == "safe"
            seen.append(request["item_id"])
        return original(request, **kwargs)

    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(sys, "argv", ["imp07_run_eval.py", "--out", str(output)])
    assert runner.main() == 0
    assert set(seen) == holdout
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["demonstration_split"] == "dev"
    assert summary["reports"]["good"]["denominator"] == 80
