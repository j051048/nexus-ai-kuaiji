"""Offline corpus and scoring for IMP07.

The corpus is synthetic and frozen by file hash. This module never calls a model,
never reads customer data and never accepts a candidate-supplied score.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re


class EvaluationError(ValueError):
    pass


SCHEMA = "imp07-eval-v1"
SPLITS = {"dev", "holdout"}


def _digest(value: str) -> bool:
    return bool(re.fullmatch("[0-9a-f]{64}", value or ""))


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_corpus(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvaluationError("corpus_invalid") from error
    if data.get("schema") != SCHEMA or data.get("data_nature") != "synthetic_only":
        raise EvaluationError("corpus_schema")
    sources = data.get("sources")
    items = data.get("items")
    if not isinstance(sources, list) or not isinstance(items, list):
        raise EvaluationError("corpus_invalid")
    if data.get("source_count") != len(sources) or data.get("variant_count") != len(items):
        raise EvaluationError("corpus_count")
    if len(sources) != 30 or len(items) != 120:
        raise EvaluationError("corpus_count")

    source_index = {}
    split_counts = Counter()
    for source in sources:
        sid = source.get("source_id")
        if not isinstance(sid, str) or not sid or sid in source_index:
            raise EvaluationError("source_identity")
        if source.get("split") not in SPLITS:
            raise EvaluationError("source_split")
        if not _digest(source.get("source_sha256")):
            raise EvaluationError("source_sha256")
        source_index[sid] = source
        split_counts[source["split"]] += 1
    if set(split_counts) != SPLITS:
        raise EvaluationError("source_split")

    item_index = {}
    variants = defaultdict(set)
    source_items = Counter()
    for item in items:
        iid = item.get("item_id")
        sid = item.get("source_id")
        if not isinstance(iid, str) or not iid or iid in item_index:
            raise EvaluationError("item_identity")
        if sid not in source_index:
            raise EvaluationError("source_identity")
        source = source_index[sid]
        if item.get("split") != source["split"]:
            raise EvaluationError("source_split")
        if not _digest(item.get("input_sha256")):
            raise EvaluationError("input_sha256")
        text = item.get("ocr_text")
        if not isinstance(text, str) or hashlib.sha256(text.encode("utf-8")).hexdigest() != item["input_sha256"]:
            raise EvaluationError("input_sha256")
        fields = item.get("gold_fields")
        if not isinstance(fields, dict) or not fields or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in fields.items()
        ):
            raise EvaluationError("gold_fields")
        if type(item.get("risk_required")) is not bool or type(item.get("abstention_expected")) is not bool:
            raise EvaluationError("gold_flags")
        variant = item.get("variant")
        if not isinstance(variant, str) or variant in variants[sid]:
            raise EvaluationError("variant_identity")
        variants[sid].add(variant)
        source_items[sid] += 1
        item_index[iid] = item
    if any(count != 4 for count in source_items.values()):
        raise EvaluationError("variant_count")
    return {
        **data,
        "source_count": len(sources),
        "variant_count": len(items),
        "source_index": source_index,
        "item_index": item_index,
        "_path": str(path),
    }


def _row_map(rows, corpus, candidate):
    if not isinstance(candidate, dict) or not isinstance(candidate.get("version"), str):
        raise EvaluationError("candidate_version")
    seen = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EvaluationError("row_invalid")
        iid = row.get("item_id")
        if iid not in corpus["item_index"]:
            raise EvaluationError("unknown_item")
        if iid in seen:
            raise EvaluationError("duplicate")
        if row.get("candidate_version") != candidate["version"]:
            raise EvaluationError("candidate_version")
        seen[iid] = row
    return seen


def _score_row(item, row):
    status = row.get("status")
    if status not in {"succeeded", "abstained", "unknown", "failed"}:
        raise EvaluationError("row_status")
    fields = row.get("fields", {})
    if not isinstance(fields, dict):
        raise EvaluationError("row_fields")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in fields.items()):
        raise EvaluationError("row_fields")
    gold = item["gold_fields"]
    field_correct = {key: status == "succeeded" and fields.get(key) == value for key, value in gold.items()}
    failures = []
    if item["risk_required"] and row.get("review_required") is not True:
        failures.append("review_missing")
    if item["abstention_expected"]:
        if status != "abstained" or fields:
            failures.append("expected_abstention_missing")
    elif status == "abstained":
        failures.append("abstained_on_supported")
    correct = status == "succeeded" and all(field_correct.values()) and not failures
    return {
        "status": status,
        "correct": correct,
        "field_correct": field_correct,
        "safety_failures": failures,
    }


def score(corpus, rows, *, candidate, baseline_rows=None, baseline_candidate=None, selection_ids=()):
    if not isinstance(corpus, dict) or "item_index" not in corpus:
        raise EvaluationError("corpus_not_loaded")
    selection = {str(value) for value in selection_ids}
    holdout_ids = {item["item_id"] for item in corpus["items"] if item["split"] == "holdout"}
    if selection.intersection(holdout_ids):
        raise EvaluationError("holdout_used_for_selection")
    current = _row_map(rows, corpus, candidate)
    baseline = _row_map(baseline_rows, corpus, baseline_candidate or candidate) if baseline_rows is not None else None

    details = []
    missing, unknown, failed = [], [], []
    known_cost = 0
    unknown_cost = 0
    for item in sorted(corpus["items"], key=lambda value: value["item_id"]):
        iid = item["item_id"]
        row = current.get(iid)
        if row is None:
            missing.append(iid)
            details.append(
                {
                    "item_id": iid,
                    "source_id": item["source_id"],
                    "split": item["split"],
                    "capability": item["capability"],
                    "status": "missing",
                    "correct": False,
                    "field_correct": {key: False for key in item["gold_fields"]},
                    "safety_failures": ["missing"],
                    "cost_cny": None,
                    "cost_known": False,
                }
            )
            continue
        result = _score_row(item, row)
        if result["status"] == "unknown":
            unknown.append(iid)
        elif result["status"] == "failed":
            failed.append(iid)
        cost = row.get("cost_cny")
        cost_known = isinstance(cost, str) and bool(re.fullmatch(r"\d+(?:\.\d{1,2})?", cost))
        known_cost += cost_known
        unknown_cost += not cost_known
        details.append(
            {
                "item_id": iid,
                "source_id": item["source_id"],
                "split": item["split"],
                "capability": item["capability"],
                **result,
                "cost_cny": cost,
                "cost_known": cost_known,
            }
        )

    regressions = []
    if baseline is not None:
        for detail in details:
            base_row = baseline.get(detail["item_id"])
            if base_row is None:
                continue
            base = _score_row(corpus["item_index"][detail["item_id"]], base_row)
            for field, ok in base["field_correct"].items():
                if ok and not detail["field_correct"].get(field):
                    regressions.append({"item_id": detail["item_id"], "field": field, "reason": "field_regression"})
            if base["correct"] and not detail["correct"]:
                regressions.append({"item_id": detail["item_id"], "reason": "sample_regression"})

    safety_failures = [detail for detail in details if detail["safety_failures"]]
    success_count = sum(detail["correct"] for detail in details)
    field_total = sum(len(item["gold_fields"]) for item in corpus["items"])
    field_correct_total = sum(sum(detail["field_correct"].values()) for detail in details)
    split_summary = {}
    for split in sorted(SPLITS):
        members = [detail for detail in details if detail["split"] == split]
        split_summary[split] = {
            "denominator": len(members),
            "success": sum(detail["correct"] for detail in members),
        }
    if missing or unknown or failed:
        acceptance = "incomplete"
    elif safety_failures or regressions or unknown_cost:
        acceptance = "rejected"
    else:
        acceptance = "accepted_offline"
    return {
        "schema": SCHEMA,
        "dataset_id": corpus["dataset_id"],
        "dataset_version": corpus["dataset_version"],
        "dataset_sha256": hashlib.sha256(_canonical(corpus).encode("utf-8")).hexdigest(),
        "candidate": candidate,
        "source_count": corpus["source_count"],
        "variant_count": corpus["variant_count"],
        "denominator": len(corpus["items"]),
        "success_count": success_count,
        "missing": missing,
        "missing_count": len(missing),
        "unknown": unknown,
        "unknown_count": len(unknown),
        "failed": failed,
        "failed_count": len(failed),
        "field_correct_total": field_correct_total,
        "field_total": field_total,
        "known_cost_count": known_cost,
        "unknown_cost_count": unknown_cost,
        "safety_failure_count": len(safety_failures),
        "regressions": regressions,
        "acceptance": acceptance,
        "engineering": "passed" if acceptance == "accepted_offline" else acceptance,
        "split_summary": split_summary,
        "real_model_evaluated": False,
        "production_ready": False,
        "details": details,
    }


def render_markdown(report):
    def plain(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# IMP07 离线评测报告（合成）",
        "",
        "真实模型效果未验证；本报告仅运行离线工程与 mock 回放，不声称真实识别率。",
        "",
        f"- 候选：{report['candidate']['version']} / {report['candidate']['fingerprint']}",
        f"- 数据集：{report['dataset_id']} v{report['dataset_version']} / {report['dataset_sha256']}",
        f"- 来源组：{report['source_count']}；样本：{report['variant_count']}（同一来源的变体不重复计为独立原件）",
        f"- 固定分母：{report['denominator']}；成功：{report['success_count']}；缺失：{report['missing_count']}；未知：{report['unknown_count']}；失败：{report['failed_count']}",
        f"- 字段正确：{report['field_correct_total']}/{report['field_total']}；费用未知：{report['unknown_cost_count']}",
        f"- 安全失败：{report['safety_failure_count']}；回归：{len(report['regressions'])}",
        f"- 结论：{report['acceptance']}（离线工程；非真实模型/生产验收）",
        "",
        "| 能力 | 分母 | 成功 |",
        "|---|---|---|",
    ]
    for split, summary in report["split_summary"].items():
        lines.append(f"| {plain(split)} | {summary['denominator']} | {summary['success']} |")
    if report["regressions"]:
        lines += ["", "## 关键回归", ""]
        for row in report["regressions"][:50]:
            lines.append(
                f"- {plain(row['item_id'])}: {plain(row['reason'])}"
                + (f" / {plain(row.get('field'))}" if row.get("field") else "")
            )
    return "\n".join(lines) + "\n"


def save_report(report, directory, *, details=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = report if details is None else {**report, "details": details}
    (directory / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (directory / "report.md").write_text(render_markdown(payload), encoding="utf-8")
