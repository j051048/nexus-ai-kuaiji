"""Read-only enterprise example retrieval and regression evaluation (AG05)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable


class RetrievalError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Example:
    example_id: str
    firm_id: str
    company_id: str
    book_id: str
    vendor_id: str
    business_type: str
    direction: str
    accounting_policy: str
    account_code: str
    amount: str
    reviewed: bool = True
    status: str = "active"
    evidence_complete: bool = True
    split: str = "train"
    source_sample_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Query:
    firm_id: str
    company_id: str
    book_id: str
    vendor_id: str
    business_type: str
    direction: str
    accounting_policy: str
    sample_id: str
    period: str


def retrieve(query: Query, examples: Iterable[Example]) -> list[Example]:
    result = []
    for item in examples:
        if item.firm_id != query.firm_id or item.company_id != query.company_id:
            continue
        if item.book_id != query.book_id:
            continue
        if not item.reviewed or item.status != "active" or not item.evidence_complete:
            continue
        if item.split == "holdout":
            continue
        if item.example_id == query.sample_id or query.sample_id in item.source_sample_ids:
            continue
        if item.vendor_id != query.vendor_id:
            continue
        if item.business_type != query.business_type or item.direction != query.direction:
            continue
        if item.accounting_policy != query.accounting_policy:
            continue
        result.append(item)
    return sorted(result, key=lambda item: (item.business_type, item.example_id))


def evaluate(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    """Compatibility entry; delegates to the single identity-checked scorer."""
    result = evaluate_samples(baseline, candidate)
    result["regression"] = result["result"] == "regression"
    result["unknown"] = sum(1 for row in candidate if row.get("unknown"))
    result["missing"] = sum(1 for row in candidate if row.get("missing"))
    if result["unknown"] or result["missing"]:
        result["result"] = "regression"
        result["regression"] = True
    result["baseline"] = {"average": round(
        sum(1 for row in baseline if row.get("amount_correct")) / max(1, len(baseline)), 4
    )}
    result["candidate"] = {
        "amount_correct": result["candidate_correct"],
        "average": result["average"],
    }
    return result


def evaluation_report(result: dict[str, Any]) -> dict[str, str]:
    payload = {
        "denominator": result.get("denominator"),
        "result": result.get("result"),
        "average": result.get("average"),
        "regressions": result.get("regressions", []),
        "fixes": result.get("fixes", []),
        "absolute_safety_failed": bool(result.get("absolute_safety_failed", False)),
    }
    json_report = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [
        "# 样例评测报告",
        "",
        f"- 分母：{payload['denominator']}",
        f"- 结论：{payload['result']}",
        f"- 平均：{payload['average']}",
        f"- 绝对安全门槛失败：{payload['absolute_safety_failed']}",
        "",
        "## 关键退步",
    ]
    lines.extend(
        f"- {row['sample_id']}：{row['reason']}" for row in payload["regressions"]
    ) if payload["regressions"] else lines.append("- 无")
    return {"json": json_report, "markdown": "\n".join(lines) + "\n"}


def validate_splits(rows: Iterable[Example], evaluation_ids: set[str]) -> None:
    seen = set()
    for row in rows:
        if row.example_id in seen:
            raise RetrievalError("example_duplicate", "样例重复")
        seen.add(row.example_id)
        if row.split == "test" and row.example_id in evaluation_ids:
            # The stored example itself cannot be in the retrieval corpus.
            raise RetrievalError("example_leak", "待测样例泄漏到检索集")


def validate_manifest(
    manifest: dict[str, Any],
    expected: dict[str, Any],
    *,
    allowed_differences: set[str] | None = None,
) -> None:
    required = ("dataset_id", "policy_version", "prompt_version", "context_version", "split", "sample_ids")
    if not isinstance(manifest, dict) or not isinstance(expected, dict):
        raise RetrievalError("manifest_invalid", "manifest 必须是对象")
    for key in required:
        if key not in manifest or key not in expected:
            raise RetrievalError("manifest_missing", f"manifest 缺少 {key}")
        if manifest[key] in (None, "", []):
            raise RetrievalError("manifest_invalid", f"manifest {key} 为空")
    allowed = allowed_differences or set()
    if not allowed.issubset({"prompt_version", "policy_version", "context_version"}):
        raise RetrievalError("manifest_difference_not_allowed", "实验差异只允许 prompt/policy/context 版本")
    for key in required:
        if key in allowed:
            continue
        if manifest[key] != expected[key]:
            raise RetrievalError("manifest_stale", f"{key} 与评分候选不一致")


def evaluate_samples(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    *,
    required_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Compare by frozen sample identity, never by array position."""
    def indexed(rows, label):
        result = {}
        for row in rows:
            sample_id = row.get("sample_id")
            if not isinstance(sample_id, str) or not sample_id:
                raise RetrievalError("evaluation_identity_missing", f"{label} 存在无效 sample_id")
            if sample_id in result:
                raise RetrievalError("evaluation_duplicate", f"{label} 存在重复 sample_id")
            result[sample_id] = row
        return result
    base, cand = indexed(baseline, "baseline"), indexed(candidate, "candidate")
    if required_ids is None:
        required_ids = set(base)
        if set(cand) != required_ids:
            raise RetrievalError("evaluation_identity_mismatch", "基线与候选样本身份不一致")
    else:
        required_ids = set(required_ids)
        if set(base) - required_ids or set(cand) - required_ids:
            raise RetrievalError("evaluation_identity_mismatch", "结果超出冻结样本集")
    if not required_ids:
        raise RetrievalError("evaluation_empty", "冻结样本集为空，不能评分")
    regressions, fixes, missing, unknown = [], [], [], []
    for sample_id in sorted(required_ids):
        b = base.get(sample_id, {"missing": True})
        c = cand.get(sample_id, {"missing": True})
        if c.get("missing"):
            missing.append(sample_id)
            continue
        if c.get("unknown"):
            unknown.append(sample_id)
        if c.get("abstained") and not b.get("abstained"):
            regressions.append({"sample_id": sample_id, "reason": "abstained"})
        elif c.get("cross_company") and not b.get("cross_company"):
            regressions.append({"sample_id": sample_id, "reason": "cross_company"})
        elif any(b.get(key) and not c.get(key) for key in ("amount_correct", "risk_escalated", "evidence_complete")):
            regressions.append({"sample_id": sample_id, "reason": "critical_regression"})
        elif any(not b.get(key) and c.get(key) for key in ("amount_correct", "risk_escalated")):
            fixes.append({"sample_id": sample_id})
    correct = sum(
        1
        for row in cand.values()
        if row.get("amount_correct") and not row.get("unknown") and not row.get("missing")
    )
    risk = {"false_negatives": [], "false_positives": [], "correct_abstentions": []}
    for sample_id in sorted(required_ids):
        row = cand.get(sample_id, {})
        if row.get("risk") and not row.get("risk_escalated"):
            risk["false_negatives"].append(sample_id)
        elif not row.get("risk") and row.get("risk_escalated"):
            risk["false_positives"].append(sample_id)
        elif row.get("risk") and row.get("abstained"):
            risk["correct_abstentions"].append(sample_id)
    safety_failed = bool(
        missing
        or unknown
        or any(row.get("cross_company") for row in cand.values())
        or any(row.get("evidence_complete") is False for row in cand.values())
    )
    result = "regression" if regressions or missing or unknown else "improvement" if fixes else "no_improvement"
    return {
        "denominator": len(required_ids),
        "candidate_correct": correct,
        "average": round(correct / len(required_ids), 4),
        "regressions": regressions,
        "fixes": fixes,
        "missing": missing,
        "unknown": unknown,
        "absolute_safety_failed": safety_failed,
        "risk": risk,
        "result": result,
    }


