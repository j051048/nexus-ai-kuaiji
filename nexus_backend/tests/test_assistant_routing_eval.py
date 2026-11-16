"""Fixed-denominator routing evaluation; no provider calls."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.assistant_routing_eval import evaluate


def test_routing_eval_uses_fixed_dev_and_holdout_denominators():
    dataset = json.loads((ROOT / "docs" / "assistant_routing_eval_v1.json").read_text(encoding="utf-8"))
    report = evaluate(dataset["cases"])
    assert report["denominator"] == 21
    assert report["by_split"]["dev"]["total"] == 12
    assert report["by_split"]["holdout"]["total"] == 9
    assert report["critical_write_misses"] == []
    assert report["passed"] == 21
    ids = [case["id"] for case in dataset["cases"]]
    assert len(ids) == len(set(ids))


def test_independent_holdout_v3_is_not_tuned():
    dataset = json.loads((ROOT / "docs" / "assistant_routing_holdout_v3.json").read_text(encoding="utf-8"))
    report = evaluate(dataset["cases"])
    assert report["denominator"] == 10
    assert report["by_split"]["holdout"]["total"] == 10
    assert report["critical_write_misses"] == []
    assert report["passed"] == 10
    ids = [case["id"] for case in dataset["cases"]]
    assert len(ids) == len(set(ids))
