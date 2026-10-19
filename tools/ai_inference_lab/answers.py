"""Versioned, offline structured suggestions and field-level validation (AG03)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

ANSWER_VERSION = "suggestion-v1"
SUPPORTED_VERSIONS = {ANSWER_VERSION}
Status = Literal[
    "suggestion_ready",
    "missing_facts",
    "conflicting_evidence",
    "unsupported_case",
    "requires_accountant",
]
ClaimKind = Literal["source_fact", "calculation", "inference", "unknown"]


class AnswerError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Claim:
    field: str
    value: Any
    kind: ClaimKind
    company_id: str
    book_id: str
    evidence_version: int
    unit: str = ""
    currency: str = ""
    evidence_ids: tuple[str, ...] = ()
    calculation: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvidenceFact:
    field: str
    value: str
    company_id: str
    book_id: str
    version: int
    currency: str
    evidence_id: str


@dataclass
class Suggestion:
    status: Status
    version: str = ANSWER_VERSION
    lines: list[dict[str, Any]] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    requires_accountant: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "lines": self.lines,
            "claims": [
                {
                    "field": claim.field,
                    "value": claim.value,
                    "kind": claim.kind,
                    "company_id": claim.company_id,
                    "book_id": claim.book_id,
                    "evidence_version": claim.evidence_version,
                    "unit": claim.unit,
                    "currency": claim.currency,
                    "evidence_ids": list(claim.evidence_ids),
                    "calculation": claim.calculation,
                }
                for claim in self.claims
            ],
            "missing_facts": self.missing_facts,
            "conflicts": self.conflicts,
            "requires_accountant": self.requires_accountant,
            "warnings": self.warnings,
        }


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AnswerError("claim_value_invalid", "数值字段不是有效十进制数") from None
    if not result.is_finite():
        raise AnswerError("claim_value_invalid", "数值字段不是有限数")
    return result


def _calculation(calculation: dict[str, Any]) -> Decimal:
    if not isinstance(calculation, dict) or calculation.get("operator") not in {"+", "-", "*", "/"}:
        raise AnswerError("claim_calculation_invalid", "计算只允许固定四则运算")
    inputs = calculation.get("inputs")
    if not isinstance(inputs, list) or not 2 <= len(inputs) <= 8:
        raise AnswerError("claim_calculation_invalid", "计算输入数量无效")
    values = [_decimal(item) for item in inputs]
    result = values[0]
    for value in values[1:]:
        if calculation["operator"] == "+":
            result += value
        elif calculation["operator"] == "-":
            result -= value
        elif calculation["operator"] == "*":
            result *= value
        else:
            if value == 0:
                raise AnswerError("claim_calculation_invalid", "计算不能除以零")
            result /= value
    return result


def validate_suggestion(
    suggestion: Suggestion,
    *,
    company_id: str,
    book_id: str,
    fact_version: int,
    evidence: list[EvidenceFact],
) -> Suggestion:
    if suggestion.version not in SUPPORTED_VERSIONS:
        raise AnswerError("answer_version_unknown", "未知建议结果版本，拒绝应用")
    if suggestion.status not in {
        "suggestion_ready",
        "missing_facts",
        "conflicting_evidence",
        "unsupported_case",
        "requires_accountant",
    }:
        raise AnswerError("answer_status_invalid", "建议状态无效")
    if suggestion.status == "suggestion_ready":
        if not suggestion.lines:
            raise AnswerError("answer_ready_without_lines", "ready 必须包含建议分录")
        if not suggestion.claims:
            raise AnswerError("answer_ready_without_claims", "ready 必须包含可核验字段")
        if suggestion.missing_facts or suggestion.conflicts:
            raise AnswerError("answer_ready_with_open_issues", "ready 不得同时声明缺失或冲突")
    else:
        if suggestion.lines:
            raise AnswerError("answer_nonready_with_lines", "非 ready 不得包含可应用分录")
        if suggestion.status == "missing_facts" and not suggestion.missing_facts:
            raise AnswerError("answer_missing_detail", "missing_facts 必须列出缺失项")
        if suggestion.status == "conflicting_evidence" and not suggestion.conflicts:
            raise AnswerError("answer_conflict_detail", "conflicting_evidence 必须列出冲突")

    by_id = {item.evidence_id: item for item in evidence}
    verified = 0
    for claim in suggestion.claims:
        if claim.company_id != company_id or claim.book_id != book_id:
            raise AnswerError("claim_scope_mismatch", "字段引用跨企业或账套")
        if claim.evidence_version != fact_version:
            raise AnswerError("claim_version_stale", "字段引用不是当前事实版本")
        if claim.kind == "inference":
            continue
        if claim.kind == "calculation":
            expected = _calculation(claim.calculation or {})
            if _decimal(claim.value) != expected:
                raise AnswerError("claim_calculation_mismatch", "计算结论与输入不一致")
            verified += 1
            continue
        if claim.kind == "unknown":
            if claim.value is not None:
                raise AnswerError("claim_unknown_has_value", "unknown 不能携带确定值")
            continue
        if claim.kind != "source_fact" or not claim.evidence_ids:
            raise AnswerError("claim_source_missing", "来源事实必须引用证据")
        for evidence_id in claim.evidence_ids:
            source = by_id.get(evidence_id)
            if source is None:
                raise AnswerError("claim_evidence_unknown", "引用证据不存在")
            if source.company_id != company_id or source.book_id != book_id:
                raise AnswerError("claim_scope_mismatch", "引用证据跨企业或账套")
            if source.version != fact_version:
                raise AnswerError("claim_version_stale", "引用证据不是当前版本")
            if claim.field in {"revenue", "profit", "operating_revenue"} and source.field == "source_gross_total":
                raise AnswerError("claim_semantic_relabel", "来源合计不能改写为收入或利润")
            # Same number in another column is not semantic proof.
            if source.field != claim.field:
                raise AnswerError("claim_field_mismatch", "字段名与引用证据不一致")
            if source.currency != claim.currency:
                raise AnswerError("claim_currency_mismatch", "币种与引用证据不一致")
            if source.value != str(claim.value):
                raise AnswerError("claim_value_mismatch", "字段值与被引用证据不一致")
        verified += 1
    if suggestion.status == "suggestion_ready" and verified == 0:
        raise AnswerError("answer_ready_unverified", "ready 没有任何可核验事实")
    return suggestion


def to_offline_handoff(suggestion: Suggestion) -> dict[str, Any]:
    """Only an offline review object; never a ledger, approval or export action."""
    if suggestion.status == "suggestion_ready":
        raise AnswerError("handoff_ready_invalid", "ready 建议直接进入草稿复核，不转人工材料")
    return {
        "kind": "offline_accountant_handoff",
        "status": suggestion.status,
        "missing_facts": list(suggestion.missing_facts),
        "conflicts": list(suggestion.conflicts),
        "warnings": list(suggestion.warnings),
        "claims": [claim.field for claim in suggestion.claims if claim.kind != "inference"],
        "requires_accountant": True,
    }


def load_suggestion(payload: dict[str, Any]) -> Suggestion:
    if not isinstance(payload, dict) or payload.get("version") not in SUPPORTED_VERSIONS:
        raise AnswerError("answer_version_unknown", "未知建议结果版本，拒绝应用")
    claims = [
        Claim(
            field=item["field"],
            value=item.get("value"),
            kind=item["kind"],
            company_id=item["company_id"],
            book_id=item["book_id"],
            evidence_version=item["evidence_version"],
            unit=item.get("unit", ""),
            currency=item.get("currency", ""),
            evidence_ids=tuple(item.get("evidence_ids", [])),
            calculation=item.get("calculation"),
        )
        for item in payload.get("claims", [])
    ]
    return Suggestion(
        status=payload["status"],
        version=payload["version"],
        lines=payload.get("lines", []),
        claims=claims,
        missing_facts=payload.get("missing_facts", []),
        conflicts=payload.get("conflicts", []),
        requires_accountant=payload.get("requires_accountant", False),
        warnings=payload.get("warnings", []),
    )
