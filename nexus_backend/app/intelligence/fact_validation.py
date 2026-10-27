"""Deterministic fact validation shared by runtime adapters and offline tests.

Evidence is supplied by authorized service reads. This module cannot authorize
access and never executes a ledger action or trusts model-provided arithmetic.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, Inexact, InvalidOperation, localcontext
import re
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
    book_id: str | None
    evidence_version: int
    unit: str = ""
    currency: str = ""
    evidence_ids: tuple[str, ...] = ()
    calculation: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvidenceFact:
    field: str
    value: Any
    company_id: str
    book_id: str | None
    version: int
    currency: str
    evidence_id: str
    unit: str = ""
    value_type: Literal["auto", "decimal", "text"] = "auto"


@dataclass(frozen=True)
class CalculationRule:
    """A service-owned output meaning; never accepted inside model output."""

    target: str
    operator: Literal["+", "-", "*", "/"]
    input_fields: tuple[str, ...]


_DEFAULT_CALCULATION_RULES = (
    CalculationRule("net_amount", "-", ("gross_amount", "tax_amount")),
    CalculationRule("gross_amount", "+", ("net_amount", "tax_amount")),
    CalculationRule("tax_amount", "-", ("gross_amount", "net_amount")),
)
_INCOME_FIELDS = {
    "revenue",
    "profit",
    "operating_revenue",
    "net_profit",
    "operating_profit",
    "income",
}


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
        return deepcopy(
            {
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
        )


def _decimal(value: Any) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise AnswerError("claim_value_invalid", "数值字段不接受布尔、浮点或隐式类型转换")
    if type(value) is str and (
        len(value) > 96 or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value)
    ):
        raise AnswerError("claim_value_invalid", "数值字段必须是普通十进制数，不接受指数或表达式")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise AnswerError("claim_value_invalid", "数值字段不是有效十进制数") from None
    if not result.is_finite():
        raise AnswerError("claim_value_invalid", "数值字段不是有限数")
    if (
        result.copy_abs() > Decimal("1000000000000000")
        or len(result.as_tuple().digits) > 28
        or result.as_tuple().exponent < -8
    ):
        raise AnswerError("claim_value_invalid", "数值字段超出金额、位数或精度上限")
    return result


def _text(value: Any, name: str, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and (not value or value != value.strip())):
        raise AnswerError("claim_structure_invalid", f"{name} 必须为有效字符串")
    return value


def _book(value: Any) -> str | None:
    return None if value is None else _text(value, "book_id")


def _version(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 100_000_000:
        raise AnswerError("claim_version_stale", "证据版本必须为当前有效整数版本")
    return value


def _currency(value: Any) -> str:
    _text(value, "currency", empty=True)
    if value and not re.fullmatch(r"[A-Z]{3}", value):
        raise AnswerError("claim_currency_mismatch", "币种必须为大写三字母代码")
    return value


def _value_type(item: EvidenceFact) -> str:
    if type(item.value_type) is not str or item.value_type not in {"auto", "decimal", "text"}:
        raise AnswerError("claim_structure_invalid", "证据字段类型无效")
    if item.value_type != "auto":
        return item.value_type
    # The old AG05 adapter adds document currency to every evidence field,
    # including the currency/name/identifier itself. Keep those values textual;
    # new runtime adapters must declare decimal/text explicitly.
    text_fields = {
        "currency",
        "invoice_number",
        "invoice_code",
        "date",
        "invoice_date",
        "transaction_date",
        "counterparty",
        "counterparty_name",
        "company_name",
        "tax_id",
        "description",
        "status",
        "document_type",
        "account_code",
        "bank_account",
        "vendor_id",
    }
    return "decimal" if item.currency and _semantic_field(item.field) not in text_fields else "text"


def _index_evidence(evidence, *, company_id, book_id, fact_version):
    if type(evidence) not in (list, tuple) or len(evidence) > 4096:
        raise AnswerError("claim_structure_invalid", "证据列表类型或数量无效")
    by_id = {}
    for item in evidence:
        if not isinstance(item, EvidenceFact):
            raise AnswerError("claim_structure_invalid", "证据结构无效")
        _text(item.evidence_id, "evidence_id")
        _text(item.field, "field")
        _text(item.company_id, "company_id")
        _book(item.book_id)
        _currency(item.currency)
        _text(item.unit, "unit", empty=True)
        _version(item.version)
        if item.company_id != company_id or item.book_id != book_id:
            raise AnswerError("claim_scope_mismatch", "引用证据跨企业或账套")
        if fact_version is not None and item.version != fact_version:
            raise AnswerError("claim_version_stale", "引用证据不是当前版本")
        if _value_type(item) == "decimal":
            _decimal(item.value)
        else:
            _text(item.value, "evidence.value", empty=True)
        if item.evidence_id in by_id and by_id[item.evidence_id] != item:
            raise AnswerError("claim_evidence_conflict", "同一证据标识包含不同内容")
        by_id[item.evidence_id] = item
    return by_id


def _same_source_scope(claim, source):
    if source.company_id != claim.company_id or source.book_id != claim.book_id:
        raise AnswerError("claim_scope_mismatch", "引用证据跨企业或账套")
    if source.version != claim.evidence_version:
        raise AnswerError("claim_version_stale", "引用证据不是当前版本")


def _semantic_field(value):
    return value.rsplit("/", 1)[-1]


def _semantic_guard(claim, source):
    if (
        _semantic_field(source.field) == "source_gross_total"
        and _semantic_field(claim.field) in _INCOME_FIELDS
    ):
        raise AnswerError(
            "claim_semantic_relabel", "来源合计不能改写为收入或利润，包括通过计算改写"
        )


def _source_for(reference, by_id):
    _text(reference, "evidence_id")
    if reference not in by_id:
        raise AnswerError("claim_evidence_unknown", "引用证据不存在")
    return by_id[reference]


def _calculation_rules(extra):
    if type(extra) not in (tuple, list) or len(extra) > 32:
        raise AnswerError("claim_calculation_policy_invalid", "服务端计算规则类型或数量无效")
    result = list(_DEFAULT_CALCULATION_RULES)
    for rule in extra:
        if not isinstance(rule, CalculationRule):
            raise AnswerError("claim_calculation_policy_invalid", "计算规则必须由服务端显式声明")
        _text(rule.target, "calculation.target")
        if (
            type(rule.operator) is not str
            or rule.operator not in {"+", "-", "*", "/"}
            or type(rule.input_fields) is not tuple
            or not 2 <= len(rule.input_fields) <= 8
        ):
            raise AnswerError("claim_calculation_policy_invalid", "服务端计算规则结构无效")
        for field_name in rule.input_fields:
            _text(field_name, "calculation.input_field")
        if _semantic_field(rule.target) in _INCOME_FIELDS:
            raise AnswerError(
                "claim_calculation_policy_invalid", "当前校验契约不支持推导收入或利润"
            )
        if (
            rule.target in {item.target for item in _DEFAULT_CALCULATION_RULES}
            and rule not in _DEFAULT_CALCULATION_RULES
        ):
            raise AnswerError("claim_calculation_policy_invalid", "不得覆盖已声明的金额推导关系")
        if rule not in result:
            result.append(rule)
    return result


def _calculation(
    claim: Claim, by_id: dict[str, EvidenceFact], rules: list[CalculationRule]
) -> Decimal:
    calculation = claim.calculation
    if (
        type(calculation) is not dict
        or set(calculation) != {"operator", "inputs"}
        or type(calculation.get("operator")) is not str
        or calculation["operator"] not in {"+", "-", "*", "/"}
    ):
        raise AnswerError("claim_calculation_invalid", "计算只允许固定四则运算和证据输入")
    inputs = calculation["inputs"]
    if type(inputs) is not list or not 2 <= len(inputs) <= 8:
        raise AnswerError("claim_calculation_invalid", "计算输入数量无效")
    sources = []
    for item in inputs:
        if type(item) is not dict or set(item) != {"evidence_id", "field"}:
            raise AnswerError(
                "claim_calculation_invalid", "计算输入必须逐项引用证据，不接受自报数字"
            )
        source = _source_for(item["evidence_id"], by_id)
        if source.field != item["field"]:
            raise AnswerError("claim_field_mismatch", "计算字段名与引用证据不一致")
        _same_source_scope(claim, source)
        _semantic_guard(claim, source)
        if _value_type(source) != "decimal":
            raise AnswerError("claim_calculation_invalid", "计算操作数必须为经过声明的数值字段")
        sources.append(source)
    if set(claim.evidence_ids) != {source.evidence_id for source in sources}:
        raise AnswerError("claim_calculation_invalid", "计算引用必须完整对应全部操作数")
    operator = calculation["operator"]
    input_fields = tuple(source.field for source in sources)

    def matches(rule):
        if rule.target != claim.field or rule.operator != operator:
            return False
        return (
            sorted(rule.input_fields) == sorted(input_fields)
            if operator in {"+", "*"}
            else rule.input_fields == input_fields
        )

    if not any(matches(rule) for rule in rules):
        raise AnswerError(
            "claim_calculation_semantic_invalid", "计算目标、运算和来源字段没有服务端声明的关系"
        )
    dimensions = [(source.currency, source.unit) for source in sources]
    desired = (claim.currency, claim.unit)
    if operator in {"+", "-"}:
        valid_dimensions = all(dimension == desired for dimension in dimensions)
    elif operator == "*":
        # A monetary value may be multiplied by a dimensionless rate. Two
        # monetary operands do not produce another monetary amount.
        dimensional = [dimension for dimension in dimensions if dimension != ("", "")]
        valid_dimensions = dimensional == ([] if desired == ("", "") else [desired])
    else:
        valid_dimensions = dimensions[0] == desired and all(
            dimension == ("", "") for dimension in dimensions[1:]
        )
    if not valid_dimensions:
        raise AnswerError("claim_currency_mismatch", "计算单位或币种不一致")
    values = [_decimal(source.value) for source in sources]
    try:
        with localcontext() as context:
            context.prec = 64
            context.traps[Inexact] = True
            result = values[0]
            for value in values[1:]:
                if operator == "+":
                    result += value
                elif operator == "-":
                    result -= value
                elif operator == "*":
                    result *= value
                else:
                    if value == 0:
                        raise AnswerError("claim_calculation_invalid", "计算不能除以零")
                    result /= value
                _decimal(result)
    except DecimalException:
        raise AnswerError(
            "claim_calculation_invalid", "计算需要舍入或超过精度，必须人工核定"
        ) from None
    return result


def validate_bound_claims(
    claims,
    *,
    company_id: str,
    book_id: str | None,
    fact_version: int | None,
    evidence: list[EvidenceFact],
    require_verified: bool = False,
    calculation_rules: tuple[CalculationRule, ...] = (),
) -> list[Claim]:
    """Validate every claim. None fact_version means per-evidence current versions.

    Company/book are still exact scope constraints. The caller owns the mapping
    from a current authorized ContextPack to this allowlist of evidence fields.
    """
    _text(company_id, "company_id")
    _book(book_id)
    if fact_version is not None:
        _version(fact_version)
    if type(require_verified) is not bool or type(claims) not in (list, tuple) or len(claims) > 256:
        raise AnswerError("claim_structure_invalid", "字段列表或校验模式无效")
    if require_verified and not claims:
        raise AnswerError("answer_ready_unverified", "ready 没有任何可核验事实")
    by_id = _index_evidence(
        evidence, company_id=company_id, book_id=book_id, fact_version=fact_version
    )
    rules = _calculation_rules(calculation_rules)
    checked = []
    for claim in claims:
        if not isinstance(claim, Claim):
            raise AnswerError("claim_structure_invalid", "字段结构无效")
        _text(claim.field, "field")
        _text(claim.company_id, "company_id")
        _book(claim.book_id)
        _version(claim.evidence_version)
        _currency(claim.currency)
        _text(claim.unit, "unit", empty=True)
        if claim.company_id != company_id or claim.book_id != book_id:
            raise AnswerError("claim_scope_mismatch", "字段引用跨企业或账套")
        if fact_version is not None and claim.evidence_version != fact_version:
            raise AnswerError("claim_version_stale", "字段引用不是当前事实版本")
        if (
            type(claim.evidence_ids) not in (tuple, list)
            or any(type(ref) is not str or not ref for ref in claim.evidence_ids)
            or len(set(claim.evidence_ids)) != len(claim.evidence_ids)
        ):
            raise AnswerError("claim_structure_invalid", "证据引用列表无效或重复")
        if type(claim.kind) is not str or claim.kind not in {
            "source_fact",
            "calculation",
            "unknown",
            "inference",
        }:
            raise AnswerError("claim_source_missing", "字段来源类型无效")
        if claim.kind != "calculation" and claim.calculation is not None:
            raise AnswerError("claim_structure_invalid", "非计算字段不得附带计算结构")
        if claim.kind in {"unknown", "inference"}:
            if require_verified:
                raise AnswerError("answer_ready_unverified", "ready 不得包含未知或推断字段")
            if claim.kind == "unknown" and claim.value is not None:
                raise AnswerError("claim_unknown_has_value", "unknown 不能携带确定值")
            for ref in claim.evidence_ids:
                _same_source_scope(claim, _source_for(ref, by_id))
            checked.append(deepcopy(claim))
            continue
        if not claim.evidence_ids:
            raise AnswerError("claim_source_missing", "来源事实必须引用证据")
        if claim.kind == "calculation":
            expected = _calculation(claim, by_id, rules)
            if _decimal(claim.value) != expected:
                raise AnswerError("claim_calculation_mismatch", "计算结论与当前证据输入不一致")
        else:
            for ref in claim.evidence_ids:
                source = _source_for(ref, by_id)
                _same_source_scope(claim, source)
                _semantic_guard(claim, source)
                if source.field != claim.field:
                    raise AnswerError("claim_field_mismatch", "字段名与引用证据不一致")
                if source.currency != claim.currency or source.unit != claim.unit:
                    raise AnswerError("claim_currency_mismatch", "单位或币种与引用证据不一致")
                if _value_type(source) == "decimal":
                    matches = _decimal(source.value) == _decimal(claim.value)
                else:
                    matches = type(claim.value) is str and source.value == claim.value
                if not matches:
                    raise AnswerError("claim_value_mismatch", "字段值与被引用证据不一致")
        checked.append(deepcopy(claim))
    return checked


def validate_suggestion(
    suggestion: Suggestion,
    *,
    company_id: str,
    book_id: str | None,
    fact_version: int,
    evidence: list[EvidenceFact],
    calculation_rules: tuple[CalculationRule, ...] = (),
) -> Suggestion:
    if not isinstance(suggestion, Suggestion):
        raise AnswerError("answer_structure_invalid", "建议结构无效")
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

    validate_bound_claims(
        suggestion.claims,
        company_id=company_id,
        book_id=book_id,
        fact_version=fact_version,
        evidence=evidence,
        require_verified=suggestion.status == "suggestion_ready",
        calculation_rules=calculation_rules,
    )
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
