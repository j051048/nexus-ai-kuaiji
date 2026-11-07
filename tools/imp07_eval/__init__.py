"""Offline corpus and scoring for IMP07.

The corpus is synthetic and frozen by file hash. This module never calls a model,
never reads customer data and never accepts a candidate-supplied score.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .costing import CostingError, summarize_attempts, money
from tools.ai_inference_lab.examples import score_sample
from tools.ocr_replay.replay import expected_request_fingerprint, Policy


class EvaluationError(ValueError):
    pass


SCHEMA = "imp07-eval-v1"
SPLITS = {"dev", "holdout"}


def _digest(value: str) -> bool:
    return bool(re.fullmatch("[0-9a-f]{64}", value or ""))


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


SCOPE = ("firm_id", "company_id", "ledger_id", "book_id", "period")


def corpus_digest(corpus):
    payload = {
        key: value
        for key, value in corpus.items()
        if key not in {"source_index", "item_index", "_path", "_frozen_sha256"}
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def execution_input(corpus, item):
    """Gold-free input envelope. Only this value crosses the executor boundary."""
    return {
        **{key: item[key] for key in SCOPE},
        "item_id": item["item_id"],
        "source_sha256": corpus["source_index"][item["source_id"]]["source_sha256"],
        "input_sha256": item["input_sha256"],
        "ocr_text": item["ocr_text"],
    }


def result_identity(corpus, item, candidate):
    request = execution_input(corpus, item)
    policy = Policy(
        name="imp07",
        version=candidate["version"],
        prompt=candidate["fingerprint"],
        gross_pattern="",
    )
    return {key: value for key, value in request.items() if key != "ocr_text"} | {
        "candidate_version": candidate["version"],
        "candidate_fingerprint": candidate["fingerprint"],
        "request_fingerprint": expected_request_fingerprint(
            {"sample_id": item["item_id"], "ocr_sha256": item["input_sha256"]}, policy
        ),
        "schema_version": "imp07-result-v2",
    }


def evidence_for(text, fields):
    # Synthetic corpus is OCR text, not a claim of actual image recognition.
    return [{"field": key, "page": 1, "quote": text} for key in fields]


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
    source_hashes = set()
    for source in sources:
        sid = source.get("source_id")
        if not isinstance(sid, str) or not sid or sid in source_index:
            raise EvaluationError("source_identity")
        if source.get("split") not in SPLITS:
            raise EvaluationError("source_split")
        if not _digest(source.get("source_sha256")):
            raise EvaluationError("source_sha256")
        if source["source_sha256"] in source_hashes:
            raise EvaluationError("duplicate_source_hash")
        source_hashes.add(source["source_sha256"])
        if any(not isinstance(source.get(key), str) or not source[key] for key in SCOPE):
            raise EvaluationError("source_scope")
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
        if any(item.get(key) != source[key] for key in SCOPE):
            raise EvaluationError("item_scope")
        if item.get("split") != source["split"]:
            raise EvaluationError("source_split")
        if not _digest(item.get("input_sha256")):
            raise EvaluationError("input_sha256")
        text = item.get("ocr_text")
        if (
            not isinstance(text, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != item["input_sha256"]
        ):
            raise EvaluationError("input_sha256")
        fields = item.get("gold_fields")
        if (
            not isinstance(fields, dict)
            or not fields
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in fields.items()
            )
        ):
            raise EvaluationError("gold_fields")
        if (
            type(item.get("risk_required")) is not bool
            or type(item.get("abstention_expected")) is not bool
        ):
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
        "_frozen_sha256": corpus_digest(data),
    }


def _row_map(rows, corpus, candidate):
    if not isinstance(candidate, dict) or not isinstance(candidate.get("version"), str):
        raise EvaluationError("candidate_version")
    if not _digest(candidate.get("fingerprint")):
        raise EvaluationError("candidate_fingerprint")
    seen = {}
    attempts = {}
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
        expected = result_identity(corpus, corpus["item_index"][iid], candidate)
        if any(row.get(key) != value for key, value in expected.items()):
            raise EvaluationError("result_binding")
        for attempt in row.get("attempts", []):
            if not isinstance(attempt, dict) or any(
                attempt.get(key) != value for key, value in expected.items()
            ):
                raise EvaluationError("attempt_binding")
            if (
                attempt.get("currency") != "CNY"
                or not isinstance(attempt.get("pricing_version"), str)
                or not attempt["pricing_version"].strip()
            ):
                raise EvaluationError("attempt_pricing")
            if attempt.get("measurement_nature") not in {"mock", "archived"}:
                raise EvaluationError("attempt_measurement")
            aid = attempt.get("attempt_id")
            if aid in attempts and attempts[aid] != iid:
                raise EvaluationError("attempt_owner_conflict")
            attempts[aid] = iid
        seen[iid] = row
    return seen


def _score_row(item, row):
    status = row.get("status")
    if status not in {"succeeded", "abstained", "unknown", "failed"}:
        raise EvaluationError("row_status")
    fields = row.get("fields", {})
    if not isinstance(fields, dict) or any(
        not isinstance(k, str) or (v is not None and not isinstance(v, str))
        for k, v in fields.items()
    ):
        raise EvaluationError("row_fields")
    sample = {
        "gold_fields": item["gold_fields"],
        "ocr_pages": [{"page": 1, "raw_text": item["ocr_text"]}],
    }
    normalized = {**row, "fields": fields, "review_required": row.get("review_required") is True}
    if status == "abstained":
        normalized.update(
            status="succeeded", decision={"kind": "abstain", "reason": "unsupported_case"}
        )
    result = score_sample(sample, item, normalized)
    return {
        **result,
        "status": status,
        "safety_failures": result["reasons"] if result["safety_failed"] else [],
    }


def score(
    corpus, rows, *, candidate, baseline_rows=None, baseline_candidate=None, selection_ids=()
):
    if not isinstance(corpus, dict) or "item_index" not in corpus:
        raise EvaluationError("corpus_not_loaded")
    if corpus_digest(corpus) != corpus.get("_frozen_sha256"):
        raise EvaluationError("corpus_changed")
    selection = {str(value) for value in selection_ids}
    holdout_ids = {item["item_id"] for item in corpus["items"] if item["split"] == "holdout"}
    if selection.intersection(holdout_ids):
        raise EvaluationError("holdout_used_for_selection")
    current = _row_map(rows, corpus, candidate)
    baseline = (
        _row_map(baseline_rows, corpus, baseline_candidate or candidate)
        if baseline_rows is not None
        else None
    )

    details = []
    missing, unknown, failed = [], [], []
    known_cost = 0
    unknown_cost = 0
    cost_total = Decimal("0")
    has_known_cost = False
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
        attempt_summary = None
        if "attempts" in row:
            attempts = row.get("attempts")
            attempt_summary = (
                summarize_attempts(attempts) if isinstance(attempts, list) and attempts else None
            )
            cost = attempt_summary.get("total") if attempt_summary else None
            cost_known = bool(attempt_summary and attempt_summary["complete"] and cost is not None)
        else:
            # Only the runner's deterministic, zero-call path has a zero without
            # physical attempts. A candidate-provided cost is not settlement.
            cost_known = (
                type(row.get("model_calls")) is int
                and row["model_calls"] == 0
                and row.get("cost_cny") in {"0", "0.00"}
            )
            cost = "0.00" if cost_known else None
        subtotal = (
            attempt_summary["known_subtotal"] if attempt_summary else (cost if cost_known else None)
        )
        if subtotal is not None:
            cost_total += Decimal(subtotal)
            has_known_cost = True
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
                "attempt_summary": attempt_summary,
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
                    regressions.append(
                        {"item_id": detail["item_id"], "field": field, "reason": "field_regression"}
                    )
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
    baseline_complete = (
        baseline is not None
        and len(baseline) == len(corpus["items"])
        and all(row["status"] in {"succeeded", "abstained"} for row in baseline.values())
    )
    if missing or unknown or failed:
        acceptance = "incomplete"
    elif safety_failures or regressions or unknown_cost or success_count != len(details):
        acceptance = "rejected"
    elif not baseline_complete:
        acceptance = "incomplete"
    else:
        acceptance = "accepted_offline"
    return {
        "schema": SCHEMA,
        "dataset_id": corpus["dataset_id"],
        "dataset_version": corpus["dataset_version"],
        "dataset_sha256": corpus_digest(corpus),
        "candidate": candidate,
        "baseline_complete": baseline_complete,
        "cost_nature": "offline_measurement_only",
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
        "cost_summary": {
            "complete": unknown_cost == 0 and known_cost == len(details),
            "known_subtotal": money(cost_total) if has_known_cost else None,
            "total": money(cost_total)
            if unknown_cost == 0 and known_cost == len(details)
            else None,
            "currency": "CNY",
        },
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
        f"- 费用完整性：{report['cost_summary']['complete']}；已知小计：{report['cost_summary']['known_subtotal'] or '不可比较'}；完整总额：{report['cost_summary']['total'] or 'null（存在未知）'}",
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
    from tools.ai_inference_lab.rsi.snapshots import atomic_create, canonical

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    payload = report if details is None else {**report, "details": details}
    atomic_create(directory / "report.json", canonical(payload))
    atomic_create(directory / "report.md", render_markdown(payload).encode("utf-8"))
