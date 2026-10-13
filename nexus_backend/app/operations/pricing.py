"""Explicit CNY tariff inputs. No exchange-rate guesses or supplier price defaults."""

from dataclasses import dataclass
import os
import re

MICRO_PER_YUAN = 1_000_000
MAX_MICRO = 999_999_999_999_990_000
MAX_RATE_MICRO = 1_000_000_000_000
MAX_TOKENS = 10_000_000


def decimal_micro(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?", value
    ):
        raise ValueError("费用须为非负十进制字符串，最多6位小数")
    whole, _, fraction = value.partition(".")
    result = int(whole) * MICRO_PER_YUAN + int(fraction.ljust(6, "0") or "0")
    if result > MAX_MICRO:
        raise ValueError("费用超出范围")
    return result


def micro_decimal(value):
    return None if value is None else f"{value // MICRO_PER_YUAN}.{value % MICRO_PER_YUAN:06d}"


@dataclass(frozen=True)
class Rate:
    input_per_million: str | None = None
    output_per_million: str | None = None

    def __post_init__(self):
        for value in (self.input_per_million, self.output_per_million):
            if value is not None and decimal_micro(value) > MAX_RATE_MICRO:
                raise ValueError("单价配置超出允许范围")

    @property
    def configured(self):
        return self.input_per_million is not None and self.output_per_million is not None

    def snapshot(self, provider, model):
        return {
            "configured": self.configured,
            "input_per_million_micro": decimal_micro(self.input_per_million)
            if self.input_per_million is not None
            else None,
            "output_per_million_micro": decimal_micro(self.output_per_million)
            if self.output_per_million is not None
            else None,
            "provider": provider,
            "model": model,
        }


@dataclass(frozen=True)
class Pricing:
    ocr: Rate = Rate()
    proposal: Rate = Rate()
    chat: Rate = Rate()

    @classmethod
    def from_env(cls):
        def rate(kind):
            values = [
                os.getenv(f"ACCOUNTING_AI_{kind}_{side}_CNY_PER_MILLION", "").strip() or None
                for side in ("INPUT", "OUTPUT")
            ]
            try:
                return Rate(*values)
            except ValueError:
                # Invalid pricing can never silently become a zero-cost tariff.
                return Rate()

        return cls(ocr=rate("OCR"), proposal=rate("PROPOSAL"), chat=rate("CHAT"))

    def for_kind(self, kind):
        return {"ocr": self.ocr, "proposal": self.proposal, "chat": self.chat}[kind]
