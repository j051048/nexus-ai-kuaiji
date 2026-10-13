"""Deterministic legacy trial balance; never combines history with cutover balance."""

import hashlib
import json
from collections import Counter, defaultdict

from .normalization import (
    AUX_KINDS,
    KINDS,
    MAX_RECORDS,
    _context,
    _issue,
    canonical_payload,
    signed_cents,
    signed_money,
)


def _total_money(cents):
    # A batch total can exceed a single line's limit, while all inputs remain bounded.
    return ("-" if cents < 0 else "") + f"{abs(cents) // 100}.{abs(cents) % 100:02d}"


def _months(start, end):
    year, month = int(start[:4]), int(start[5:7])
    result = []
    while f"{year:04d}-{month:02d}" <= end[:7]:
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def _identity(record):
    kind, payload = record["kind"], record["payload"]
    if kind == "account":
        return (kind, payload["code"])
    if kind == "auxiliary":
        return (kind, payload["kind"], payload["code"])
    if kind == "balance":
        return (
            kind,
            record["period"],
            payload["account_code"],
            json.dumps(payload["auxiliaries"], sort_keys=True),
        )
    if kind == "open_item":
        return (kind, record["external_id"])
    return (kind, record["period"], record["external_id"])


def deduplicate_records(records):
    """One accounting identity, many retained origins; conflicting content blocks."""
    unique, identical, issues, seen = [], 0, [], {}
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            issues.append(_issue("RECORD_SHAPE_INVALID", "规范化记录须为对象及 payload 对象"))
            continue
        try:
            key = _identity(row)
            value = canonical_payload(row["payload"])
        except (KeyError, TypeError, ValueError):
            issues.append(
                _issue(
                    "RECORD_SHAPE_INVALID",
                    "规范化记录结构无效，不能参加复算",
                    str(row.get("kind", "")),
                    str(row.get("external_id", "")),
                    row.get("row_number", 0),
                )
            )
            continue
        if key in seen:
            previous = seen[key]
            if previous[0] != value:
                issues.append(
                    _issue(
                        "DUPLICATE_CONFLICT",
                        "同一来源键出现不同内容，须确认正确版本；系统不覆盖旧记录",
                        row["kind"],
                        row["external_id"],
                        row.get("row_number", 0),
                    )
                )
            else:
                identical += 1
            continue
        seen[key] = (value, row)
        unique.append(row)
    return unique, identical, issues


def validate_records(records, context, mapping=None):
    """Malformed stored facts fail closed rather than crash into partial acceptance."""
    _context(context)
    try:
        return _validate_records(records, context, mapping)
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, OverflowError):
        issue = _issue(
            "RECORD_SHAPE_INVALID", "记录字段或金额不符合规范化约束，须重新解析原件；本报告不可接管"
        )
        return {
            "summary": {"counts": {}, "totals": {}},
            "issues": [issue],
            "blocking_count": 1,
            "warning_count": 0,
            "checks": [],
            "coverage": {
                "complete": False,
                "open_item_reconciliation_complete": False,
                "history_is_reference_only": True,
            },
            "open_item_bindings": [],
        }


