"""Deterministic primitives shared by requests, domain workflows and SQL probes."""

import re
from datetime import date

MAX_CENTS = 99_999_999_999_999
MONEY_PATTERN = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?\Z")
PERIOD_PATTERN = re.compile(r"20[0-9]{2}-(?:0[1-9]|1[0-2])\Z")


def money_to_cents(value: str) -> int:
    if not isinstance(value, str) or not MONEY_PATTERN.fullmatch(value):
        raise ValueError("金额必须是非负十进制字符串，最多两位小数，不接受指数或浮点数")
    whole, _, fraction = value.partition(".")
    result = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    if result > MAX_CENTS:
        raise ValueError("金额超出允许范围")
    return result


def cents_to_money(cents: int) -> str:
    if type(cents) is not int or not 0 <= cents <= MAX_CENTS:
        raise ValueError("金额须为允许范围内的整数分")
    return f"{cents // 100}.{cents % 100:02d}"


def signed_cents_to_money(cents: int) -> str:
    """Format a signed balance or net change without weakening non-negative input rules."""
    if type(cents) is not int or abs(cents) > MAX_CENTS:
        raise ValueError("金额须为允许范围内的整数分")
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    return f"{sign}{value // 100}.{value % 100:02d}"


def validate_period(value: str) -> str:
    if not isinstance(value, str) or not PERIOD_PATTERN.fullmatch(value):
        raise ValueError("期间必须是 2000 至 2099 年内的 YYYY-MM")
    return value


def validate_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期必须是 YYYY-MM-DD")
    date.fromisoformat(value)
    return value
