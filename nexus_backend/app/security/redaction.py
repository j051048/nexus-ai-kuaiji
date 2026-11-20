"""Field-level display redaction; internal matching always uses the full value."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = frozenset({"bank_account", "account_no", "tax_id", "phone", "mobile", "id_card"})


def mask_bank_account(value: str) -> str:
    digits = re.sub(r"\s+", "", value or "")
    if len(digits) <= 4:
        return "****" if digits else ""
    return "****" + digits[-4:]


def mask_tax_id(value: str) -> str:
    value = (value or "").strip()
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    if len(digits) < 7:
        return "*" * len(digits)
    return digits[:3] + "*" * (len(digits) - 7) + digits[-4:]


def mask_id_card(value: str) -> str:
    value = (value or "").strip()
    if len(value) < 10:
        return "*" * len(value)
    return value[:6] + "*" * (len(value) - 10) + value[-4:]


def mask_sensitive_value(field: str, value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if field in {"bank_account", "account_no"}:
        return mask_bank_account(value)
    if field == "tax_id":
        return mask_tax_id(value)
    if field in {"phone", "mobile"}:
        return mask_phone(value)
    if field == "id_card":
        return mask_id_card(value)
    return value


def redact_sensitive(value: Any) -> Any:
    """Recursively mask known fields without changing the stored record."""
    if isinstance(value, dict):
        return {
            key: mask_sensitive_value(key, item)
            if key in SENSITIVE_KEYS
            else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value
