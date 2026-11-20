"""PLT02-E: read-only reference report reconciliation.

The reference CSV is supplied by the client.  It never changes the system
report; the comparison is a projection and explanations are append-only
evidence attached to the report/reference hash.
"""

from __future__ import annotations

import csv
import hashlib
import io
import unicodedata
from decimal import Decimal, InvalidOperation

from ..accounting.domain import AccountingError

MAX_REFERENCE_BYTES = 512_000
MAX_ABS_CENTS = 10**15
EXPLANATION_TYPES = {
    "rounding": "舍入",
    "reclassification": "重分类",
    "basis": "口径不同",
    "unknown": "待查",
}

# Reference names are deliberately explicit.  We do not guess a line from a
# customer's own wording; unknown names are a 422 with the CSV row number.
CANONICAL_LINES = (
    ("资产负债表", "资产合计", "asset", "balance_sheet", "assets", "资产合计"),
    ("资产负债表", "负债合计", "liability", "balance_sheet", "liabilities", "负债合计"),
    (
        "资产负债表",
        "所有者权益合计",
        "equity",
        "balance_sheet",
        "equity",
        "所有者权益合计",
    ),
    ("利润表", "营业收入", "income", "profit_statement", "revenue_cents", "营业收入"),
    ("利润表", "成本费用", "expense", "profit_statement", "expense_cents", "成本费用"),
    ("利润表", "利润", "profit", "profit_statement", "profit_cents", "利润"),
    ("现金流量表", "期初现金", "cash", "cash_flow", "opening_cash_cents", "期初现金"),
    ("现金流量表", "期末现金", "cash", "cash_flow", "closing_cash_cents", "期末现金"),
    ("现金流量表", "净变动", "cash", "cash_flow", "net_change_cents", "净变动"),
    ("现金流量表", "经营活动", "cash", "cash_flow", "operating_cents", "经营活动"),
    ("现金流量表", "投资活动", "cash", "cash_flow", "investing_cents", "投资活动"),
    ("现金流量表", "筹资活动", "cash", "cash_flow", "financing_cents", "筹资活动"),
)

ALIASES = {
    ("资产负债表", "资产总计"): "资产合计",
    ("资产负债表", "负债总计"): "负债合计",
    ("资产负债表", "所有者权益总计"): "所有者权益合计",
    ("利润表", "收入合计"): "营业收入",
    ("利润表", "费用合计"): "成本费用",
    ("利润表", "利润合计"): "利润",
    ("现金流量表", "现金净变动"): "净变动",
}

SECTION_ALIASES = {
    "资产负债表": "资产负债表",
    "利润表": "利润表",
    "现金流量表": "现金流量表",
    "balance_sheet": "资产负债表",
    "profit_statement": "利润表",
    "cash_flow": "现金流量表",
}

_HEADER_ALIASES = {
    "报表": "section",
    "报表名称": "section",
    "行次": "line_no",
    "行号": "line_no",
    "行名": "line_name",
    "项目": "line_name",
    "金额": "amount",
    "本期金额": "amount",
}
_CANONICAL = {
    (section, name): (kind, group, value_key, label)
    for section, name, kind, group, value_key, label in CANONICAL_LINES
}


def _text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _cents(value: str, row_number: int) -> int:
    text = _text(value).replace(",", "").replace("￥", "").replace("¥", "")
    if not text:
        raise AccountingError(f"参考报表第 {row_number} 行金额为空", 422)
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise AccountingError(f"参考报表第 {row_number} 行金额格式无效", 422) from exc
    if amount != amount.quantize(Decimal("0.01")):
        raise AccountingError(f"参考报表第 {row_number} 行金额最多两位小数", 422)
    cents = int(amount * 100)
    if abs(cents) > MAX_ABS_CENTS:
        raise AccountingError(f"参考报表第 {row_number} 行金额超出范围", 422)
    return cents


