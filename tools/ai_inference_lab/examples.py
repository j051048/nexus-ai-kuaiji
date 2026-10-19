"""Read-only enterprise example retrieval and regression evaluation (AG05)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
    if len(baseline) != len(candidate):
        raise RetrievalError("evaluation_denominator_mismatch", "基线与候选分母不一致")
    if not baseline:
        raise RetrievalError("evaluation_empty", "没有可评分结果")
    total = len(baseline)
    keys = ("amount_correct", "risk_escalated", "cross_company", "unknown", "missing")
    base = {key: sum(1 for row in baseline if row.get(key)) for key in keys}
    cand = {key: sum(1 for row in candidate if row.get(key)) for key in keys}
    avg_base = sum(1 for row in baseline if row.get("amount_correct")) / total
    avg_cand = sum(1 for row in candidate if row.get("amount_correct")) / total
    regression = (
        cand["amount_correct"] < base["amount_correct"]
        or cand["risk_escalated"] < base["risk_escalated"]
        or cand["cross_company"] > base["cross_company"]
        or cand["unknown"] > base["unknown"]
        or cand["missing"] > base["missing"]
    )
    return {
        "denominator": total,
        "baseline": {"amount_correct": base["amount_correct"], "average": round(avg_base, 4)},
        "candidate": {"amount_correct": cand["amount_correct"], "average": round(avg_cand, 4)},
        "unknown": cand["unknown"],
        "missing": cand["missing"],
        "regression": regression,
        "result": "regression" if regression else "improvement" if avg_cand > avg_base else "no_improvement",
    }


def validate_splits(rows: Iterable[Example], evaluation_ids: set[str]) -> None:
    seen = set()
    for row in rows:
        if row.example_id in seen:
            raise RetrievalError("example_duplicate", "样例重复")
        seen.add(row.example_id)
        if row.split == "test" and row.example_id in evaluation_ids:
            # The stored example itself cannot be in the retrieval corpus.
            raise RetrievalError("example_leak", "待测样例泄漏到检索集")


def validate_manifest(manifest: dict[str, Any], expected: dict[str, Any]) -> None:
    for key in ("policy_version", "prompt_version", "context_version", "split"):
        if manifest.get(key) != expected.get(key):
            raise RetrievalError("manifest_stale", f"{key} 与评分候选不一致")


def money(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception:
        raise RetrievalError("example_amount_invalid", "样例金额无效") from None
    if not result.is_finite():
        raise RetrievalError("example_amount_invalid", "样例金额无效")
    return result
