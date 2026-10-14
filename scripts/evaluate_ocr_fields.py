"""Offline, read-only OCR comparison; never calls models or changes approval state.

Each JSONL label/result represents ONE invoice; split multi-invoice attachments
into explicitly paired samples before comparison. Real labels remain private.
"""

import argparse
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import statistics
import unicodedata


FIELDS = (
    "invoice_number",
    "issued_on",
    "seller_tax_id",
    "buyer_tax_id",
    "gross_amount",
    "net_amount",
    "tax_amount",
    "seller_name",
    "buyer_name",
    "currency",
)
MAX_BYTES = 20 * 1024 * 1024
MAX_SAMPLES = 5000


def read_rows(path):
    if Path(path).stat().st_size > MAX_BYTES:
        raise ValueError("单个评测文件不能超过 20 MiB")
    with Path(path).open(encoding="utf-8-sig") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return rows


def indexed(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SAMPLES:
        raise ValueError("评测文件须包含 1–5000 个样本")
    result = {}
    for row in rows:
        key = row.get("sample_id") if isinstance(row, dict) else None
        if not isinstance(key, str) or not key.strip() or len(key) > 120 or key in result:
            raise ValueError("样本 ID 缺失、过长或重复")
        result[key] = row
    return result


def canonical(field, value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("字段须为文本或 null")
    text = unicodedata.normalize("NFKC", value).strip()
    if field.endswith("_amount"):
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?", text):
            raise ValueError("金额须为非负十进制字符串，最多两位小数")
        return Decimal(text)
    return text


def nonnegative_number(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def evaluate(labels, predictions):
    truth, actual = indexed(labels), indexed(predictions) if predictions else {}
    if set(actual) - set(truth):
        raise ValueError("候选结果包含不属于固定样本集的 ID")
    fields = {
        field: {"expected": 0, "correct": 0, "incorrect": 0, "missing": 0} for field in FIELDS
    }
    complete = exact = unflagged_errors = error_samples = flagged = 0
    durations, corrections, costs = [], [], []
    unknown_cost = missing_results = 0
    categories = {}
    for key, label in truth.items():
        expected = label.get("fields")
        if not isinstance(expected, dict) or not expected or set(expected) - set(FIELDS):
            raise ValueError("标签必须包含受支持的非空字段集合")
        category = label.get("category", "unspecified")
        if not isinstance(category, str) or not category or len(category) > 80:
            raise ValueError("样本类别无效")
        category_stats = categories.setdefault(category, {"samples": 0, "exact": 0})
        category_stats["samples"] += 1
        row = actual.get(key)
        values = {}
        if row is None:
            missing_results += 1
        else:
            if (
                row.get("status") not in {"succeeded", "failed"}
                or type(row.get("review_required")) is not bool
            ):
                raise ValueError("候选必须明确记录成功/失败状态与是否需要人工处理")
            if row["status"] == "failed" and not row["review_required"]:
                raise ValueError("失败样本必须明确需要人工处理")
            flagged += int(row["review_required"])
            if row["status"] == "succeeded":
                values = row.get("fields")
                if not isinstance(values, dict) or set(values) - set(FIELDS):
                    raise ValueError("候选字段无效")
                complete += 1
            for name, bucket in (("duration_ms", durations), ("correction_seconds", corrections)):
                value = row.get(name)
                if value is not None:
                    if not nonnegative_number(value):
                        raise ValueError("耗时须为有限非负数字")
                    bucket.append(value)
            price = row.get("cost_cny")
            if price is not None:
                if not isinstance(price, str) or not re.fullmatch(
                    r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", price
                ):
                    raise ValueError("费用须为最多六位小数的非负字符串或 null")
                costs.append(Decimal(price))
        if row is None or row.get("cost_cny") is None:
            unknown_cost += 1
        correct = True
        for field, value in expected.items():
            expected_value = canonical(field, value)
            counter = fields[field]
            counter["expected"] += 1
            if field not in values:
                counter["missing"] += 1
                correct = False
                continue
            try:
                matched = canonical(field, values[field]) == expected_value
            except (ValueError, InvalidOperation):
                matched = False
            counter["correct" if matched else "incorrect"] += 1
            correct = correct and matched
        if correct:
            exact += 1
            category_stats["exact"] += 1
        else:
            error_samples += 1
            if row and row["status"] == "succeeded" and not row["review_required"]:
                unflagged_errors += 1
    for value in fields.values():
        value["accuracy"] = value["correct"] / value["expected"] if value["expected"] else None
    durations.sort()
    return {
        "schema_version": "ocr-field-evaluation-v1",
        "samples": len(truth),
        "completed": complete,
        "missing_results": missing_results,
        "all_labeled_fields_exact": exact,
        "sample_exact_rate": exact / len(truth),
        "fields": fields,
        "categories": categories,
        "manual_review_flagged": flagged,
        "error_samples": error_samples,
        "unflagged_error_samples": unflagged_errors,
        "unflagged_error_rate_among_errors": unflagged_errors / error_samples
        if error_samples
        else None,
        "duration_ms_p95": durations[math.ceil(len(durations) * 0.95) - 1] if durations else None,
        "duration_samples": len(durations),
        "correction_seconds_median": statistics.median(corrections) if corrections else None,
        "correction_time_samples": len(corrections),
        "reported_cost_cny": str(sum(costs, Decimal("0"))) if costs else None,
        "known_cost_samples": len(costs),
        "unknown_cost_samples": unknown_cost,
        "scope_note": "离线比较已配对的单张票据标签与结果，不调用模型，不证明标签已独立确认，不授权自动审核或发布。费用缺失不是零；未告警错误指未标记人工处理，不代表已经导出。",
    }


def main():
    parser = argparse.ArgumentParser(
        description="只读比较固定票据样本的字段、人工处理标记、耗时和已知费用"
    )
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path, action="append")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        labels = read_rows(args.labels)
        reports = [evaluate(labels, read_rows(path)) for path in args.results]
        # Refuse accidental replacement of labels, predictions, or an earlier report.
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(
                {"candidates": reports}, handle, ensure_ascii=False, indent=2, allow_nan=False
            )
            handle.write("\n")
    except (ValueError, OSError, InvalidOperation):
        parser.exit(2, "评测失败：检查文件、固定样本 ID、字段格式及输出文件是否已经存在。\n")
    print(f"已比较 {len(reports)} 组结果；报告仅包含汇总，不包含票据文字、税号或单据号码。")


if __name__ == "__main__":
    main()