def h01_manifest_identity(path: str | Path) -> dict[str, Any]:
    """Thin read-only adapter over H01's frozen manifest validator."""
    from tools.ocr_replay import replay

    manifest = replay.load_manifest(Path(path))
    return {
        "dataset_id": manifest["manifest_sha256"],
        "sample_ids": [sample["sample_id"] for sample in manifest["samples"]],
        "companies": {sample["sample_id"]: sample["company_id"] for sample in manifest["samples"]},
        "splits": {sample["sample_id"]: sample.get("split", "test") for sample in manifest["samples"]},
    }


def h01_validate_result(
    manifest_path: str | Path,
    sample_id: str,
    result: dict[str, Any],
    policy=None,
) -> None:
    """Result-level H01 identity check; no duplicate manifest rules."""
    from tools.ocr_replay import replay

    manifest = replay.load_manifest(Path(manifest_path))
    sample = next((item for item in manifest["samples"] if item["sample_id"] == sample_id), None)
    if sample is None:
        raise RetrievalError("h01_sample_unknown", "H01 manifest 中不存在该样本")
    try:
        replay.validate_result_identity(manifest, sample, result, policy)
    except replay.ReplayError as error:
        raise RetrievalError(error.code, error.message) from None


def money(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception:
        raise RetrievalError("example_amount_invalid", "样例金额无效") from None
    if not result.is_finite():
        raise RetrievalError("example_amount_invalid", "样例金额无效")
    return result
