"""Bounded presentation of authoritative reconciliation; no new accounting rules.

Only called inside the existing authorized month-close reader. Full inputs and
dependency revisions are fingerprinted even when display rows are omitted.
Missing/unreviewed bank coverage must never turn internal zero defaults into a
claimed bank balance. These are current read results, not a close approval.
"""

from copy import deepcopy

from app.accounting.close_dependencies import close_dependencies
from app.accounting.domain import AccountingError
from app.accounting.models import cents_to_money
from app.accounting.service import fingerprint
from app.posting.service import PostingService
from app.subledger.service import SubledgerService
from app.subledger.reconciliation import unresolved_adjustments

LIMIT = 20
NEXT = "打开“正式账簿 → 正式月结与报表发布”，核对对应科目、辅助维度及未结事项；需要更正时走事实修订或调整流程"
BANK_NEXT = (
    "打开“正式账簿 → 正式月结与报表发布”，核对对应银行原件、流水与分录，由独立核验人核验后重新预检"
)


def integer(value):
    if type(value) is not int:
        raise ValueError("reconciliation requires exact integer cents/counts")
    return value


def money(value):
    value = integer(value)
    return ("-" if value < 0 else "") + cents_to_money(abs(value))


def _text(value):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("invalid reconciliation label")
    return value


def _bounded(raw):
    result = {k: deepcopy(v) for k, v in raw.items() if k not in {"rows", "adjustments"}}
    # Prioritize blocked rows; preserve domain ordering within each group.
    rows = list(raw.get("rows", []))
    rows.sort(key=lambda r: r["passed"])
    result["rows"] = deepcopy(rows[:LIMIT])
    result["omitted_rows"] = max(0, len(rows) - LIMIT)
    return result


def collect_details(db, conn, actor, book, checks=None):
    source = {
        "kind": "reconciliation",
        "version": 1,
        "company_id": book["company_id"],
        "id": book["id"],
        "period": book["period"],
        "status": "current",
    }
    try:
        posting = PostingService(db)
        trial = posting.trial_balance_in_transaction(conn, actor, book["id"])
        sub = SubledgerService(db)
        prior = {row["code"]: row["detail"] for row in checks or []}
        control = prior.get("subledger_control")
        bank = prior.get("bank_reconciliation")
        if control is None:
            control = sub.control_reconciliation_in_transaction(
                conn, actor, book["id"], trial=trial
            )
        if bank is None:
            bank = sub.bank_reconciliation_in_transaction(conn, actor, book["id"], trial=trial)
        adjustments = unresolved_adjustments(conn, book["id"])
        dependencies = close_dependencies(conn, book["id"], "small_enterprise_basic")
    except AccountingError as error:
        if error.status != 409:
            raise  # Authorization, database and programming failures are not missing facts.
        source.update(
            status="unavailable", reason=str(error), inputs_fingerprint=fingerprint(str(error))
        )
        return source
    source["inputs_fingerprint"] = fingerprint(
        {
            "trial": trial,
            "control": control,
            "bank": bank,
            "adjustments": adjustments,
            "dependencies": dependencies,
        }
    )
    source["control"], source["bank"] = _bounded(control), _bounded(bank)
    # Aggregate bank amounts can net opposing accounts or contain placeholder
    # coverage zeros. Only independently qualified per-account values are sent.
    for key in list(source["bank"]):
        if key.endswith("_cents"):
            del source["bank"][key]
    labels = {row["account_id"]: row for row in trial["rows"]}
    for row in source["control"]["rows"]:
        meta = labels.get(row["control_account_id"])
        row["code"] = meta["code"] if meta else ""
        row["name"] = meta["name"] if meta else "当前正式科目未能定位"
    for row in source["bank"]["rows"]:
        own = [a for a in adjustments if a["bank_account_id"] == row["account_id"]]
        row["adjustment_count"] = len(own)
        row["adjustment_absolute_cents"] = sum(abs(integer(a["amount_cents"])) for a in own)
        if not row["coverage"]:
            # Never send invented bank balance/difference defaults to the model.
            for key in (
                "opening_cents",
                "closing_cents",
                "difference_cents",
                "book_difference_cents",
                "opening_book_difference_cents",
            ):
                row[key] = None
    validate_details(source, book["company_id"], book["id"], book["period"])
    return source