def parse_reference(csv_text: str) -> dict:
    raw = csv_text.encode("utf-8")
    if len(raw) > MAX_REFERENCE_BYTES:
        raise AccountingError("参考报表 CSV 超过 512KB 限制", 422)
    try:
        text = csv_text.lstrip("\ufeff")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
    except csv.Error as exc:
        raise AccountingError("参考报表 CSV 无法解析", 422) from exc
    if not rows:
        raise AccountingError("参考报表为空", 422)
    header = [_text(cell) for cell in rows[0]]
    mapped = []
    for cell in header:
        key = _HEADER_ALIASES.get(cell)
        if not key:
            raise AccountingError(f"参考报表 CSV 含未知表头：{cell or '空'}", 422)
        mapped.append(key)
    if sorted(mapped) != ["amount", "line_name", "line_no", "section"]:
        raise AccountingError("参考报表 CSV 表头必须为：报表,行次,行名,金额", 422)
    index = {key: i for i, key in enumerate(mapped)}
    seen = set()
    parsed = []
    for line_number, cells in enumerate(rows[1:], start=2):
        if not any(_text(cell) for cell in cells):
            continue
        if len(cells) < 4:
            raise AccountingError(f"参考报表第 {line_number} 行少于 4 列", 422)
        section = SECTION_ALIASES.get(_text(cells[index["section"]]))
        if not section:
            raise AccountingError(f"参考报表第 {line_number} 行报表名称无效", 422)
        name = _text(cells[index["line_name"]])
        name = ALIASES.get((section, name), name)
        canonical = _CANONICAL.get((section, name))
        if not canonical:
            raise AccountingError(
                f"参考报表第 {line_number} 行无法识别：{section}/{name}；请按文档使用标准行名",
                422,
            )
        key = f"{canonical[1]}.{canonical[2]}"
        if key in seen:
            raise AccountingError(f"参考报表第 {line_number} 行重复：{section}/{name}", 422)
        seen.add(key)
        line_no = _text(cells[index["line_no"]])
        if not line_no:
            raise AccountingError(f"参考报表第 {line_number} 行次为空", 422)
        parsed.append(
            {
                "section": section,
                "line_no": line_no,
                "line_name": name,
                "key": key,
                "amount_cents": _cents(cells[index["amount"]], line_number),
                "_canonical": canonical,
            }
        )
    if not parsed:
        raise AccountingError("参考报表没有数据行", 422)
    return {"rows": parsed, "sha256": hashlib.sha256(raw).hexdigest()}


def _system_value(content: dict, section: str, value_key: str):
    group = content.get(section)
    if not isinstance(group, dict):
        return None
    value = group.get(value_key)
    return int(value) if isinstance(value, int) else None


def _profit_item_section(row: dict) -> str | None:
    section = row.get("section")
    if section in {"income", "expense"}:
        return section
    code = str(row.get("code", ""))
    if code.startswith(("6001", "6051", "6111", "6301")):
        return "income"
    if code.startswith(("6401", "6402", "6403", "6601", "6602", "6603", "6711", "6801")):
        return "expense"
    return None


def _drilldown(content: dict, section: str, kind: str) -> list[dict]:
    if section == "balance_sheet":
        items = content.get("balance_sheet", {}).get("items", [])
        allowed = {"asset"} if kind == "asset" else {kind}
        return [
            {"code": row["code"], "name": row["name"], "amount_cents": int(row["amount_cents"])}
            for row in items
            if row.get("section") in allowed
        ]
    if section == "profit_statement":
        statement = content.get("profit_statement", {})
        items = statement.get("items", [])
        if kind in {"income", "expense"}:
            matched = [
                {
                    "code": row["code"],
                    "name": row["name"],
                    "amount_cents": int(row["amount_cents"]),
                }
                for row in items
                if _profit_item_section(row) == kind
            ]
            if matched:
                return matched
            key, name = (
                ("revenue_cents", "营业收入") if kind == "income" else ("expense_cents", "成本费用")
            )
            return [{"code": key, "name": name, "amount_cents": int(statement.get(key) or 0)}]
        if items:
            return [
                {"code": row["code"], "name": row["name"], "amount_cents": int(row["amount_cents"])}
                for row in items
            ]
        return [
            {"code": key, "name": name, "amount_cents": int(statement.get(key) or 0)}
            for key, name in (
                ("revenue_cents", "营业收入"),
                ("expense_cents", "成本费用"),
                ("profit_cents", "利润"),
            )
        ]
    if section != "cash_flow":
        return []
    cash = content.get("cash_flow", {})
    labels = {
        "opening_cash_cents": "期初现金",
        "closing_cash_cents": "期末现金",
        "net_change_cents": "现金净变动",
        "operating_cents": "经营活动",
        "investing_cents": "投资活动",
        "financing_cents": "筹资活动",
    }
    if section == "cash_flow" and "items" in cash and isinstance(cash["items"], list):
        return [
            {
                "code": str(row.get("code", "")),
                "name": str(row.get("name", "")),
                "amount_cents": int(row["amount_cents"]),
            }
            for row in cash["items"]
        ]
    return [
        {"code": key, "name": label, "amount_cents": int(cash.get(key, 0))}
        for key, label in labels.items()
        if key in cash
    ]


