"""Return-file contract for PLT05-B.

This module only parses and reconciles the synthetic/agreed CSV contract. It
does not import files, write the database, or mark a real T+ roundtrip as
verified.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.accounting.domain import AccountingError

HEADER = [
    "批次标识",
    "凭证标识",
    "对方凭证号",
    "结果",
    "失败原因",
    "对方过账日期",
    "金额",
    "科目",
]
RESULT_STATES = {
    "accepted",
    "rejected",
    "duplicate",
    "unknown",
    "missing",
    "extra",
    "amount_mismatch",
    "account_mismatch",
}
_MONEY = re.compile(r"^\d{1,15}(?:\.\d{1,2})?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FORMULA = ("=", "+", "-", "@")


def _fail(detail: str, status: int = 422) -> None:
    raise AccountingError(detail, status)


def _cents(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    if not _MONEY.fullmatch(value):
        _fail("金额必须是人民币元且最多两位小数")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        _fail("金额格式不正确")
    return int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _accounts(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    return [part.strip().split(":", 1)[0] for part in value.split("|") if part.strip()]


def parse_return_file(content: bytes, *, batch_code: str, exported: list[dict]) -> dict:
    if len(content) > 2_000_000:
        _fail("返回结果文件最多 2 MB", 413)
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), strict=True)
        if reader.fieldnames != HEADER:
            _fail("返回结果文件表头不符合契约")
        declared: list[dict] = []
        seen: set[str] = set()
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                _fail("返回结果文件行列数不正确")
            row_batch = raw["批次标识"].strip()
            if row_batch != batch_code:
                _fail("返回结果文件批次标识与本次导出不一致")
            code = raw["凭证标识"].strip()
            if not code or code in seen:
                _fail("返回结果文件凭证标识为空或重复")
            seen.add(code)
            result = raw["结果"].strip()
            if result not in RESULT_STATES:
                _fail("返回结果文件结果值不受支持")
            reason = raw["失败原因"].strip()
            if result != "accepted" and not reason:
                _fail("非 accepted 的逐笔结果必须填写失败原因")
            posted = raw["对方过账日期"].strip()
            if posted:
                if not _DATE.fullmatch(posted):
                    _fail("对方过账日期必须是 YYYY-MM-DD")
                try:
                    date.fromisoformat(posted)
                except ValueError:
                    _fail("对方过账日期不是有效日期")
            # 对方凭证号同样来自外部文件，公式前缀注入必须与凭证标识、失败原因一并拦截。
            external_code = raw["对方凭证号"].strip()
            if any(value.startswith(_FORMULA) for value in (code, external_code, reason)):
                _fail("返回结果文件包含不允许的公式前缀")
            declared.append(
                {
                    "voucher_code": code,
                    "external_code": external_code,
                    "status": result,
                    "reason": reason,
                    "posted_date": posted or None,
                    "actual_cents": _cents(raw["金额"]),
                    "accounts": _accounts(raw["科目"]),
                }
            )
    except (UnicodeDecodeError, csv.Error) as error:
        raise AccountingError("返回结果文件编码或 CSV 结构不正确", 422) from error
    if not declared or len(declared) > 10_000:
        _fail("返回结果文件行数必须在 1 到 10000 之间")

    exported_by_code = {item["voucher_code"]: item for item in exported}
    rows: list[dict] = []
    for row in declared:
        expected = exported_by_code.get(row["voucher_code"])
        if expected is None:
            row.update(status="extra", reason=row["reason"] or "不在本次导出范围")
            rows.append(row)
            continue
        status, reason = row["status"], row["reason"]
        if row["actual_cents"] is not None and row["actual_cents"] != expected["amount_cents"]:
            status, reason = "amount_mismatch", reason or "金额与冻结交付包不一致"
        elif row["accounts"] and set(row["accounts"]) != set(expected["accounts"]):
            # 严格集合相等：冻结凭证有两个科目时，回执少给、多给、或夹带错科目都算差异，
            # 只要有一科目对不上就不能进入“已核实过账”。
            status, reason = "account_mismatch", reason or "科目与冻结交付包不一致"
        elif status in {"missing", "extra", "amount_mismatch", "account_mismatch"}:
            reason = reason or "文件声明存在差异"
        if status == "accepted" and (row["actual_cents"] is None or not row["accounts"]):
            reason = reason or "客户未提供金额或科目，只能确认接收"
        if status == "accepted" and not row["external_code"]:
            reason = reason or "对方未提供凭证号，只能确认接收"
        row.update(status=status, reason=reason)
        rows.append(row)

    for code, expected in exported_by_code.items():
        if code not in seen:
            rows.append(
                {
                    "voucher_code": code,
                    "external_code": "",
                    "status": "missing",
                    "reason": "返回文件未包含本次导出的凭证",
                    "posted_date": None,
                    "actual_cents": None,
                    "accounts": [],
                }
            )

    counts = {state: sum(row["status"] == state for row in rows) for state in RESULT_STATES}
    received = all(row["status"] == "accepted" for row in rows)
    # 逐笔“核实过账”必须同时具备对方凭证号、对方过账日期、金额与科目；
    # 缺任一项只代表对方已接收，绝不返回 posted = True。
    complete = all(
        bool(row["external_code"])
        and bool(row["posted_date"])
        and row["actual_cents"] is not None
        and bool(row["accounts"])
        for row in rows
    )
    posted = received and complete
    if received:
        status = "accepted"
    elif counts["rejected"] or counts["amount_mismatch"] or counts["account_mismatch"]:
        status = "partial"
    elif counts["missing"] or counts["extra"]:
        status = "partial"
    else:
        status = "partial"
    return {
        "contract": "roundtrip-return-v1",
        "batch_code": batch_code,
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "status": status,
        "counts": counts,
        "received": received,
        "posted": posted,
        "rows": sorted(rows, key=lambda row: (row["voucher_code"] != "", row["voucher_code"])),
    }
