"""B08 independent, read-only dual-track comparison.

This tool intentionally does not import the accounting engine. Expected values
must come from a frozen legacy export plus accountant-confirmed normalization;
it never derives the answer from the candidate implementation.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any


class InputError(ValueError):
    pass


def _load(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InputError(f"无法读取或解析核对数据集：{path}") from error
    if not isinstance(value, dict):
        raise InputError("核对数据集必须是 JSON 对象")
    return value


def _required(value: dict[str, Any], name: str):
    if name not in value or value[name] in (None, ""):
        raise InputError(f"核对数据集缺少 {name}")
    return value[name]


def _aux(value) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise InputError("辅助核算必须是对象")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _scope(dataset, kind):
    company = _required(dataset, "company_id")
    ledger = _required(dataset, "ledger_id")
    version = _required(dataset, "data_version")
    periods = _required(dataset, "periods")
    if (
        not isinstance(periods, list)
        or not periods
        or any(not isinstance(item, str) for item in periods)
    ):
        raise InputError(f"{kind} periods 必须是非空字符串数组")
    return company, ledger, version, sorted(set(periods))


def _integer(value, field, kind):
    if type(value) is not int:
        raise InputError(f"{kind} 字段 {field} 必须是整数分")
    return value


def _check_scope(record, company, ledger, kind, ref):
    if record.get("company_id", company) != company or record.get("ledger_id", ledger) != ledger:
        return f"{kind} {ref} 跨企业或跨账套"
    return None


def _documents(dataset, company, ledger, periods, issues):
    docs = dataset.get("documents", [])
    if not isinstance(docs, list):
        raise InputError("documents 必须是数组")
    result = {}
    for index, row in enumerate(docs):
        if not isinstance(row, dict):
            raise InputError("documents 行必须是对象")
        key = row.get("match_key")
        if not isinstance(key, str) or not key.strip():
            raise InputError("每笔凭证必须有稳定 match_key")
        ref = f"documents[{index}]/{key}"
        cross = _check_scope(row, company, ledger, "凭证", key)
        if cross:
            issues.append({"code": "cross_scope", "ref": ref, "message": cross})
        if row.get("period") not in periods:
            issues.append(
                {"code": "period_out_of_scope", "ref": ref, "message": "凭证期间不在冻结范围内"}
            )
        if key in result:
            issues.append(
                {"code": "duplicate_document", "ref": ref, "message": "同一 match_key 重复"}
            )
            continue
        debit = _integer(row.get("debit_cents", 0), "debit_cents", "凭证")
        credit = _integer(row.get("credit_cents", 0), "credit_cents", "凭证")
        if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
            issues.append(
                {"code": "line_amount", "ref": ref, "message": "每条分录必须且只能有一侧非零金额"}
            )
        result[key] = {
            "period": row.get("period"),
            "date": row.get("date"),
            "account_code": row.get("account_code"),
            "aux": _aux(row.get("aux")),
            "debit_cents": debit,
            "credit_cents": credit,
            "kind": row.get("kind", "business"),
        }
    return result


def _balances(dataset, company, ledger, issues):
    rows = dataset.get("balances", [])
    if not isinstance(rows, list):
        raise InputError("balances 必须是数组")
    result = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("balances 行必须是对象")
        period, account = row.get("period"), row.get("account_code")
        if not isinstance(period, str) or not isinstance(account, str):
            raise InputError("余额必须有 period/account_code")
        key = (period, account, _aux(row.get("aux")))
        ref = f"balances[{index}]/{period}/{account}"
        cross = _check_scope(row, company, ledger, "余额", ref)
        if cross:
            issues.append({"code": "cross_scope", "ref": ref, "message": cross})
        if key in result:
            issues.append(
                {"code": "duplicate_balance", "ref": ref, "message": "同一期间/科目/辅助重复"}
            )
            continue
        result[key] = {
            field: _integer(row.get(field, 0), field, "余额")
            for field in ("opening_cents", "debit_cents", "credit_cents", "closing_cents")
        }
    return result


def _reports(dataset, company, ledger, data_version, issues):
    rows = dataset.get("reports", [])
    if not isinstance(rows, list):
        raise InputError("reports 必须是数组")
    result = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("reports 行必须是对象")
        key = (row.get("period"), row.get("report_id"), row.get("line"))
        if any(not isinstance(item, str) for item in key):
            raise InputError("报表必须有 period/report_id/line")
        ref = f"reports[{index}]/{key[0]}/{key[1]}/{key[2]}"
        cross = _check_scope(row, company, ledger, "报表", ref)
        if cross:
            issues.append({"code": "cross_scope", "ref": ref, "message": cross})
        if key in result:
            issues.append({"code": "duplicate_report", "ref": ref, "message": "同一报表行重复"})
            continue
        result[key] = {
            "amount_cents": _integer(row.get("amount_cents", 0), "amount_cents", "报表"),
            "source_version": row.get("source_version"),
            "status": row.get("status", "draft"),
        }
        if row.get("status") == "published" and row.get("source_version") != data_version:
            issues.append(
                {
                    "code": "stale_report",
                    "ref": ref,
                    "message": f"发布报表来源版本 {row.get('source_version')} 不是当前 {data_version}",
                }
            )
    return result


def _receipts(dataset, company, ledger, issues):
    rows = dataset.get("receipts", [])
    if not isinstance(rows, list):
        raise InputError("receipts 必须是数组")
    result = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("receipts 行必须是对象")
        key = (row.get("period"), row.get("receipt_id"))
        if any(not isinstance(item, str) for item in key):
            raise InputError("回执必须有 period/receipt_id")
        ref = f"receipts[{index}]/{key[0]}/{key[1]}"
        cross = _check_scope(row, company, ledger, "回执", ref)
        if cross:
            issues.append({"code": "cross_scope", "ref": ref, "message": cross})
        result[key] = {
            "status": row.get("status"),
            "source": row.get("source"),
            "external_id": row.get("external_id"),
        }
        if row.get("status") == "vendor_confirmed" and row.get("source") not in {
            "vendor_api",
            "vendor_query",
        }:
            issues.append(
                {
                    "code": "manual_receipt_as_vendor",
                    "ref": ref,
                    "message": "人工上传/人工回执不能显示为厂商独立确认",
                }
            )
    return result


def _compare_map(expected, actual, kind, fields, issues):
    for key in sorted(set(expected) - set(actual)):
        issues.append({"code": "missing", "ref": f"{kind}:{key}", "message": f"缺少{kind}"})
    for key in sorted(set(actual) - set(expected)):
        issues.append({"code": "extra", "ref": f"{kind}:{key}", "message": f"多出{kind}"})
    for key in sorted(set(expected) & set(actual)):
        for field in fields:
            if expected[key][field] != actual[key][field]:
                issues.append(
                    {
                        "code": "value_mismatch",
                        "ref": f"{kind}:{key}/{field}",
                        "message": f"{kind} {field} 不一致",
                        "expected": expected[key][field],
                        "actual": actual[key][field],
                    }
                )


def compare(baseline: dict[str, Any], candidate: dict[str, Any]):
    issues: list[dict[str, Any]] = []
    b_company, b_ledger, b_version, b_periods = _scope(baseline, "baseline")
    c_company, c_ledger, c_version, c_periods = _scope(candidate, "candidate")
    if (b_company, b_ledger) != (c_company, c_ledger):
        issues.append(
            {"code": "scope_mismatch", "ref": "dataset", "message": "两家企业/账套不能互相比较"}
        )
    if b_periods != c_periods:
        issues.append({"code": "period_mismatch", "ref": "dataset", "message": "期间范围不一致"})
    if b_version != c_version:
        issues.append(
            {"code": "version_mismatch", "ref": "dataset", "message": "数据版本不一致，旧验收失效"}
        )

    b_docs = _documents(baseline, b_company, b_ledger, b_periods, issues)
    c_docs = _documents(candidate, c_company, c_ledger, c_periods, issues)
    _compare_map(
        b_docs,
        c_docs,
        "凭证",
        ("period", "date", "account_code", "aux", "debit_cents", "credit_cents", "kind"),
        issues,
    )
    b_bal = _balances(baseline, b_company, b_ledger, issues)
    c_bal = _balances(candidate, c_company, c_ledger, issues)
    _compare_map(
        b_bal,
        c_bal,
        "余额",
        ("opening_cents", "debit_cents", "credit_cents", "closing_cents"),
        issues,
    )
    b_reports = _reports(baseline, b_company, b_ledger, b_version, issues)
    c_reports = _reports(candidate, c_company, c_ledger, c_version, issues)
    _compare_map(b_reports, c_reports, "报表", ("amount_cents", "source_version", "status"), issues)
    b_receipts = _receipts(baseline, b_company, b_ledger, issues)
    c_receipts = _receipts(candidate, c_company, c_ledger, issues)
    _compare_map(b_receipts, c_receipts, "回执", ("status", "source", "external_id"), issues)

    # Opening must not also be posted as a normal/closing voucher.
    for key, row in c_docs.items():
        if row["kind"] == "opening" and row["debit_cents"] + row["credit_cents"] > 0:
            issues.append(
                {
                    "code": "opening_duplicate",
                    "ref": f"凭证:{key}",
                    "message": "期初被再次伪造成普通凭证，存在重复计入风险",
                }
            )

    # Continuous periods: prior closing must equal next opening for every account/aux.
    def continuity(rows, label):
        grouped = defaultdict(list)
        for (period, account, aux), value in rows.items():
            grouped[(account, aux)].append((period, value))
        for (account, aux), values in grouped.items():
            values.sort()
            for (previous_period, previous), (next_period, nxt) in zip(values, values[1:]):
                if previous["closing_cents"] != nxt["opening_cents"]:
                    issues.append(
                        {
                            "code": "period_transition",
                            "ref": f"{label}:{account}:{aux}:{previous_period}->{next_period}",
                            "message": "上期期末与下期期初不衔接",
                            "expected": previous["closing_cents"],
                            "actual": nxt["opening_cents"],
                        }
                    )

    continuity(b_bal, "baseline")
    continuity(c_bal, "candidate")
    return {
        "status": "blocked" if issues else "pass",
        "scope": {"company_id": b_company, "ledger_id": b_ledger, "periods": b_periods},
        "versions": {"baseline": b_version, "candidate": c_version},
        "counts": {
            "documents": len(c_docs),
            "balances": len(c_bal),
            "reports": len(c_reports),
            "receipts": len(c_receipts),
            "issues": len(issues),
        },
        "issues": issues,
    }


def markdown(result):
    lines = [
        "# B08 双轨核对结果",
        "",
        f"- 状态：**{result['status']}**",
        f"- 企业：{result['scope']['company_id']}",
        f"- 账套：{result['scope']['ledger_id']}",
        f"- 期间：{', '.join(result['scope']['periods'])}",
        f"- 基线版本：{result['versions']['baseline']}",
        f"- 候选版本：{result['versions']['candidate']}",
        "",
        "## 差异",
        "",
    ]
    if not result["issues"]:
        lines.append("无差异。")
    else:
        for item in result["issues"]:
            lines.append(f"- `{item['code']}` {item['ref']}：{item['message']}")
    lines.extend(["", "> 本报告只做只读核对，不构成会计签收或正式切换批准。"])
    return "\n".join(lines) + "\n"


def _write(path, text, force):
    target = Path(path)
    if target.exists() and not force:
        raise InputError(f"输出文件已存在，拒绝覆盖：{path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="B08 独立双轨核对（只读，不修改账本）")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--json-out")
    parser.add_argument("--markdown-out")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = compare(_load(args.baseline), _load(args.candidate))
        if args.json_out:
            _write(args.json_out, json.dumps(result, ensure_ascii=False, indent=2), args.force)
        if args.markdown_out:
            _write(args.markdown_out, markdown(result), args.force)
    except InputError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