def compare(csv_text: str, content: dict) -> dict:
    reference = parse_reference(csv_text)
    reference_by_key = {row["key"]: row for row in reference["rows"]}
    system_by_key = {}
    for section, name, kind, group, value_key, label in CANONICAL_LINES:
        value = _system_value(content, group, value_key)
        if value is None:
            continue
        system_by_key[f"{group}.{value_key}"] = {
            "section": group,
            "line_no": len(system_by_key) + 1,
            "line_name": label,
            "key": f"{group}.{value_key}",
            "amount_cents": value,
            "kind": kind,
        }
    rows = []
    keys = list(dict.fromkeys(list(system_by_key) + list(reference_by_key)))
    for key in keys:
        system = system_by_key.get(key)
        ref = reference_by_key.get(key)
        if system and ref:
            diff = ref["amount_cents"] - system["amount_cents"]
            status = "matched" if diff == 0 else "amount_different"
            rows.append(
                {
                    "line_key": key,
                    "section": system["section"],
                    "line_no": system["line_no"],
                    "line_name": system["line_name"],
                    "reference_line_no": ref["line_no"],
                    "reference_name": ref["line_name"],
                    "reference_cents": ref["amount_cents"],
                    "system_cents": system["amount_cents"],
                    "difference_cents": diff,
                    "status": status,
                    "drilldown": _drilldown(content, system["section"], system["kind"])
                    if status == "amount_different"
                    else [],
                }
            )
        elif system:
            rows.append(
                {
                    "line_key": key,
                    "section": system["section"],
                    "line_no": system["line_no"],
                    "line_name": system["line_name"],
                    "reference_line_no": None,
                    "reference_name": None,
                    "reference_cents": None,
                    "system_cents": system["amount_cents"],
                    "difference_cents": None,
                    "status": "missing_reference",
                    "drilldown": [],
                }
            )
        elif ref:
            rows.append(
                {
                    "line_key": key,
                    "section": ref["section"],
                    "line_no": None,
                    "line_name": ref["_canonical"][3],
                    "reference_line_no": ref["line_no"],
                    "reference_name": ref["line_name"],
                    "reference_cents": ref["amount_cents"],
                    "system_cents": None,
                    "difference_cents": None,
                    "status": "missing_system",
                    "drilldown": [],
                }
            )
    rows.sort(
        key=lambda row: (row["section"], row["line_no"] or 10**6, row["reference_line_no"] or 10**6)
    )
    counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in (
            "matched",
            "amount_different",
            "missing_reference",
            "missing_system",
        )
    }
    return {
        "reference_sha256": reference["sha256"],
        "reference_rows": len(reference["rows"]),
        "system_rows": len(system_by_key),
        "counts": counts,
        "rows": rows,
    }


def canonical_line(line_key: str) -> dict:
    for section, name, kind, group, value_key, label in CANONICAL_LINES:
        if f"{group}.{value_key}" == line_key:
            return {"section": section, "line_key": line_key, "line_name": name}
    raise AccountingError("差异行不存在于标准报表行", 422)
