"""银行数据双适配器：不同来源归一成同一种「银行行」。

对账逻辑不该关心数据是从柜台 CSV 来的还是从电子回单 JSON 来的；
适配器只负责把来源差异挡在外面，并保留可追溯的来源标记。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Iterable, Literal

SIDE = Literal["income", "expense"]


class AdapterError(ValueError):
    """来源数据不合法：宁可整批拒绝，也不猜测金额或方向。"""


@dataclass(frozen=True)
class BankLine:
    account: str
    serial: str
    date: str
    amount_cents: int
    direction: SIDE
    counterparty: str
    source: str
    raw: dict = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict:
        return {
            "account": self.account,
            "serial": self.serial,
            "date": self.date,
            "amount_cents": self.amount_cents,
            "direction": self.direction,
            "counterparty": self.counterparty,
            "source": self.source,
        }


def _cents(value: str, *, field_name: str) -> int:
    text = (value or "").strip().replace(",", "")
    if not text:
        raise AdapterError(f"{field_name} 为空")
    negative = text.startswith("-")
    text = text.lstrip("-")
    if "." in text:
        whole, _, frac = text.partition(".")
        frac = (frac + "00")[:2]
        if not whole.isdigit() or not frac.isdigit():
            raise AdapterError(f"{field_name} 不是合法金额：{value}")
        cents = int(whole) * 100 + int(frac)
    elif text.isdigit():
        cents = int(text) * 100
    else:
        raise AdapterError(f"{field_name} 不是合法金额：{value}")
    return -cents if negative else cents


def _direction(value: str) -> SIDE:
    text = (value or "").strip().lower()
    if text in {"income", "in", "收", "收入", "贷"}:
        return "income"
    if text in {"expense", "out", "支", "支出", "借"}:
        return "expense"
    raise AdapterError(f"未知的收支方向：{value}")


CSV_COLUMNS = ("account", "serial", "date", "amount", "direction", "counterparty")


def parse_bank_csv(text: str, *, source: str = "counter_csv") -> list[BankLine]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [name for name in CSV_COLUMNS if name not in (reader.fieldnames or [])]
    if missing:
        raise AdapterError(f"银行 CSV 缺少列：{', '.join(missing)}")
    lines: list[BankLine] = []
    for index, row in enumerate(reader, start=2):
        try:
            cents = _cents(row["amount"], field_name=f"第 {index} 行 amount")
            direction = _direction(row["direction"])
        except AdapterError as error:
            raise AdapterError(f"第 {index} 行：{error}") from error
        if cents <= 0:
            raise AdapterError(f"第 {index} 行：金额必须大于零，方向由 direction 表达")
        lines.append(
            BankLine(
                account=row["account"].strip(),
                serial=row["serial"].strip(),
                date=row["date"].strip(),
                amount_cents=cents,
                direction=direction,
                counterparty=row["counterparty"].strip(),
                source=source,
                raw=dict(row),
            )
        )
    if not lines:
        raise AdapterError("银行 CSV 没有数据行")
    return lines


RECEIPT_KEYS = ("account", "serial", "date", "amount_cents", "direction", "counterparty")


def parse_receipt_json(payload: str | bytes, *, source: str = "etax_receipt") -> list[BankLine]:
    if isinstance(payload, (dict, list)):
        data = payload
    else:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as error:
            raise AdapterError("电子回单不是合法 JSON") from error
    rows = data.get("receipts") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise AdapterError("电子回单里没有 receipts")
    lines: list[BankLine] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise AdapterError(f"第 {index} 条回单不是对象")
        missing = [key for key in RECEIPT_KEYS if key not in row]
        if missing:
            raise AdapterError(f"第 {index} 条回单缺少字段：{', '.join(missing)}")
        amount = row["amount_cents"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise AdapterError(f"第 {index} 条回单金额必须是正整数分")
        lines.append(
            BankLine(
                account=str(row["account"]).strip(),
                serial=str(row["serial"]).strip(),
                date=str(row["date"]).strip(),
                amount_cents=amount,
                direction=_direction(str(row["direction"])),
                counterparty=str(row["counterparty"]).strip(),
                source=source,
                raw=dict(row),
            )
        )
    return lines


def merge(*batches: Iterable[BankLine]) -> list[BankLine]:
    """同一账号+流水号视为同一行；不同来源重复导入只保留第一条。"""
    seen: set[tuple[str, str]] = set()
    merged: list[BankLine] = []
    for batch in batches:
        for line in batch:
            key = (line.account, line.serial)
            if key in seen:
                continue
            seen.add(key)
            merged.append(line)
    return merged
