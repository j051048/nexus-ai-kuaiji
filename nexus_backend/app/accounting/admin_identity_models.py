from typing import Literal

from pydantic import Field, SecretStr, StrictStr, field_validator, model_validator

from .models import FirmMemberGrant, InputModel


class AccountRequestCreate(InputModel):
    idempotency_key: StrictStr = Field(min_length=8, max_length=120)
    name: StrictStr = Field(min_length=1, max_length=120)
    email: StrictStr = Field(min_length=3, max_length=254)
    company_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    company_role: Literal["owner", "admin", "finance", "employee"] | None = None
    department_id: StrictStr | None = Field(default=None, min_length=1, max_length=100)
    firm_role: Literal["admin", "operator", "reviewer", "viewer"] | None = None
    provisioning_mode: Literal["activation", "password"] = "activation"
    initial_password: SecretStr | None = Field(default=None, min_length=12, max_length=128, repr=False)

    @field_validator("initial_password", mode="before")
    @classmethod
    def password_is_string(cls, value):
        # SecretStr preserves significant whitespace; never normalize a password.
        if value is not None and not isinstance(value, (str, SecretStr)):
            raise ValueError("初始密码必须为字符串")
        return SecretStr(value) if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        return FirmMemberGrant.normalize_email(value)

    @model_validator(mode="after")
    def target_scope(self):
        if self.provisioning_mode == "password":
            if not self.company_id or self.firm_role or self.initial_password is None:
                raise ValueError("密码开户只适用于企业员工，并须设置初始密码")
        elif self.initial_password is not None:
            raise ValueError("激活邀请不能携带初始密码")
        if self.company_id:
            if not self.company_role or self.firm_role:
                raise ValueError("企业开户必须指定企业角色，不能同时授予事务所角色")
        elif not self.firm_role or self.company_role or self.department_id:
            raise ValueError("事务所开户必须指定事务所角色；部门只适用于企业成员")
        return self


class AccountRequestRetry(InputModel):
    initial_password: SecretStr | None = Field(default=None, min_length=12, max_length=128, repr=False)

    _password_is_string = field_validator("initial_password", mode="before")(
        AccountRequestCreate.password_is_string.__func__
    )


class AccountRequestAccept(InputModel):
    token: StrictStr = Field(min_length=32, max_length=160, repr=False)
