from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from ..accounting.models import InputModel, cents_to_money, money_to_cents
from ..intelligence.models import EvidenceField


class FieldLabel(InputModel):
    extraction_id: StrictStr = Field(min_length=1, max_length=100)
    document_index: int = Field(strict=True, ge=0, le=19)
    field: EvidenceField
    page: int = Field(strict=True, ge=1, le=6)
    value: StrictStr | None = Field(max_length=500)

    @model_validator(mode="after")
    def exact_amount(self):
        if self.field in {"gross_amount", "net_amount", "tax_amount"} and self.value is not None:
            self.value = cents_to_money(money_to_cents(self.value))
        return self


class StandardCreate(InputModel):
    sample_id: StrictStr = Field(min_length=1, max_length=100)
    field_labels: list[FieldLabel] = Field(default_factory=list, max_length=100)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=1, max_length=100)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("须说明原件核对依据")
        return value.strip()

    @field_validator("field_labels")
    @classmethod
    def unique_labels(cls, labels):
        keys = [(label.extraction_id, label.document_index, label.field) for label in labels]
        if len(set(keys)) != len(keys):
            raise ValueError("同一票据字段不能重复标注")
        return labels


class StandardConfirm(InputModel):
    evidence_checked: Literal[True]
    reason: StrictStr = Field(min_length=1, max_length=1000)

    _nonblank = field_validator("reason")(StandardCreate.nonblank.__func__)


class RunCreate(InputModel):
    standard_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    mode: Literal["rules", "templates", "stored_model", "stored_ocr"]
    model: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    prompt_version: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    idempotency_key: StrictStr = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_scope(self):
        if len(set(self.standard_ids)) != len(self.standard_ids) or any(
            not value or len(value) > 100 for value in self.standard_ids
        ):
            raise ValueError("评测标准标识不能重复或过长")
        self.standard_ids = sorted(self.standard_ids)
        stored = self.mode.startswith("stored_")
        if (
            stored != bool(self.model and self.prompt_version)
            or not stored
            and (self.model or self.prompt_version)
        ):
            raise ValueError("存量模型回放须指定模型及提示词版本；规则和模板不使用模型参数")
        return self


class BenchmarkCreate(InputModel):
    name: StrictStr = Field(min_length=1, max_length=100)
    standard_ids: list[StrictStr] = Field(min_length=1, max_length=100)
    reason: StrictStr = Field(min_length=1, max_length=1000)
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)

    @model_validator(mode="after")
    def validate_set(self):
        self.name, self.reason = self.name.strip(), self.reason.strip()
        if (
            not self.name
            or not self.reason
            or len(set(self.standard_ids)) != len(self.standard_ids)
            or any(
                not item.strip() or item != item.strip() or len(item) > 100
                for item in self.standard_ids
            )
        ):
            raise ValueError("请填写样本集名称及依据，标准标识不能重复或过长")
        self.standard_ids = sorted(self.standard_ids)
        return self


class BenchmarkCompare(InputModel):
    baseline_run_id: StrictStr = Field(min_length=1, max_length=100)
    candidate_run_id: StrictStr = Field(min_length=1, max_length=100)
    minimum_samples: int = Field(default=1, strict=True, ge=1, le=100)
    allowed_differences: int = Field(default=0, strict=True, ge=0, le=100)