def validate_details(source, company_id, book_id, period):
    if (
        source.get("kind") != "reconciliation"
        or type(source.get("version")) is not int
        or source["version"] != 1
        or source.get("company_id") != company_id
        or source.get("id") != book_id
        or source.get("period") != period
    ):
        raise ValueError("reconciliation scope mismatch")
    digest = source.get("inputs_fingerprint")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("reconciliation fingerprint missing")
    if source["status"] == "unavailable":
        _text(source["reason"])
        if "bank" in source or "control" in source:
            raise ValueError("unavailable reconciliation contains amounts")
        return
    if source["status"] != "current":
        raise ValueError("invalid reconciliation status")
    for kind in ("control", "bank"):
        part = source[kind]
        if (
            not isinstance(part["rows"], list)
            or len(part["rows"]) > LIMIT
            or integer(part["omitted_rows"]) < 0
        ):
            raise ValueError("unbounded reconciliation rows")
        if type(part["passed"]) is not bool:
            raise ValueError("invalid reconciliation outcome")
        for row in part["rows"]:
            if type(row["passed"]) is not bool:
                raise ValueError("invalid reconciliation row outcome")
            for key in ("dimensions_valid", "mapping_ambiguous", "statement_source_matches"):
                if key in row and type(row[key]) is not bool:
                    raise ValueError("invalid reconciliation qualification")
            _text(row["name"])
            if not isinstance(row.get("aux"), dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in row["aux"].items()
            ):
                raise ValueError("invalid auxiliary identity")
            for key, value in row.items():
                if key.endswith(("_cents", "_count")) and not (
                    kind == "bank"
                    and value is None
                    and key
                    in {
                        "source_line_count",
                        "opening_cents",
                        "closing_cents",
                        "difference_cents",
                        "book_difference_cents",
                        "opening_book_difference_cents",
                    }
                ):
                    integer(value)
                    if key.endswith("_count") and value < 0:
                        raise ValueError("invalid reconciliation count")
            if kind == "control":
                _text(row["code"])
                if row["kind"] not in {"receivable", "advance_paid", "payable", "advance_received"}:
                    raise ValueError("invalid control nature")
                expected = row["subledger_cents"] * (
                    1 if row["kind"] in {"receivable", "advance_paid"} else -1
                )
                if row["book_cents"] - expected != row["difference_cents"]:
                    raise ValueError("inconsistent control difference")
            else:
                if type(row["coverage"]) is not bool or row["coverage"] != (
                    row["coverage_state"] == "verified"
                ):
                    raise ValueError("invalid bank qualification")
                amounts = [
                    row[k]
                    for k in (
                        "opening_cents",
                        "closing_cents",
                        "difference_cents",
                        "book_difference_cents",
                        "opening_book_difference_cents",
                    )
                ]
                if row["coverage"]:
                    for amount in amounts:
                        integer(amount)
                    if (
                        row["book_cents"] - row["closing_cents"] != row["book_difference_cents"]
                        or row["opening_cents"] + row["net_cents"] - row["closing_cents"]
                        != row["difference_cents"]
                    ):
                        raise ValueError("inconsistent bank difference")
                elif any(v is not None for v in amounts):
                    raise ValueError("unverified bank amounts must remain unknown")
        for key, value in part.items():
            if key.endswith(("_cents", "_count")):
                integer(value)
                if key.endswith("_count") and value < 0:
                    raise ValueError("invalid reconciliation count")


