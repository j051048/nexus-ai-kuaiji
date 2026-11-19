"""Read-only preflight for a taxi month-end walkthrough sample.

Never writes a database or an object store. It reuses the production import
parser so the client sample is checked against the same size, encoding,
header and date/amount rules as the import wizard.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nexus_backend"))

from app.accounting.domain import AccountingError  # noqa: E402
from app.import_wizard.parser import convert_date, convert_money, parse_file  # noqa: E402

REQUIRED = ("date", "description")
IDENTITY = ("external_id", "stable_transaction_id")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="出租车月度样本导入前只读校验")
    parser.add_argument("files", nargs="+", type=Path, help="CSV/XLSX 样本文件")
    parser.add_argument("--period", required=True, help="会计期间，格式 YYYY-MM")
    parser.add_argument(
        "--date-format",
        choices=("ymd", "dmy", "mdy", "excel_serial"),
        default="ymd",
        help="日期列格式；默认 ymd",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args(argv)


def _period_bounds(period: str) -> tuple[str, str]:
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ValueError("--period 必须是 YYYY-MM")
    year, month = (int(part) for part in period.split("-"))
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def validate_file(path: Path, period: str, date_format: str) -> dict:
    result: dict = {
        "file": path.name,
        "rows": 0,
        "columns": [],
        "total_cents": 0,
        "errors": [],
        "warnings": [],
    }
    start, end = _period_bounds(period)
    try:
        data = path.read_bytes()
        table = parse_file(path.name, data)
        headers = [str(value).strip() for value in table["headers"]]
        rows = table["rows"]
        suggested = table["suggested_columns"]
    except (OSError, AccountingError, ValueError) as error:
        result["errors"].append(str(error))
        return result

    result["columns"] = headers
    result["rows"] = len(rows)
    if not rows:
        result["errors"].append("样本没有流水行")
    if not any(field in suggested for field in IDENTITY):
        result["errors"].append("缺少交易编号或银行原始交易号列")
    if "date" not in suggested:
        result["errors"].append("缺少可识别的日期列")
    if "description" not in suggested:
        result["errors"].append("缺少可识别的摘要/用途列")
    amount_ok = "amount" in suggested or (
        "income" in suggested and "expense" in suggested
    )
    if not amount_ok:
        result["errors"].append("缺少金额列，或收入/支出两列")
    if "amount" in suggested and "direction" not in suggested:
        result["warnings"].append("只有金额列、无方向列；请确认金额带正负号或补充方向")

    if any("date" not in suggested or "description" not in suggested for _ in [0]):
        return result

    total = 0
    for offset, raw in enumerate(rows, start=1):
        try:
            raw_date = raw[suggested["date"]].strip()
            parsed_date = convert_date(raw_date, date_format)
        except (IndexError, ValueError) as error:
            result["errors"].append(f"第 {offset} 行日期无效：{error}")
            continue
        if not (start <= parsed_date < end):
            result["errors"].append(f"第 {offset} 行日期 {parsed_date} 不在期间 {period}")
            continue
        if "amount" in suggested:
            try:
                amount = convert_money(raw[suggested["amount"]], allow_grouping=True, signed=True)
            except (IndexError, ValueError) as error:
                result["errors"].append(f"第 {offset} 行金额无效：{error}")
                continue
            if "direction" in suggested:
                direction = raw[suggested["direction"]].strip()
                if direction in {"支出", "付款", "借方", "出账"}:
                    amount = -abs(amount)
                elif direction in {"收入", "收款", "贷方", "入账"}:
                    amount = abs(amount)
                elif direction:
                    result["errors"].append(f"第 {offset} 行方向无法识别：{direction}")
                    continue
        else:
            try:
                income = convert_money(
                    raw[suggested["income"]] or "0", allow_grouping=True
                )
                expense = convert_money(
                    raw[suggested["expense"]] or "0", allow_grouping=True
                )
                amount = income - expense
            except (IndexError, ValueError) as error:
                result["errors"].append(f"第 {offset} 行金额无效：{error}")
                continue
        total += amount
        if amount == 0:
            result["warnings"].append(f"第 {offset} 行金额为 0")
    result["total_cents"] = total
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    results = [validate_file(path, args.period, args.date_format) for path in args.files]
    failed = any(item["errors"] for item in results)
    payload = {
        "period": args.period,
        "status": "failed" if failed else "passed",
        "files": results,
        "notice": "只读预检，不入库、不替代客户确认或真实导入。",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(
                f"{item['file']}: {len(item['rows'])} 行, "
                f"{len(item['errors'])} 个错误, {len(item['warnings'])} 个提示"
            )
            for message in item["errors"]:
                print(f"  ERROR {message}")
            for message in item["warnings"]:
                print(f"  WARN  {message}")
        print(payload["notice"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
