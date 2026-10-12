from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    user_id: str
    firm_id: str


class AccountingError(Exception):
    def __init__(self, detail: str, status: int = 422):
        self.detail, self.status = detail, status
        super().__init__(detail)
