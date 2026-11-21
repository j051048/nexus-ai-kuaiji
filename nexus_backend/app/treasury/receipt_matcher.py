"""银行电子回单与流水明细行的确定性匹配。

原则：只在五个字段全部一致且候选唯一时自动挂接；其余一律进人工歧义队列，
绝不按金额或日期猜。金额一律整数分，账号与户名按原样精确比较（不做模糊）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

RECEIPT_FIELDS = ("account", "serial", "traded_on", "amount_cents", "counterparty")


@dataclass(frozen=True)
class Receipt:
    account: str
    serial: str
    traded_on: str
    amount_cents: int
    counterparty: str
    receipt_id: str = ""


@dataclass(frozen=True)
class StatementLine:
    account: str
    serial: str
    traded_on: str
    amount_cents: int
    counterparty: str
    line_id: str = ""


@dataclass
class MatchResult:
    matched: list[tuple[str, str]] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)
    unmatched_lines: list[str] = field(default_factory=list)

    @property
    def matched_pairs(self) -> list[tuple[str, str]]:
        return list(self.matched)


def _key(account: str, serial: str, traded_on: str, amount_cents: int, counterparty: str):
    return (account, serial, traded_on, int(amount_cents), counterparty)


def _complete(account, serial, traded_on, amount_cents, counterparty) -> bool:
    if not account or not serial or not traded_on or not counterparty:
        return False
    return isinstance(amount_cents, int) and amount_cents != 0


def match(receipts: list[Receipt], lines: list[StatementLine]) -> MatchResult:
    """Pair receipts with statement lines; ambiguous cases go to humans."""

    result = MatchResult()
    by_key: dict[tuple, list[str]] = {}
    for line in lines:
        if not _complete(
            line.account, line.serial, line.traded_on, line.amount_cents, line.counterparty
        ):
            result.manual.append(
                {
                    "receipt": None,
                    "reason": "line_incomplete",
                    "line_id": line.line_id,
                    "detail": "流水行缺少账号、流水号、日期、金额或对方户名，无法确定性匹配",
                }
            )
            continue
        by_key.setdefault(
            _key(line.account, line.serial, line.traded_on, line.amount_cents, line.counterparty),
            [],
        ).append(line.line_id)

    used: set[str] = set()
    for receipt in receipts:
        if not _complete(
            receipt.account,
            receipt.serial,
            receipt.traded_on,
            receipt.amount_cents,
            receipt.counterparty,
        ):
            result.manual.append(
                {
                    "receipt": receipt.receipt_id,
                    "reason": "receipt_incomplete",
                    "line_id": None,
                    "detail": "回单缺少账号、流水号、日期、金额或对方户名，转人工核对",
                }
            )
            continue
        candidates = [
            line_id
            for line_id in by_key.get(
                _key(
                    receipt.account,
                    receipt.serial,
                    receipt.traded_on,
                    receipt.amount_cents,
                    receipt.counterparty,
                ),
                [],
            )
            if line_id not in used
        ]
        if len(candidates) == 1:
            used.add(candidates[0])
            result.matched.append((receipt.receipt_id, candidates[0]))
        elif not candidates:
            result.manual.append(
                {
                    "receipt": receipt.receipt_id,
                    "reason": "no_candidate",
                    "line_id": None,
                    "detail": "没有账号、流水号、日期、金额、户名全部一致的流水行",
                }
            )
        else:
            result.manual.append(
                {
                    "receipt": receipt.receipt_id,
                    "reason": "ambiguous",
                    "line_id": None,
                    "detail": f"同日同额同户名有 {len(candidates)} 条候选流水，按金额猜测会造成错配",
                }
            )

    result.unmatched_lines = [
        line_id for ids in by_key.values() for line_id in ids if line_id not in used
    ]
    return result
