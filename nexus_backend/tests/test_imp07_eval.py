"""IMP07: independent corpus identity, fixed denominators and regression veto."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.imp07_eval import EvaluationError, load_corpus, render_markdown, score
from tools.ai_inference_lab.rsi.snapshots import Store, SnapshotError


CORPUS = Path(__file__).parent / "fixtures" / "imp07_eval" / "corpus.json"
CANDIDATE = {"version": "imp07-candidate-v1", "fingerprint": "a" * 64}


def corpus():
    return load_corpus(CORPUS)


def rows_for(data, *, status="succeeded", cost="0.01"):
    rows = []
    for item in data["items"]:
        if status == "unknown":
            rows.append({"item_id": item["item_id"], "candidate_version": CANDIDATE["version"], "status": "unknown", "fields": {}, "cost_cny": None})
        elif status == "abstained":
            rows.append({"item_id": item["item_id"], "candidate_version": CANDIDATE["version"], "status": "abstained", "fields": {}, "cost_cny": cost})
        else:
            rows.append(
                {
                    "item_id": item["item_id"],
                    "candidate_version": CANDIDATE["version"],
                    "status": "succeeded",
                    "fields": deepcopy(item["gold_fields"]),
                    "review_required": item["risk_required"],
                    "cost_cny": cost,
                }
            )
    return rows


def test_07_t1_source_group_cannot_cross_splits_or_change_hash(tmp_path):
    data = corpus()
    assert data["source_count"] == 30 and data["variant_count"] == 120
    assert len(data["items"]) == 120

    leaked = deepcopy(data)
    source = leaked["sources"][0]
    other_split = "holdout" if source["split"] == "dev" else "dev"
    for item in leaked["items"]:
        if item["source_id"] == source["source_id"]:
            item["split"] = other_split
    leaked_path = tmp_path / "leaked.json"
    leaked_path.write_text(json.dumps(leaked, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvaluationError, match="source_split"):
        load_corpus(leaked_path)

    changed = deepcopy(data)
    changed["items"][0]["input_sha256"] = "0" * 64
    changed_path = tmp_path / "changed.json"
    changed_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EvaluationError, match="input_sha256"):
        load_corpus(changed_path)


def test_07_t2_duplicate_missing_foreign_and_stale_candidate_fail_closed():
    data = corpus()
    rows = rows_for(data)
    duplicate = rows + [deepcopy(rows[0])]
    with pytest.raises(EvaluationError, match="duplicate"):
        score(data, duplicate, candidate=CANDIDATE)

    missing = score(data, rows[:-1], candidate=CANDIDATE)
    assert missing["denominator"] == 120
    assert missing["missing_count"] == 1
    assert missing["engineering"] == "incomplete"

    foreign = deepcopy(rows)
    foreign[0]["item_id"] = "not-in-corpus"
    with pytest.raises(EvaluationError, match="unknown_item"):
        score(data, foreign, candidate=CANDIDATE)

    with pytest.raises(EvaluationError, match="candidate_version"):
        score(data, rows, candidate={"version": "other", "fingerprint": "b" * 64})


def test_07_t3_amount_fix_cannot_drop_invoice_leading_zero():
    data = corpus()
    baseline = rows_for(data)
    candidate = deepcopy(baseline)
    victim = next(item for item in data["items"] if item["gold_fields"]["invoice_number"].startswith("000"))
    row = next(item for item in candidate if item["item_id"] == victim["item_id"])
    row["fields"]["invoice_number"] = row["fields"]["invoice_number"].lstrip("0")
    result = score(data, candidate, candidate=CANDIDATE, baseline_rows=baseline)
    assert result["acceptance"] == "rejected"
    assert any(reg["item_id"] == victim["item_id"] and reg["reason"] == "field_regression" for reg in result["regressions"])


def test_07_t4_holdout_cannot_participate_in_selection():
    data = corpus()
    holdout = next(item for item in data["items"] if item["split"] == "holdout")
    with pytest.raises(EvaluationError, match="holdout"):
        score(data, rows_for(data), candidate=CANDIDATE, selection_ids={holdout["item_id"]})


def test_07_t5_unknown_and_abstain_do_not_win_and_cost_is_not_free():
    data = corpus()
    unknown = score(data, rows_for(data, status="unknown"), candidate=CANDIDATE)
    assert unknown["denominator"] == 120
    assert unknown["success_count"] == 0
    assert unknown["unknown_cost_count"] == 120
    assert unknown["acceptance"] == "incomplete"

    abstained = score(data, rows_for(data, status="abstained"), candidate=CANDIDATE)
    assert abstained["success_count"] == 0
    assert abstained["acceptance"] in {"rejected", "incomplete"}


def test_07_t6_report_details_and_markdown_stay_consistent():
    data = corpus()
    result = score(data, rows_for(data), candidate=CANDIDATE)
    assert len(result["details"]) == 120
    assert result["success_count"] == sum(row["correct"] for row in result["details"])
    assert result["field_correct_total"] == sum(sum(row["field_correct"].values()) for row in result["details"])
    text = render_markdown(result)
    assert "真实模型效果未验证" in text
    assert "合成" in text
    assert "120" in text


def test_07_t7_snapshot_lineage_and_allowlist_still_reject_bad_changes(tmp_path):
    store = Store(tmp_path / "snaps")
    base = store.snapshot({"prompt.txt": "只提取有来源的字段", "mock.json": '{"strategy":"baseline"}'}, reason="base")
    child = store.snapshot(
        {"prompt.txt": "只提取有来源的字段", "mock.json": '{"strategy":"gross-label-safe"}'},
        reason="child",
        parent=base["id"],
    )
    assert child["root_id"] == base["id"]
    assert store.diff(base["id"], child["id"]) == ["mock.json"]
    with pytest.raises(SnapshotError, match="resource_allowlist"):
        store.snapshot({"prompt.txt": "x"}, reason="bad")