def _validate_records(records, context, mapping=None):
    """Verify normalized historical facts only; current sources/drafts stay separate."""
    _context(context)
    issues, checks = [], []
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        return {
            "summary": {"counts": {}, "totals": {}},
            "issues": [_issue("RECORD_LIMIT", "迁移项目最多 50,000 条记录")],
            "blocking_count": 1,
            "warning_count": 0,
            "checks": [],
            "coverage": {"complete": False},
        }
    unique, deduplicated, duplicate_issues = deduplicate_records(records)
    issues.extend(duplicate_issues)
    if (
        sum(
            len(row["payload"].get("lines", [])) if row["kind"] == "voucher" else 1
            for row in unique
        )
        > MAX_RECORDS
    ):
        issues.append(_issue("RECORD_LIMIT", "迁移项目含凭证分录在内最多 50,000 条原始记录"))
    grouped = defaultdict(list)
    for row in unique:
        if row["kind"] not in KINDS:
            issues.append(
                _issue(
                    "RECORD_KIND_UNSUPPORTED",
                    "未支持的记录类型",
                    row["kind"],
                    row["external_id"],
                    row.get("row_number", 0),
                )
            )
            continue
        if not context["history_start"][:7] <= row.get("period", "") <= context["history_end"][:7]:
            issues.append(
                _issue(
                    "RECORD_PERIOD_OUTSIDE",
                    "记录在历史范围外",
                    row["kind"],
                    row["external_id"],
                    row.get("row_number", 0),
                )
            )
        grouped[row["kind"]].append(row)

    def issue(code, message, row=None, severity="blocking"):
        row = row or {}
        entry = _issue(
            code,
            message,
            row.get("kind", ""),
            row.get("external_id", ""),
            row.get("row_number", 0),
            severity,
        )
        evidence = row.get("payload", {}).get("_evidence", {})
        if evidence.get("file_id"):
            entry["file_id"] = evidence["file_id"]
        issues.append(entry)

    accounts = {row["payload"]["code"]: row for row in grouped["account"]}
    auxiliaries = {
        (row["payload"]["kind"], row["payload"]["code"]): row for row in grouped["auxiliary"]
    }
    children = defaultdict(list)
    if not accounts:
        issue("ACCOUNTS_MISSING", "缺少来源科目表，不能确认旧账完整性")
    for code, row in accounts.items():
        payload = row["payload"]
        parent = payload.get("parent_code")
        if parent:
            if parent not in accounts:
                issue("ACCOUNT_PARENT_MISSING", f"科目 {code} 的上级 {parent} 不在来源科目表", row)
            children[parent].append(code)
        visited, ancestor = {code}, parent
        while ancestor and ancestor in accounts:
            if ancestor in visited:
                issue("ACCOUNT_HIERARCHY_CYCLE", f"科目 {code} 的上级关系形成循环", row)
                break
            visited.add(ancestor)
            ancestor = accounts[ancestor]["payload"].get("parent_code")
    for code, row in accounts.items():
        if children.get(code) and row["payload"]["posting"]:
            issue("PARENT_MARKED_POSTING", f"科目 {code} 有下级科目，不能同时声明可记账末级", row)
        if not children.get(code) and not row["payload"]["posting"]:
            issue("NONPOSTING_LEAF", f"非末级科目 {code} 未提供下级，科目树可能不完整", row)

    if mapping is not None:
        mapping = mapping.model_dump() if hasattr(mapping, "model_dump") else mapping
        account_maps = mapping.get("accounts", []) if isinstance(mapping, dict) else []
        mapped = {entry["source_code"]: entry for entry in account_maps}
        targets = defaultdict(list)
        for entry in account_maps:
            targets[entry["target_code"]].append(entry["source_code"])
            if entry["source_code"] not in accounts:
                issue("MAPPING_SOURCE_UNKNOWN", f"映射来源科目 {entry['source_code']} 不存在")
        for target, sources in targets.items():
            if len(sources) > 1:
                issue(
                    "ACCOUNT_MAPPING_MERGE",
                    f"多个来源科目映射至 {target}，不能静默合并：" + "、".join(sources),
                )
        for code, row in accounts.items():
            if row["payload"]["posting"] and code not in mapped:
                issue("ACCOUNT_MAPPING_MISSING", f"末级科目 {code} 尚未确认目标映射", row)
        auxiliary_maps = {
            (entry["kind"], entry["source_code"]): entry for entry in mapping.get("auxiliaries", [])
        }
        targets_aux = defaultdict(list)
        for entry in mapping.get("auxiliaries", []):
            if entry.get("target_id"):
                targets_aux[(entry["kind"], entry["target_id"])].append(entry["source_code"])
        for (_, target), sources in targets_aux.items():
            if len(sources) > 1:
                issue(
                    "AUXILIARY_MAPPING_MERGE",
                    f"多个辅助档案映射至 {target}，须独立确认合并，不能默认为同一往来方",
                )
        for key, row in auxiliaries.items():
            if key not in auxiliary_maps:
                issue("AUXILIARY_MAPPING_MISSING", f"辅助档案 {key[0]}/{key[1]} 尚未确认映射", row)

    def check_aux(row, account_code, values):
        if not isinstance(values, dict):
            issue("AUXILIARY_INVALID", "辅助核算必须是对象", row)
            return
        required = set(
            accounts.get(account_code, {}).get("payload", {}).get("auxiliary_requirements", [])
        )
        for dimension in required - values.keys():
            issue(
                "AUXILIARY_REQUIRED_MISSING", f"科目 {account_code} 缺少辅助维度 {dimension}", row
            )
        for dimension, identifier in values.items():
            if dimension not in AUX_KINDS or (dimension, identifier) not in auxiliaries:
                issue(
                    "AUXILIARY_REFERENCE_UNKNOWN", f"辅助档案 {dimension}/{identifier} 未提供", row
                )

    movement = defaultdict(lambda: [0, 0])
    posted_by_month = Counter()
    vouchers_by_id = defaultdict(list)
    total_debit = total_credit = 0
    status_counts = Counter()
    voucher_numbers = {}
    for row in grouped["voucher"]:
        payload, period = row["payload"], row["period"]
        status_counts[payload["status"]] += 1
        vouchers_by_id[row["external_id"]].append(row)
        number_key = (period, payload["voucher_type"], payload["number"])
        if (
            number_key in voucher_numbers
            and voucher_numbers[number_key]["external_id"] != row["external_id"]
        ):
            issue(
                "VOUCHER_NUMBER_COLLISION",
                f"{period} {payload['voucher_type']}字 {payload['number']} 号对应多个原凭证 ID，须查明是否重复或重新编号",
                row,
                "warning"
                if payload["status"] == voucher_numbers[number_key]["payload"]["status"] == "void"
                else "blocking",
            )
        else:
            voucher_numbers[number_key] = row
        debit = credit = 0
        for line in payload["lines"]:
            code = line["account_code"]
            try:
                debit_value, credit_value = (
                    signed_cents(line["debit"]),
                    signed_cents(line["credit"]),
                )
            except (ValueError, TypeError, KeyError) as error:
                issue("LINE_MONEY_INVALID", str(error), row)
                continue
            debit += debit_value
            credit += credit_value
            if debit_value and credit_value:
                issue(
                    "LINE_BOTH_SIDES", f"分录 {line['ordinal']} 同时有借贷金额，须核实原格式", row
                )
            if not debit_value and not credit_value:
                issue("LINE_ZERO_AMOUNT", f"分录 {line['ordinal']} 借贷均为零", row, "warning")
            if (debit_value < 0 or credit_value < 0) and not payload["is_reversal"]:
                issue(
                    "RED_ENTRY_NOT_DECLARED",
                    "负数分录必须明确原凭证红字冲销标志，不能自动解释",
                    row,
                )
            if code not in accounts:
                issue("VOUCHER_ACCOUNT_UNKNOWN", f"分录科目 {code} 未提供", row)
            elif not accounts[code]["payload"]["posting"]:
                issue("VOUCHER_NONLEAF_ACCOUNT", f"分录使用非末级科目 {code}", row)
            check_aux(row, code, line["auxiliaries"])
            if payload["status"] == "posted":
                movement[(period, code, "{}")][0] += debit_value
                movement[(period, code, "{}")][1] += credit_value
                if line["auxiliaries"]:
                    dimension_key = json.dumps(line["auxiliaries"], sort_keys=True)
                    movement[(period, code, dimension_key)][0] += debit_value
                    movement[(period, code, dimension_key)][1] += credit_value
        if debit != credit:
            issue(
                "VOUCHER_UNBALANCED",
                f"凭证借贷不平：借 {_total_money(debit)}，贷 {_total_money(credit)}",
                row,
                "warning" if payload["status"] == "void" else "blocking",
            )
        if payload["status"] == "posted":
            posted_by_month[period] += 1
            total_debit += debit
            total_credit += credit
        else:
            issue(
                "NONPOSTED_VOUCHER_RETAINED",
                f"原凭证为 {payload['original_status']}，保留历史但不计入已记账余额",
                row,
                "warning",
            )
    checks.append(
        {
            "code": "VOUCHER_BALANCE",
            "passed": not any(
                item["code"] == "VOUCHER_UNBALANCED" and item["severity"] == "blocking"
                for item in issues
            ),
            "count": len(grouped["voucher"]),
        }
    )

    # Each account can have an aggregate row and auxiliary rows. Pick aggregate once;
    # auxiliary rows are reconciliation evidence, never another amount accumulator.
    balance_rows = {}
    auxiliary_balance_rows = defaultdict(list)
    for row in grouped["balance"]:
        payload = row["payload"]
        code, aux = payload["account_code"], payload["auxiliaries"]
        key = (row["period"], code, json.dumps(aux, sort_keys=True))
        balance_rows[key] = row
        if aux:
            auxiliary_balance_rows[(row["period"], code)].append(row)
        if code not in accounts:
            issue("BALANCE_ACCOUNT_UNKNOWN", f"余额科目 {code} 未提供", row)
        if aux:
            check_aux(row, code, aux)
        try:
            opening = signed_cents(payload["opening"])
            debit = signed_cents(payload["period_debit"])
            credit = signed_cents(payload["period_credit"])
            closing = signed_cents(payload["closing"])
            if opening + debit - credit != closing:
                issue("BALANCE_EQUATION_MISMATCH", "期初净额 + 本期借方 − 本期贷方 ≠ 期末净额", row)
            if payload["scope"] != "posted":
                issue("BALANCE_SCOPE_INVALID", "余额不是仅已记账口径，不能与已记账凭证勾稽", row)
            if (
                row["period"].endswith("-01")
                and "year_debit" in payload
                and (
                    signed_cents(payload["year_debit"]) != debit
                    or signed_cents(payload["year_credit"]) != credit
                )
            ):
                issue("YEAR_START_MOVEMENT_MISMATCH", "一月本年累计借贷须等于一月本期发生额", row)
            if code in accounts and accounts[code]["payload"]["posting"]:
                actual = movement.get(key, [0, 0])
                if [debit, credit] != actual:
                    issue(
                        "PERIOD_MOVEMENT_MISMATCH",
                        f"余额表本期发生与已记账凭证不同：表借/贷 {_total_money(debit)}/{_total_money(credit)}，凭证 {_total_money(actual[0])}/{_total_money(actual[1])}",
                        row,
                    )
        except (ValueError, TypeError, KeyError) as error:
            issue("BALANCE_MONEY_INVALID", str(error), row)

    months = _months(context["history_start"], context["history_end"])
    leaf_codes = sorted(
        code
        for code, row in accounts.items()
        if row["payload"]["posting"] and not children.get(code)
    )
    effective = {}
    for period in months:
        for code in accounts:
            aggregate = balance_rows.get((period, code, "{}"))
            details = auxiliary_balance_rows.get((period, code), [])
            if len({tuple(sorted(row["payload"]["auxiliaries"])) for row in details}) > 1:
                issue(
                    "AUXILIARY_BALANCE_GRAIN_MIXED",
                    f"{period} 科目 {code} 的辅助余额混合不同维度汇总层级，不能叠加",
                    details[0],
                )
            if aggregate:
                effective[(period, code)] = {
                    field: signed_cents(aggregate["payload"][field])
                    for field in ("opening", "period_debit", "period_credit", "closing")
                }
            elif details:
                effective[(period, code)] = {
                    field: sum(signed_cents(row["payload"][field]) for row in details)
                    for field in ("opening", "period_debit", "period_credit", "closing")
                }
            if aggregate and details:
                for field in ("opening", "period_debit", "period_credit", "closing"):
                    if sum(signed_cents(row["payload"][field]) for row in details) != signed_cents(
                        aggregate["payload"][field]
                    ):
                        issue(
                            "AUXILIARY_BALANCE_SUM_MISMATCH",
                            f"科目 {code} 的辅助明细 {field} 合计与科目汇总不同",
                            aggregate,
                        )
            required_aux = accounts[code]["payload"]["auxiliary_requirements"]
            if (
                required_aux
                and aggregate
                and not details
                and any(
                    signed_cents(aggregate["payload"][field])
                    for field in ("opening", "period_debit", "period_credit", "closing")
                )
            ):
                issue(
                    "AUXILIARY_BALANCES_MISSING",
                    f"科目 {code} 有辅助核算及金额，缺少辅助余额明细",
                    aggregate,
                )
        for code in leaf_codes:
            if (period, code) not in effective:
                issue(
                    "MONTH_ACCOUNT_BALANCE_MISSING",
                    f"{period} 缺少末级科目 {code} 余额；零余额也须原表明确提供",
                    accounts[code],
                )
        for parent, codes in children.items():
            if (period, parent) not in effective or any(
                (period, code) not in effective for code in codes
            ):
                continue
            for field in ("opening", "period_debit", "period_credit", "closing"):
                if effective[(period, parent)][field] != sum(
                    effective[(period, code)][field] for code in codes
                ):
                    issue(
                        "PARENT_BALANCE_SUM_MISMATCH",
                        f"{period} 上级科目 {parent} 的 {field} 与直接下级合计不同",
                        accounts.get(parent),
                    )
        if leaf_codes and all((period, code) in effective for code in leaf_codes):
            for field in ("opening", "closing"):
                net = sum(effective[(period, code)][field] for code in leaf_codes)
                if net:
                    issue(
                        "TRIAL_BALANCE_MISMATCH",
                        f"{period} 末级科目 {field} 借方正净额合计不是零：{_total_money(net)}",
                    )
        checks.append(
            {
                "code": "MONTH_COVERAGE",
                "period": period,
                "passed": bool(leaf_codes)
                and all((period, code) in effective for code in leaf_codes),
                "posted_voucher_count": posted_by_month[period],
            }
        )

    # Check account and per-auxiliary continuity between every consecutive month.
    for previous, period in zip(months, months[1:]):
        for code in leaf_codes:
            if (
                (previous, code) in effective
                and (period, code) in effective
                and effective[(previous, code)]["closing"] != effective[(period, code)]["opening"]
            ):
                issue(
                    "BALANCE_CONTINUITY_MISMATCH",
                    f"{code} 在 {previous} 的期末不等于 {period} 的期初",
                    accounts[code],
                )
        for (row_period, code, aux), row in balance_rows.items():
            if row_period != previous:
                continue
            current = balance_rows.get((period, code, aux))
            if (
                current
                and previous[:4] == period[:4]
                and "year_debit" in row["payload"]
                and "year_debit" in current["payload"]
            ):
                for side in ("debit", "credit"):
                    if signed_cents(row["payload"][f"year_{side}"]) + signed_cents(
                        current["payload"][f"period_{side}"]
                    ) != signed_cents(current["payload"][f"year_{side}"]):
                        issue(
                            "YEAR_MOVEMENT_CONTINUITY_MISMATCH",
                            f"{code} 在 {previous}/{period} 的本年累计 {side} 不连续",
                            current,
                        )
            if aux == "{}":
                continue
            if current and signed_cents(row["payload"]["closing"]) != signed_cents(
                current["payload"]["opening"]
            ):
                issue(
                    "AUXILIARY_CONTINUITY_MISMATCH",
                    f"辅助余额 {code} 在 {previous}/{period} 不连续",
                    current,
                )
            elif not current and signed_cents(row["payload"]["closing"]):
                issue(
                    "AUXILIARY_NEXT_MONTH_MISSING",
                    f"{period} 缺少尚有期末余额的辅助档案 {code}",
                    row,
                )
    checks.append(
        {
            "code": "BALANCE_RECONCILIATION",
            "passed": not any(
                item["severity"] == "blocking"
                and item["code"]
                in {
                    "BALANCE_EQUATION_MISMATCH",
                    "PERIOD_MOVEMENT_MISMATCH",
                    "TRIAL_BALANCE_MISMATCH",
                    "BALANCE_CONTINUITY_MISMATCH",
                    "AUXILIARY_BALANCE_SUM_MISMATCH",
                    "PARENT_BALANCE_SUM_MISMATCH",
                }
                for item in issues
            ),
        }
    )

    open_receivable = open_payable = 0
    settlement_scope = (mapping or {}).get("settlement_accounts", {})
    open_by_counterparty = defaultdict(int)
    open_bindings = []
    reconciliation_start = len(issues)
    for row in grouped["open_item"]:
        payload = row["payload"]
        if ("counterparty", payload["counterparty_code"]) not in auxiliaries:
            issue("OPEN_ITEM_COUNTERPARTY_UNKNOWN", "未结事项往来方档案缺失", row)
        original, settled, remaining = (
            signed_cents(payload[field])
            for field in ("original_amount", "settled_amount", "remaining_amount")
        )
        if min(original, settled, remaining) < 0 or original - settled != remaining:
            issue("OPEN_ITEM_AMOUNT_MISMATCH", "未结金额须为原金额减已核销金额，且均为非负数", row)
        if payload["occurred_on"] >= context["cutover_date"]:
            issue("OPEN_ITEM_AFTER_CUTOVER", "历史未结事项发生日期不能晚于接管时点", row)
        if payload.get("due_on") and payload["due_on"] < payload["occurred_on"]:
            issue("OPEN_ITEM_DUE_BEFORE_OCCURRED", "到期日期早于发生日期，请核实", row, "warning")
        for identifier in payload["voucher_ids"]:
            related = vouchers_by_id.get(identifier, [])
            if not related:
                issue("OPEN_ITEM_VOUCHER_UNKNOWN", f"未结事项关联凭证 {identifier} 未提供", row)
            elif len(related) != 1 or related[0]["payload"]["status"] != "posted":
                issue(
                    "OPEN_ITEM_VOUCHER_NOT_POSTED",
                    f"未结事项关联凭证 {identifier} 不唯一或尚未记账",
                    row,
                )
        if payload["kind"] == "receivable":
            open_receivable += remaining
        else:
            open_payable += remaining
        allowed = settlement_scope.get(payload["kind"], [])
        selected = payload.get("account_code") or (allowed[0] if len(allowed) == 1 else "")
        if not allowed or not selected or selected not in allowed:
            issue(
                "OPEN_ITEM_SCOPE_UNVERIFIED",
                "未结事项须明确应收/应付科目范围；多科目时每条事项须指定来源科目",
                row,
            )
        else:
            open_by_counterparty[(payload["kind"], selected, payload["counterparty_code"])] += (
                remaining
            )
            open_bindings.append(
                {
                    "external_id": row["external_id"],
                    "kind": payload["kind"],
                    "account_code": selected,
                    "counterparty_code": payload["counterparty_code"],
                    "remaining_amount": payload["remaining_amount"],
                }
            )
    counterpart_balances = defaultdict(int)
    scope_accounts = {}
    for item_kind, account_codes in settlement_scope.items():
        for code in account_codes:
            if code in scope_accounts:
                issue("OPEN_ITEM_SCOPE_CROSSED", f"科目 {code} 同时属于应收应付范围")
                continue
            scope_accounts[code] = item_kind
            if code not in accounts or not accounts[code]["payload"]["posting"]:
                issue("OPEN_ITEM_SCOPE_UNKNOWN", f"未结事项范围科目 {code} 不存在或不是末级")
                continue
            expected_category, expected_side = (
                ("asset", "debit") if item_kind == "receivable" else ("liability", "credit")
            )
            if (
                accounts[code]["payload"]["category"] != expected_category
                or accounts[code]["payload"]["normal_balance"] != expected_side
            ):
                issue(
                    "OPEN_ITEM_SCOPE_ACCOUNT_TYPE",
                    f"{item_kind} 科目 {code} 的类别或正常余额方向与指定范围不一致",
                    accounts[code],
                )
            details = auxiliary_balance_rows.get((months[-1], code), [])
            if not details:
                # A zero source closing balance is explicit, not an invented subledger.
                final = effective.get((months[-1], code))
                if not final or final["closing"] != 0:
                    issue(
                        "OPEN_ITEM_SCOPE_UNVERIFIED",
                        f"科目 {code} 缺少接管前最后期间的往来方辅助余额，不能猜分户余额",
                        accounts[code],
                    )
                continue
            for detail in details:
                counterparty = detail["payload"]["auxiliaries"].get("counterparty")
                if not counterparty:
                    issue(
                        "OPEN_ITEM_SCOPE_UNVERIFIED",
                        f"科目 {code} 的辅助余额没有往来方编码，不能核对逐笔未结事项",
                        detail,
                    )
                    continue
                balance = signed_cents(detail["payload"]["closing"])
                counterpart_balances[(item_kind, code, counterparty)] += (
                    balance if item_kind == "receivable" else -balance
                )
    for key in set(counterpart_balances) | set(open_by_counterparty):
        expected = counterpart_balances.get(key, 0)
        actual = open_by_counterparty.get(key, 0)
        if expected < 0:
            issue(
                "OPEN_ITEM_OPPOSITE_BALANCE",
                f"{key[0]} 科目 {key[1]}/{key[2]} 有反向余额，当前未结模板不可自动冲抵",
            )
        if expected != actual:
            issue(
                "OPEN_ITEM_BALANCE_MISMATCH",
                f"{key[0]} 科目 {key[1]}/{key[2]} 的未结明细 {_total_money(actual)} 与来源辅助余额 {_total_money(expected)} 不一致",
            )
    open_reconciliation_complete = (
        bool(scope_accounts)
        and not any(item["severity"] == "blocking" for item in issues[reconciliation_start:])
        and not any(
            item["severity"] == "blocking"
            and item["code"]
            in {
                "AUXILIARY_BALANCE_SUM_MISMATCH",
                "AUXILIARY_BALANCE_GRAIN_MIXED",
                "AUXILIARY_REFERENCE_UNKNOWN",
                "AUXILIARY_BALANCES_MISSING",
            }
            for item in issues
        )
    )
    if not grouped["open_item"] and not settlement_scope:
        issue(
            "OPEN_ITEMS_NOT_PROVIDED",
            "未提供逐笔未结往来；汇总余额不能还原账龄，接管后不得据此自动核销",
            severity="warning",
        )
    checks.append(
        {
            "code": "OPEN_ITEM_RECONCILIATION",
            "passed": open_reconciliation_complete,
            "scope_provided": bool(settlement_scope),
            "count": len(open_bindings),
        }
    )
    if context.get("source_controls_verified") is not True:
        issue(
            "SOURCE_CONTROLS_NOT_AVAILABLE",
            "尚无源软件控制总数验收；当前已完成原件解析数量与余额勾稽，不宣称源导出范围完整",
            severity="warning",
        )

    counts = {kind: len(grouped[kind]) for kind in sorted(KINDS)}
    totals = {
        "posted_debit": _total_money(total_debit),
        "posted_credit": _total_money(total_credit),
        "open_receivable": _total_money(open_receivable),
        "open_payable": _total_money(open_payable),
    }
    last_period = months[-1]
    if leaf_codes and all((last_period, code) in effective for code in leaf_codes):
        totals["cutover_net_balance"] = _total_money(
            sum(effective[(last_period, code)]["closing"] for code in leaf_codes)
        )
    blocking = sum(entry["severity"] == "blocking" for entry in issues)
    warning = sum(entry["severity"] == "warning" for entry in issues)
    coverage = {
        "complete": blocking == 0 and bool(accounts) and bool(grouped["balance"]),
        "periods": months,
        "balance_periods": sorted({row["period"] for row in grouped["balance"]}),
        "voucher_periods": sorted(posted_by_month),
        "has_accounts": bool(accounts),
        "has_balances": bool(grouped["balance"]),
        "has_open_items": bool(grouped["open_item"]),
        "open_item_reconciliation_complete": open_reconciliation_complete,
        "source_controls_verified": context.get("source_controls_verified") is True,
        "history_is_reference_only": True,
        "balance_and_vouchers_accumulated_together": False,
        "unsupported_modules": [
            "inventory",
            "fixed_assets",
            "payroll",
            "quantity",
            "foreign_currency",
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "context": context,
                "records": [
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"row_number"} and key != "payload"
                    }
                    | {"payload": json.loads(canonical_payload(row["payload"]))}
                    for row in unique
                ],
                "mapping": mapping,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "summary": {
            "counts": counts,
            "totals": totals,
            "status_counts": dict(status_counts),
            "deduplicated_count": deduplicated,
            "raw_record_count": len(records),
        },
        "issues": issues,
        "blocking_count": blocking,
        "warning_count": warning,
        "checks": checks,
        "coverage": coverage,
        "open_item_bindings": open_bindings,
        "fingerprint": fingerprint,
    }
