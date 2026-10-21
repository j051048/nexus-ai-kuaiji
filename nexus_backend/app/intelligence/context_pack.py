"""One deterministic ContextPack compiler for runtime and offline evaluation.

The caller obtains scope and evidence through authorized service reads. These
checks validate a contract, not authority: a content hash is not a signature.
No network, database, model SDK or offline tools package is imported here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import json
import math
import re
from typing import Any, Iterable, Literal

CONTEXT_VERSION = "context-pack-v2"
TASKS = {
    "voucher_explain", "journal_suggest", "difference_investigate",
    "missing_material", "progress", "assistant_reply", "invoice_extract",
}
FACT_SOURCES = {"approved_fact", "ocr_unreviewed", "user_text", "history", "saved_fact", "source_unreviewed"}
REFERENCED_SOURCES = {"approved_fact", "ocr_unreviewed", "saved_fact", "source_unreviewed"}
ESTIMATE = "utf8_bytes_div3_including_metadata_not_exact_tokenizer"


class ContextError(ValueError):
    code = "context_invalid"

    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


class BudgetExceeded(ContextError):
    code = "context_budget_exceeded"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def _json(value: Any) -> Any:
    """Detach caller objects without accepting lossy JSON coercions."""
    def validate(item: Any, depth: int = 0) -> None:
        if depth > 64:
            raise ContextError("context_json_invalid", "ContextPack JSON 层级过深")
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) in (list, tuple):
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        raise ContextError("context_json_invalid", "ContextPack 只接受有限 JSON 值和字符串键")

    validate(value)
    try:
        return json.loads(canonical(value))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ContextError("context_json_invalid", "ContextPack JSON 值无效") from None


def _text(value: Any, name: str, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value.strip()):
        raise ContextError("context_field_invalid", f"ContextPack {name} 必须为字符串")
    if not empty and value != value.strip():
        raise ContextError("context_field_invalid", f"ContextPack {name} 不得包含首尾空白")
    return value


def _int(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 100_000_000:
        raise ContextError("context_field_invalid", f"ContextPack {name} 必须为范围内整数")
    return value


def _period(value: Any) -> str:
    _text(value, "period")
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value) or value[:4] == "0000":
        raise ContextError("context_scope_invalid", "ContextPack 期间必须为有效 YYYY-MM")
    return value


def _scope(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise ContextError("context_scope_missing", "ContextPack 缺少可信范围")
    if set(value) - {"kind", "firm_id", "company_id", "book_id", "period", "books"}:
        raise ContextError("context_scope_invalid", "ContextPack 范围包含未知字段")
    result = {key: _text(value.get(key), key) for key in ("firm_id", "company_id")}
    kind = value.get("kind", "book")
    if kind == "company":
        if value.get("book_id") is not None or value.get("period") is not None:
            raise ContextError("context_scope_invalid", "企业范围不得假定单一账套/期间")
        books = value.get("books", [])
        if type(books) is not list:
            raise ContextError("context_scope_invalid", "ContextPack books 必须为列表")
        normalized, seen = [], set()
        for book in books:
            if type(book) is not dict or set(book) != {"book_id", "period"}:
                raise ContextError("context_scope_invalid", "ContextPack 账套范围字段无效")
            entry = {"book_id": _text(book["book_id"], "book_id"), "period": _period(book["period"])}
            identity = (entry["book_id"], entry["period"])
            if identity in seen:
                raise ContextError("context_scope_invalid", "ContextPack 账套范围重复")
            seen.add(identity)
            normalized.append(entry)
        return {**result, "kind": "company", "book_id": None, "period": None, "books": normalized}
    if kind != "book" or "books" in value:
        raise ContextError("context_scope_invalid", "ContextPack 范围类型无效")
    result.update(book_id=_text(value.get("book_id"), "book_id"), period=_period(value.get("period")))
    if "kind" in value:
        result["kind"] = "book"
    return result


def _budget(value: Any) -> dict[str, int]:
    if type(value) is not dict or set(value) != {"input_tokens", "output_reserve"}:
        raise ContextError("context_budget_missing", "ContextPack 缺少输入/输出预算")
    limits = {key: _int(value[key], key, 1) for key in ("input_tokens", "output_reserve")}
    if limits["output_reserve"] >= limits["input_tokens"]:
        raise BudgetExceeded("context_budget_exceeded", "ContextPack 没有剩余输入预算")
    return limits


def estimate_tokens(value: Any) -> int:
    """Deterministic UTF-8 estimate, not a provider tokenizer or usage record."""
    return max(1, (len(canonical(value).encode("utf-8")) + 2) // 3)


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
        return fingerprint(_invoice_identity(self))


@dataclass(frozen=True)
class SourceEvidence:
    """A real record/page before (or outside) structured invoice extraction."""
    source_id: str
    kind: str
    value: Any
    version: int = 1

    @property
    def identity(self) -> str:
        return fingerprint(_source_identity(self))


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    version: int
    source: Literal["approved_fact", "ocr_unreviewed", "user_text", "history", "saved_fact", "source_unreviewed"]
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
    scope: dict[str, Any]
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
        payload = _json(asdict(self))
        if fingerprint({key: value for key, value in payload.items() if key != "fingerprint"}) != self.fingerprint:
            raise ContextError("context_integrity_invalid", "ContextPack 编译后内容被修改")
        return payload


def _invoice_identity(item: Evidence) -> dict[str, Any]:
    return {
        "attachment_id": _text(item.attachment_id, "attachment_id"),
        "extraction_id": _text(item.extraction_id, "extraction_id"),
        "document_id": _text(item.document_id, "document_id"),
        "page": _int(item.page, "page", 1), "field": _text(item.field, "field"),
        "version": _int(item.version, "evidence.version", 1),
    }


def _source_identity(item: SourceEvidence) -> dict[str, Any]:
    return {"source_id": _text(item.source_id, "source_id"), "kind": _text(item.kind, "kind"), "version": _int(item.version, "evidence.version", 1)}


def _evidence_rows(rows: Iterable[Evidence | SourceEvidence]) -> list[dict[str, Any]]:
    result, seen = [], {}
    for item in rows:
        if isinstance(item, Evidence):
            identity = _invoice_identity(item)
        elif isinstance(item, SourceEvidence):
            identity = _source_identity(item)
        else:
            raise ContextError("context_evidence_invalid", "ContextPack 证据类型无效")
        row = {"id": fingerprint(identity), **identity, "value": _json(item.value)}
        if row["id"] in seen:
            if canonical(row) != canonical(seen[row["id"]]):
                raise ContextError("context_evidence_conflict", "同一证据身份包含冲突值")
            continue
        seen[row["id"]] = row
        result.append(row)
    return result


def _facts(rows: Iterable[Fact], evidence_ids: set[str]) -> list[dict[str, Any]]:
    result, keys = [], set()
    for item in rows:
        if not isinstance(item, Fact):
            raise ContextError("context_fact_invalid", "ContextPack 事实类型无效")
        key = _text(item.key, "fact.key")
        if key in keys:
            raise ContextError("context_fact_conflict", "ContextPack 事实键重复")
        keys.add(key)
        if type(item.source) is not str or item.source not in FACT_SOURCES:
            raise ContextError("context_fact_invalid", "ContextPack 事实来源无效")
        if item.evidence_id is not None:
            _text(item.evidence_id, "evidence_id")
        if (item.source in REFERENCED_SOURCES or item.evidence_id is not None) and item.evidence_id not in evidence_ids:
            raise ContextError("context_evidence_missing", "事实缺少当前证据引用")
        result.append({"key": key, "value": _json(item.value), "version": _int(item.version, "fact.version"), "source": item.source, "evidence_id": item.evidence_id})
    return result


def _history(rows: Iterable[HistoryItem], fact_version: int, evidence_ids: set[str]) -> list[dict[str, Any]]:
    result = []
    for item in rows:
        if not isinstance(item, HistoryItem) or type(item.stale) is not bool:
            raise ContextError("context_history_invalid", "ContextPack 历史类型无效")
        if type(item.evidence_ids) not in (tuple, list):
            raise ContextError("context_history_invalid", "ContextPack 历史证据必须为列表")
        refs = [_text(ref, "history.evidence_id") for ref in item.evidence_ids]
        if len(set(refs)) != len(refs):
            raise ContextError("context_history_invalid", "ContextPack 历史证据重复")
        version = _int(item.fact_version, "history.fact_version")
        stale = item.stale or version != fact_version or not set(refs).issubset(evidence_ids)
        answer = _text(item.answer, "history.answer", empty=True)
        result.append({
            "question": _text(item.question, "history.question", empty=True),
            "answer": "" if stale else answer, "fact_version": version,
            "evidence_ids": refs, "stale": stale,
        })
    return result


def _rules(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result, keys = [], set()
    for item in rows:
        if type(item) is not dict or set(item) - {"key", "version", "content", "required"}:
            raise ContextError("context_rule_invalid", "ContextPack 规则字段无效")
        key = _text(item.get("key"), "rule.key")
        if key in keys or type(item.get("required", False)) is not bool:
            raise ContextError("context_rule_invalid", "ContextPack 规则重复或 required 无效")
        keys.add(key)
        result.append({"key": key, "version": _text(item.get("version"), "rule.version"), "content": _json(item.get("content", "")), "required": item.get("required", False)})
    return result


def _required(keys: Iterable[str], facts: list[dict[str, Any]]) -> list[str]:
    result = sorted({_text(key, "required_key") for key in keys})
    current = {row["key"]: row["value"] for row in facts}
    missing = [key for key in result if key not in current or current[key] is None]
    if missing:
        raise ContextError("context_required_missing", "关键事实不足：" + ", ".join(missing))
    return result


def _estimate_payload(payload: dict[str, Any]) -> int:
    # Include coverage and the final hex fingerprint. Recompute until the number
    # of digits in the estimated_tokens field itself is accounted for.
    payload["coverage"]["estimated_tokens"] = 0
    for _ in range(12):
        estimated = estimate_tokens({**payload, "fingerprint": "0" * 64})
        if estimated == payload["coverage"]["estimated_tokens"]:
            return estimated
        payload["coverage"]["estimated_tokens"] = estimated
    raise ContextError("context_budget_invalid", "ContextPack 预算估算未收敛")


def _construct(payload: dict[str, Any]) -> ContextPack:
    return ContextPack(**{**payload, **{key: tuple(payload[key]) for key in ("facts", "evidence", "rules", "unresolved", "history")}})


def compile_context_pack(
    *, task: str, scope: dict[str, Any], facts: Iterable[Fact], evidence: Iterable[Evidence | SourceEvidence],
    rules: Iterable[dict[str, Any]] = (), history: Iterable[HistoryItem] = (), unresolved: Iterable[str] = (),
    budget: dict[str, int], prompt_version: str, schema_version: str, policy_version: str,
    required_keys: Iterable[str] = (),
) -> ContextPack:
    if type(task) is not str or task not in TASKS:
        raise ContextError("context_task_invalid", "ContextPack 任务类型无效")
    trusted_scope, limits = _scope(scope), _budget(budget)
    versions = {key: _text(value, key) for key, value in {"prompt_version": prompt_version, "schema_version": schema_version, "policy_version": policy_version}.items()}
    evidence_rows = _evidence_rows(evidence)
    evidence_ids = {row["id"] for row in evidence_rows}
    fact_rows = _facts(facts, evidence_ids)
    fact_version = max((row["version"] for row in fact_rows), default=0)
    history_rows, rule_rows = _history(history, fact_version, evidence_ids), _rules(rules)
    required = _required(required_keys, fact_rows)
    unresolved_rows = sorted({_text(item, "unresolved") for item in unresolved})
    original_history, original_rules = len(history_rows), len(rule_rows)
    payload = {
        "version": CONTEXT_VERSION, "task": task, "scope": trusted_scope, "fact_version": fact_version,
        "facts": fact_rows, "evidence": evidence_rows, "rules": rule_rows, "history": history_rows,
        "unresolved": unresolved_rows, "budget": limits, **versions,
    }
    available = limits["input_tokens"] - limits["output_reserve"]
    while True:
        payload["coverage"] = {
            "included": {key: len(payload[key]) for key in ("facts", "evidence", "rules", "history")},
            "omitted": {"facts": 0, "evidence": 0, "rules": original_rules - len(rule_rows), "history": original_history - len(history_rows)},
            "estimated_tokens": 0, "estimate": ESTIMATE, "complete_required_facts": True, "required_keys": required,
        }
        estimated = _estimate_payload(payload)
        if estimated <= available:
            break
        if history_rows:
            history_rows.pop()
            continue
        optional = next((index for index in range(len(rule_rows) - 1, -1, -1) if not rule_rows[index]["required"]), None)
        if optional is not None:
            rule_rows.pop(optional)
            continue
        raise BudgetExceeded("context_budget_exceeded", f"关键事实、证据与规则需要 {estimated} 估算 token，超过可用 {available}")
    payload["fingerprint"] = fingerprint(payload)
    return _construct(payload)


def load_context_pack(payload: dict[str, Any]) -> ContextPack:
    """Validate structure, references, scope, budget and integrity on load."""
    if type(payload) is not dict or payload.get("version") != CONTEXT_VERSION:
        raise ContextError("context_version_unknown", "未知 ContextPack 版本，拒绝执行")
    if set(payload) != set(ContextPack.__dataclass_fields__):
        raise ContextError("context_structure_invalid", "ContextPack 顶层字段不完整或未知")
    value = _json(payload)
    digest = value.pop("fingerprint")
    if type(digest) is not str or not re.fullmatch("[0-9a-f]{64}", digest) or not hmac.compare_digest(digest, fingerprint(value)):
        raise ContextError("context_integrity_invalid", "ContextPack 内容指纹不一致")
    for key in ("facts", "evidence", "rules", "history", "unresolved"):
        if type(value[key]) is not list:
            raise ContextError("context_structure_invalid", f"ContextPack {key} 必须为列表")
    try:
        evidence = []
        for row in value["evidence"]:
            if type(row) is not dict or "id" not in row:
                raise ContextError("context_evidence_invalid", "ContextPack 证据字段无效")
            data = {key: item for key, item in row.items() if key != "id"}
            item = SourceEvidence(**data) if "source_id" in data else Evidence(**data)
            if row["id"] != item.identity:
                raise ContextError("context_evidence_invalid", "ContextPack 证据身份不匹配")
            evidence.append(item)
        facts = [Fact(**row) for row in value["facts"]]
        history = [HistoryItem(**row) for row in value["history"]]
    except (TypeError, KeyError):
        raise ContextError("context_structure_invalid", "ContextPack 事实或证据结构无效") from None
    coverage = value["coverage"]
    if type(coverage) is not dict or set(coverage) != {"included", "omitted", "estimated_tokens", "estimate", "complete_required_facts", "required_keys"}:
        raise ContextError("context_coverage_invalid", "ContextPack 覆盖信息无效")
    for key in ("included", "omitted"):
        if type(coverage[key]) is not dict or set(coverage[key]) != {"facts", "evidence", "rules", "history"}:
            raise ContextError("context_coverage_invalid", "ContextPack 覆盖计数无效")
        for field, count in coverage[key].items():
            _int(count, f"coverage.{key}.{field}")
    if type(coverage["required_keys"]) is not list:
        raise ContextError("context_coverage_invalid", "ContextPack 必需事实列表无效")
    if coverage["complete_required_facts"] is not True or coverage["estimate"] != ESTIMATE:
        raise ContextError("context_coverage_invalid", "ContextPack 覆盖信息不完整")
    _int(coverage["estimated_tokens"], "coverage.estimated_tokens", 1)
    if coverage["omitted"]["facts"] or coverage["omitted"]["evidence"]:
        raise ContextError("context_coverage_invalid", "ContextPack 不得裁剪关键事实或证据")
    if coverage["included"] != {key: len(value[key]) for key in ("facts", "evidence", "rules", "history")}:
        raise ContextError("context_coverage_invalid", "ContextPack 覆盖计数与内容不符")
    # Recompilation checks semantics, including references and stale history.
    # The original omitted counts describe data no longer present in the pack.
    compiled = compile_context_pack(
        task=value["task"], scope=value["scope"], facts=facts, evidence=evidence, rules=value["rules"],
        history=history, unresolved=value["unresolved"], budget=value["budget"],
        prompt_version=value["prompt_version"], schema_version=value["schema_version"], policy_version=value["policy_version"],
        required_keys=coverage["required_keys"],
    ).as_dict()
    for key in value:
        if key != "coverage" and canonical(value[key]) != canonical(compiled[key]):
            raise ContextError("context_structure_invalid", f"ContextPack {key} 与规范结构不符")
    if coverage["required_keys"] != compiled["coverage"]["required_keys"]:
        raise ContextError("context_coverage_invalid", "ContextPack 必需事实列表不规范")
    measured = estimate_tokens({**value, "fingerprint": digest})
    if coverage["estimated_tokens"] != measured or measured > value["budget"]["input_tokens"] - value["budget"]["output_reserve"]:
        raise BudgetExceeded("context_budget_exceeded", "ContextPack 完整内容预算不一致或超限")
    return _construct({**value, "fingerprint": digest})