def detail_issues(source):
    validate_details(source, source["company_id"], source["id"], source["period"])
    issues = []

    def add(code, reason, action=NEXT, owner="账套会计"):
        issues.append(
            dict(
                code=code,
                reason=reason,
                owner_role=owner,
                next_action=action,
                evidence_ids=["book:" + source["id"]],
            )
        )

    if source["status"] == "unavailable":
        add(
            "reconciliation_unavailable",
            "当前勾稽暂不可读取：" + source["reason"],
            "打开正式账簿，先核对正式期初与核算配置；处理后重新发起诊断",
        )
        return issues
    control, bank = source["control"], source["bank"]
    if not control["passed"] and not control["rows"]:
        add("control_setup", control.get("reason") or "子账核对未通过，尚无可核验的科目明细")
    for row in control["rows"]:
        if row["passed"]:
            continue
        aux = (
            "；辅助维度 " + ", ".join(f"{k}={v}" for k, v in sorted(row["aux"].items()))
            if row["aux"]
            else ""
        )
        reason = (
            f"科目 {row['code']} {row['name']}：账面余额 {money(row['book_cents'])} 元，"
            f"未结事项 {money(row['subledger_cents'])} 元，差额 {money(row['difference_cents'])} 元"
            "（借方为正；差额=账面余额−按核算类型转换的子账余额）" + aux
        )
        if not row["dimensions_valid"]:
            reason += "；必要辅助维度缺失"
        add("control_account_difference", reason)
    for row in bank["rows"]:
        name = "银行账户“" + row["name"] + "”"
        if not row["coverage"]:
            add(
                "bank_coverage_missing",
                name + "缺少已独立核验的本月完整对账单",
                BANK_NEXT,
                "账套会计与独立核验人",
            )
        elif not row["passed"]:
            reason = name
            if row["mapping_ambiguous"] or not row["statement_source_matches"]:
                reason += "的科目映射或流水原件绑定尚未一致，不能把差额作为已核实结论"
            else:
                reason += (
                    f"：账面期末 {money(row['book_cents'])} 元，对账单期末 {money(row['closing_cents'])} 元，"
                    f"账面−对账单 {money(row['book_difference_cents'])} 元；"
                    f"期初+净流入−期末 {money(row['difference_cents'])} 元；"
                    f"账面期初−对账单期初 {money(row['opening_book_difference_cents'])} 元"
                )
            reason += f"；未匹配流水 {row['unmatched_count']} 笔，未匹配正式分录 {row['unmatched_formal_count']} 笔"
            add("bank_account_difference", reason, BANK_NEXT, "账套会计与独立核验人")
        if row["adjustment_count"]:
            add(
                "bank_adjustments",
                name
                + f"有 {row['adjustment_count']} 项未结调节项，金额绝对值合计 {money(row['adjustment_absolute_cents'])} 元（不互相抵销）",
                BANK_NEXT,
            )
    if not bank["passed"] and not bank["rows"]:
        add(
            "bank_setup",
            "银行账户身份或完整覆盖尚未确认，不能由账套合计推断逐账户已对平",
            BANK_NEXT,
        )
    for key, label in (
        ("unassigned_line_count", "未归属银行账户的流水"),
        ("unmapped_cash_bucket_count", "未映射银行账户的余额组"),
    ):
        if bank.get(key):
            add("bank_unassigned", f"{label} {integer(bank[key])} 项", BANK_NEXT)
    covered = sum(row["adjustment_count"] for row in bank["rows"])
    if bank.get("unresolved_adjustment_count", 0) > covered:
        add(
            "bank_adjustments",
            f"另有 {bank['unresolved_adjustment_count'] - covered} 项调节项未在以上账户明细展示，请核对全部账户",
            BANK_NEXT,
        )
    for kind, part, label in (("control", control, "子账"), ("bank", bank, "银行")):
        if part.get("continuity", {}).get("passed") is False:
            add(
                kind + "_continuity",
                label + "跨期衔接或稳定身份未通过，不能据单月余额认定已对平",
                BANK_NEXT if kind == "bank" else NEXT,
            )
        if part["omitted_rows"]:
            add(
                kind + "_omitted",
                f"{label}另有 {part['omitted_rows']} 行未展示，请打开完整明细；本次摘要不是全量结论",
                BANK_NEXT if kind == "bank" else NEXT,
            )
    return issues


def delivery_target(view):
    """Read navigation only. No URL, permission or business command from model."""
    rows = [s for s in view.get("sources", []) if s["kind"] == "reconciliation"]
    if view.get("coverage") != "selected_book" or len(rows) != 1:
        return None
    source = rows[0]
    validate_details(source, view["company_id"], view["book_id"], view["period"])
    return dict(
        schema="assistant-month-close-v1",
        company_id=view["company_id"],
        book_id=view["book_id"],
        period=view["period"],
        inputs_fingerprint=source["inputs_fingerprint"],
    )
