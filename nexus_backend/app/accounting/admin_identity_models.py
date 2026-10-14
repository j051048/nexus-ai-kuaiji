from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from .models import FirmMemberGrant, InputModel


class AccountRequestCreate(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    name: StrictStr = Field(min_length=1, max_length=120)
    email: StrictStr = Field(min_length=3, max_length=254)
    company_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    company_role: Literal["owner", "admin", "finance", "employee"] | None = None
    department_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    firm_role: Literal["admin", "operator", "reviewer", "viewer"] | None = None

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        return FirmMemberGrant.normalize_email(value)

    @model_validator(mode="after")
    def target_scope(self):
        if self.company_id:
            if not self.company_role or self.firm_role:
                raise ValueError("企业开户必须指定企业角色，不能同时授予事务所角色")
        elif not self.firm_role or self.company_role or self.department_id:
            raise ValueError("事务所开户必须指定事务所角色；部门只适用于企业成员")
        return self


class AccountRequestAccept(InputModel):
    token: StrictStr = Field(min_length=32, max_length=160, repr=False)
