"""Deterministic offline ContextPack compiler (AG02).

No network, model, embedding database or production service dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from hashlib import sha256
import json
from typing import Any, Iterable, Literal

CONTEXT_VERSION = "context-pack-v1"
TASKS = {"voucher_explain", "journal_suggest", "difference_investigate", "missing_material", "progress"}


class ContextError(ValueError):
    code = "context_invalid"

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class BudgetExceeded(ContextError):
    code = "context_budget_exceeded"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Evidence:
    attachment_id: str
    extraction_id: str
    document_id: str
    page: int
    field: str
    value: Any
    version: int = 1

    @property
    def identity(self) -> str:
        # Every document owns an identity: one attachment may carry several invoices.
        return fingerprint({
            "attachment_id": self.attachment_id,
            "extraction_id": self.extraction_id,
            "document_id": self.document_id,
            "page": self.page,
            "field": self.field,
            "version": self.version,
        })


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    version: int
    source: Literal["approved_fact", "ocr_unreviewed", "user_text", "history"]
    evidence_id: str | None = None


@dataclass(frozen=True)
class HistoryItem:
    question: str
    answer: str
    fact_version: int
    evidence_ids: tuple[str, ...] = ()
    stale: bool = False


@dataclass(frozen=True)
class ContextPack:
    version: str
    task: str
    scope: dict[str, str]
    fact_version: int
    facts: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]
    unresolved: tuple[str, ...]
    history: tuple[dict[str, Any], ...]
    coverage: dict[str, Any]
    budget: dict[str, int]
    prompt_version: str
    schema_version: str
    policy_version: str
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scope(value: dict[str, str]) -> dict[str, str]:
    required = ("firm_id", "company_id", "book_id", "period")
    scope = {key: str(value.get(key, "")).strip() for key in required}
    if not all(scope.values()):
        raise ContextError("context_scope_missing", "ContextPack 缺少可信企业/账套/期间范围")
    return scope


def _budget(value: dict[str, int]) -> dict[str, int]:
    try:
        result = {key: int(value[key]) for key in ("input_tokens", "output_reserve")}
    except (KeyError, TypeError, ValueError):
        raise ContextError("context_budget_missing", "ContextPack 缺少输入/输出预算") from None
    if min(result.values()) < 0 or result["output_reserve"] < 1:
        raise ContextError("context_budget_invalid", "ContextPack 预算无效")
    return result


def estimate_tokens(value: Any) -> int:
    """Conservative character-based estimate; never claims exact tokenizer output."""
    return max(1, (len(canonical(value)) + 2) // 3)


def _evidence_rows(rows: Iterable[Evidence]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in rows:
        if item.identity in seen:
            continue
        seen.add(item.identity)
        result.append({
            "id": item.identity,
            "attachment_id": item.attachment_id,
            "extraction_id": item.extraction_id,
            "document_id": item.document_id,
            "page": item.page,
            "field": item.field,
            "value": item.value,
            "version": item.version,
        })
    return result


def compile_context_pack(
    *,
    task: str,
    scope: dict[str, str],
    facts: Iterable[Fact],
    evidence: Iterable[Evidence],
    rules: Iterable[dict[str, Any]] = (),
    history: Iterable[HistoryItem] = (),
    unresolved: Iterable[str] = (),
    budget: dict[str, int],
    prompt_version: str,
    schema_version: str,
    policy_version: str,
    required_keys: Iterable[str] = (),
) -> ContextPack:
    if task not in TASKS:
        raise ContextError("context_task_invalid", "ContextPack 任务类型无效")
    trusted_scope = _scope(scope)
    limits = _budget(budget)
    if not prompt_version or not schema_version or not policy_version:
        raise ContextError("context_version_missing", "ContextPack 版本信息不完整")

    fact_rows = [
        {
            "key": fact.key,
            "value": fact.value,
            "version": fact.version,
            "source": fact.source,
            "evidence_id": fact.evidence_id,
        }
        for fact in facts
    ]
    fact_version = max((row["version"] for row in fact_rows), default=0)
    evidence_rows = _evidence_rows(evidence)
    evidence_ids = {row["id"] for row in evidence_rows}

    # A claim without matching current evidence is not silently upgraded.
    for row in fact_rows:
        if row["source"] == "approved_fact" and row["evidence_id"] not in evidence_ids:
            raise ContextError("context_evidence_missing", "已批准事实缺少当前证据引用")
        if row["source"] == "ocr_unreviewed" and row["evidence_id"] not in evidence_ids:
            raise ContextError("context_evidence_missing", "识别结果缺少当前证据引用")

    history_rows = []
    for item in history:
        history_rows.append({
            "question": item.question,
            "answer": item.answer,
            "fact_version": item.fact_version,
            "evidence_ids": list(item.evidence_ids),
            "stale": item.stale or item.fact_version != fact_version,
        })
    if any(row["stale"] for row in history_rows):
        history_rows = [
            {**row, "answer": ""} if row["stale"] else row for row in history_rows
        ]

    rule_rows = []
    for rule in rules:
        rule_rows.append({
            "key": str(rule.get("key", "")),
            "version": str(rule.get("version", "")),
            "content": rule.get("content", ""),
        })

    required = set(required_keys)
    required.update(row["key"] for row in fact_rows if row["key"] in required)
    missing_required = sorted(required - {row["key"] for row in fact_rows})
    if missing_required:
        raise ContextError("context_required_missing", "关键事实不足：" + ", ".join(missing_required))

    core = {
        "version": CONTEXT_VERSION,
        "task": task,
        "scope": trusted_scope,
        "fact_version": fact_version,
        "facts": fact_rows,
        "evidence": evidence_rows,
        "rules": rule_rows,
        "history": history_rows,
        "unresolved": sorted(set(unresolved)),
    }
    full = {
        **core,
        "budget": limits,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "policy_version": policy_version,
    }
    estimated = estimate_tokens(full)
    available = limits["input_tokens"] - limits["output_reserve"]
    original_history = len(history_rows)
    original_rules = len(rule_rows)
    if estimated > available:
        # Drop low-value history first, then rules. Facts, evidence, conflicts and
        # unresolved issues are never silently removed.
        while history_rows and estimate_tokens({**full, "history": history_rows}) > available:
            history_rows.pop()
        while rule_rows and estimate_tokens({**full, "rules": rule_rows}) > available:
            rule_rows.pop()
        if estimate_tokens({**full, "history": history_rows, "rules": rule_rows}) > available:
            raise BudgetExceeded(
                "context_budget_exceeded",
                f"关键事实与证据需要 {estimated} 估算 token，超过可用 {available}",
            )
    included = {
        "facts": len(fact_rows),
        "evidence": len(evidence_rows),
        "rules": len(rule_rows),
        "history": len(history_rows),
    }
    coverage = {
        "included": included,
        "omitted": {
            "facts": 0,
            "evidence": 0,
            "rules": max(0, original_rules - len(rule_rows)),
            "history": max(0, original_history - len(history_rows)),
        },
        "estimated_tokens": estimate_tokens({**full, "history": history_rows, "rules": rule_rows}),
        "estimate": "conservative_char_estimate_not_exact_tokenizer",
        "complete_required_facts": True,
    }
    payload = {**full, "history": history_rows, "rules": rule_rows, "coverage": coverage}
    return ContextPack(
        version=CONTEXT_VERSION,
        task=task,
        scope=trusted_scope,
        fact_version=fact_version,
        facts=tuple(fact_rows),
        evidence=tuple(evidence_rows),
        rules=tuple(rule_rows),
        unresolved=tuple(sorted(set(unresolved))),
        history=tuple(history_rows),
        coverage=coverage,
        budget=limits,
        prompt_version=prompt_version,
        schema_version=schema_version,
        policy_version=policy_version,
        fingerprint=fingerprint(payload),
    )


def load_context_pack(payload: dict[str, Any]) -> ContextPack:
    if not isinstance(payload, dict) or payload.get("version") != CONTEXT_VERSION:
        raise ContextError("context_version_unknown", "未知 ContextPack 版本，拒绝执行")
    return ContextPack(
        version=payload["version"],
        task=payload["task"],
        scope=payload["scope"],
        fact_version=payload["fact_version"],
        facts=tuple(payload["facts"]),
        evidence=tuple(payload["evidence"]),
        rules=tuple(payload["rules"]),
        unresolved=tuple(payload["unresolved"]),
        history=tuple(payload["history"]),
        coverage=payload["coverage"],
        budget=payload["budget"],
        prompt_version=payload["prompt_version"],
        schema_version=payload["schema_version"],
        policy_version=payload["policy_version"],
        fingerprint=payload["fingerprint"],
    )
