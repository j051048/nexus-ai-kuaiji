from dataclasses import dataclass
from datetime import date
import re
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from app.accounting.models import cents_to_money, money_to_cents


EvidenceField = Literal[
    "invoice_number", "issued_on", "seller_name", "seller_tax_id", "buyer_name",
    "buyer_tax_id", "gross_amount", "net_amount", "tax_amount", "currency",
]


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InvoicePage(OutputModel):
    page: StrictInt = Field(ge=1, le=6)
    raw_text: StrictStr = Field(max_length=30_000)


class InvoiceEvidence(OutputModel):
    field: EvidenceField
    page: StrictInt = Field(ge=1, le=6)
    quote: StrictStr = Field(min_length=1, max_length=500)


class InvoiceDocument(OutputModel):
    document_type: Literal["invoice", "receipt", "other", "unreadable"]
    invoice_number: StrictStr | None = Field(max_length=100)
    issued_on: StrictStr | None
    seller_name: StrictStr | None = Field(max_length=200)
    seller_tax_id: StrictStr | None = Field(max_length=50)
    buyer_name: StrictStr | None = Field(max_length=200)
    buyer_tax_id: StrictStr | None = Field(max_length=50)
    gross_amount: StrictStr | None
    net_amount: StrictStr | None
    tax_amount: StrictStr | None
    currency: StrictStr | None = Field(max_length=10)
    pages: list[InvoicePage] = Field(min_length=1, max_length=6)
    evidence: list[InvoiceEvidence] = Field(max_length=100)

    @field_validator("gross_amount", "net_amount", "tax_amount")
    @classmethod
    def valid_amount(cls, value: str | None) -> str | None:
        return None if value is None else cents_to_money(money_to_cents(value))

    @field_validator("issued_on")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if value is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("日期必须是 YYYY-MM-DD")
            date.fromisoformat(value)
        return value


class InvoiceExtraction(OutputModel):
    documents: list[InvoiceDocument] = Field(min_length=1, max_length=20)


class JournalLine(OutputModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=lambda schema: schema["required"].append("auxiliary_values"))
    account_code: StrictStr = Field(min_length=1, max_length=30)
    debit: StrictStr
    credit: StrictStr
    # Fixed nullable properties keep the provider's strict JSON schema bounded;
    # null values are normalized away for the ledger's existing dict contract.
    auxiliary_values: dict[str, StrictStr] = Field(default_factory=dict, json_schema_extra={
        "type": "object", "properties": {key: {"type": ["string", "null"], "maxLength": 200}
                                              for key in ("department", "counterparty", "employee", "project")},
        "required": ["department", "counterparty", "employee", "project"], "additionalProperties": False,
    })

    @field_validator("auxiliary_values", mode="before")
    @classmethod
    def valid_auxiliaries(cls, value):
        if not isinstance(value, dict) or set(value) - {"department", "counterparty", "employee", "project"}:
            raise ValueError("辅助核算字段不合法")
        if any(item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 200) for item in value.values()):
            raise ValueError("辅助核算值须为1–200字的文本或null")
        return {key: item.strip() for key, item in value.items() if item is not None}

    @field_validator("debit", "credit")
    @classmethod
    def valid_amount(cls, value: str) -> str:
        return cents_to_money(money_to_cents(value))


class JournalEvidenceRef(OutputModel):
    attachment_id: StrictStr = Field(min_length=1, max_length=100)
    extraction_id: StrictStr = Field(min_length=1, max_length=100)
    field: EvidenceField
    page: StrictInt = Field(ge=1, le=6)


class JournalSuggestion(OutputModel):
    description: StrictStr = Field(min_length=1, max_length=500)
    lines: list[JournalLine] = Field(min_length=1, max_length=30)
    reasoning: StrictStr = Field(min_length=1, max_length=2000)
    evidence_refs: list[JournalEvidenceRef] = Field(max_length=100)
    warnings: list[StrictStr] = Field(max_length=20)

    @field_validator("warnings")
    @classmethod
    def valid_warnings(cls, value: list[str]) -> list[str]:
        if any(len(warning) > 500 for warning in value):
            raise ValueError("警示文字过长")
        return value


class ProviderUsage(OutputModel):
    prompt_tokens: StrictInt = Field(ge=0)
    completion_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)


T = TypeVar("T", bound=OutputModel)


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    data: T
    provider: str
    model: str
    prompt_version: str
    usage: ProviderUsage | None


@dataclass(frozen=True)
class PreparedPage:
    page: int
    image_data_url: str
    native_text: str = ""
