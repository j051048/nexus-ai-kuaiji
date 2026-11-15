"""Synthetic 54-column T+ adapter.

The customer template is not available. This profile is deliberately labelled
synthetic and must not be described as a native T+ format. It freezes a
deterministic, reviewable projection of an already frozen delivery package so
that mapping, hashes, row counts and receipts can be tested end to end.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation

from app.accounting.domain import AccountingError

PROFILE = "hengzhang.tplus.synthetic54.v1"
EXTERNAL_VOUCHER_WORD = "记"
INTERNAL_VOUCHER_WORD = "转"
LINES_PER_ROW = 6
BASE_COLUMNS = [
    "导出协议",
    "账套标识",
    "会计期间",
    "来源导出编号",
    "凭证组序号",
    "外部凭证编号",
    "外部凭证字",
    "内部凭证字",
    "凭证日期",
    "摘要",
    "复核状态",
    "来源包指纹",
]
LINE_COLUMNS = ["科目编码", "科目名称", "借方金额", "贷方金额", "辅助核算类型", "辅助核算编码", "行摘要"]
COLUMNS = BASE_COLUMNS + [
    f"分录{index}-{name}" for index in range(1, LINES_PER_ROW + 1) for name in LINE_COLUMNS
]
assert len(COLUMNS) == 54

RESULT_COLUMNS = ["行号", "外部凭证编号", "结果", "原因"]
RESULT_STATES = {"accepted", "rejected", "duplicate", "unknown"}
FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _text(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if text.startswith(FORMULA_PREFIX):
        return "'" + text
    return text


def _amount(value) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "0.00"
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise AccountingError("冻结凭证金额格式损坏，不能生成适配文件", 409) from error
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise AccountingError("冻结凭证金额格式损坏，不能生成适配文件", 409)
    return f"{amount:.2f}"


def _cents(value) -> int:
    text = "" if value is None else str(value)
    try:
        amount = Decimal(text or "0")
    except InvalidOperation as error:
        raise AccountingError("冻结凭证金额格式损坏，不能生成适配文件", 409) from error
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise AccountingError("冻结凭证金额超过两位小数，不能生成适配文件", 409)
    return int(amount * 100)


def _package_vouchers(package: dict) -> list[dict]:
    vouchers = package.get("vouchers")
    if not isinstance(vouchers, list) or not vouchers:
        raise AccountingError("冻结交付包没有可导出的凭证", 409)
    if package.get("schema") != "hengzhang.tplus.delivery.v1":
        raise AccountingError("冻结交付包协议不匹配，拒绝生成", 409)
    return vouchers


def build_rows(package: dict) -> tuple[list[list[str]], list[str], int]:
    voucher_codes: list[str] = []
    rows: list[list[str]] = []
    for voucher in _package_vouchers(package):
        lines = voucher.get("lines")
        if not isinstance(lines, list) or not lines:
            raise AccountingError("冻结凭证没有分录，拒绝生成", 409)
        debit = sum(_cents(line.get("debit")) for line in lines)
        credit = sum(_cents(line.get("credit")) for line in lines)
        if debit != credit:
            raise AccountingError("冻结凭证借贷不平衡，拒绝生成", 409)
        code = _text(voucher.get("external_code"))
        if not code:
            raise AccountingError("冻结凭证缺少外部编号，拒绝生成", 409)
        external_code = f"T54-{code}"
        if external_code in voucher_codes:
            raise AccountingError("冻结凭证外部编号重复，拒绝生成", 409)
        voucher_codes.append(external_code)
        for offset in range(0, len(lines), LINES_PER_ROW):
            group = lines[offset : offset + LINES_PER_ROW]
            group_number = offset // LINES_PER_ROW + 1
            row = [
                PROFILE,
                _text(package.get("external_book_id")),
                _text(package.get("period")),
                _text(package.get("export_id")),
                str(group_number),
                external_code,
                EXTERNAL_VOUCHER_WORD,
                INTERNAL_VOUCHER_WORD,
                _text(voucher.get("date")),
                _text(voucher.get("description")),
                "已复核冻结",
                _text(package.get("manifest_fingerprint")),
            ]
            for line in group:
                auxiliary = line.get("auxiliary_values") or {}
                kinds = ";".join(sorted(_text(kind) for kind in auxiliary))
                codes = ";".join(_text(auxiliary[kind]) for kind in sorted(auxiliary))
                row.extend(
                    [
                        _text(line.get("account_code")),
                        _text(line.get("account_name")),
                        _amount(line.get("debit")),
                        _amount(line.get("credit")),
                        kinds,
                        codes,
                        _text(voucher.get("description")),
                    ]
                )
            while len(row) < len(COLUMNS):
                row.extend([""] * len(LINE_COLUMNS))
            if len(row) != len(COLUMNS):
                raise AccountingError("适配列数不是 54 列，拒绝生成", 409)
            rows.append(row)
    if not rows:
        raise AccountingError("冻结交付包没有可导出的分录", 409)
    return rows, voucher_codes, len(voucher_codes)


def render(package: dict) -> tuple[bytes, list[str], int]:
    rows, codes, voucher_count = build_rows(package)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    content = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    return content, codes, voucher_count


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_result(content: bytes, codes: list[str]) -> list[dict]:
    if len(content) > 2_000_000:
        raise AccountingError("T+ 回执文件最多 2 MB", 413)
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), strict=True)
        if reader.fieldnames != RESULT_COLUMNS:
            raise ValueError("header")
        rows = []
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError("shape")
            number = raw["行号"].strip()
            if not number.isdigit() or int(number) != len(rows) + 1:
                raise ValueError("row number")
            code = raw["外部凭证编号"].strip()
            status = raw["结果"].strip()
            reason = raw["原因"].strip()
            if status not in RESULT_STATES or not code:
                raise ValueError("status or code")
            if any(
                value.startswith(FORMULA_PREFIX)
                for value in (code, status, reason)
            ):
                raise ValueError("formula")
            if len(content) > 2_000_000:
                raise ValueError("size")
            rows.append(
                {
                    "row_number": int(number),
                    "voucher_external_code": code,
                    "status": status,
                    "reason": reason,
                }
            )
    except (ValueError, UnicodeDecodeError, csv.Error) as error:
        raise AccountingError("T+ 回执格式不正确或包含公式，请使用四列模板", 422) from error
    if not rows or len(rows) > 10_000:
        raise AccountingError("T+ 回执行数必须在 1 到 10000 之间", 422)
    if [row["row_number"] for row in rows] != list(range(1, len(rows) + 1)):
        raise AccountingError("T+ 回执行号必须连续", 422)
    if (len(rows) != len(codes) or len(set(codes)) != len(codes)
            or len({row["voucher_external_code"] for row in rows}) != len(rows)
            or {row["voucher_external_code"] for row in rows} != set(codes)):
        raise AccountingError("T+ 回执必须逐行覆盖本次导出的全部凭证", 422)
    if any(
        row["status"] in {"rejected", "unknown", "duplicate"} and not row["reason"]
        for row in rows
    ):
        raise AccountingError("拒绝、重复或未知回执必须填写原因", 422)
    return rows


def result_summary(rows: list[dict]) -> tuple[str, dict]:
    counts = {state: sum(row["status"] == state for row in rows) for state in RESULT_STATES}
    if counts["accepted"] == len(rows):
        status = "accepted"
    elif counts["rejected"] == len(rows):
        status = "rejected"
    elif counts["unknown"] == len(rows):
        status = "unknown"
    else:
        status = "partial"
    return status, counts


def stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
