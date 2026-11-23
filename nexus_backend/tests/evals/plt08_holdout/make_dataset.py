"""生成 PLT08-E 封存留出样本。

本文件生成的全部为**合成数据**：企业、法人、金额、凭证号与对象 ID 都是虚构的，
仅用于离线评测，不得导入任何真实账套，也不代表任何真实客户的情况。
生成结果写入同目录的 dataset.json，评测器只读该文件，不再访问数据库或网络。
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "dataset.json"
NOTICE = (
    "本文件全部为合成数据，仅用于 PLT08-E 留出评测；企业、法人、金额与对象 ID 均为虚构，"
    "不得导入真实账套，也不代表任何真实客户的合规结论。"
)


def money(cents):
    return f"{cents // 100:,}.{cents % 100:02d}"


def _version(seed):
    return f"{seed:016x}"


def _revenue_case(index, *, category, fee_supported, off_period=False, abstain=False):
    book = f"ho-book-{index:03d}"
    period = "2026-10"
    company = f"ho-company-{(index % 5) + 1:02d}"
    firm = f"ho-firm-{(index % 2) + 1:02d}"
    settle = 8_000_000 + index * 137_000
    shift = 3_500 * ((index % 7) + 1)
    bank = settle - shift
    settle_id = f"stl-{index:03d}"
    bank_id = f"bnk-{index:03d}"
    fee_id = f"fee-{index:03d}"
    sources = [
        {
            "id": settle_id,
            "description": "平台结算单",
            "amount_cents": settle,
            "source_version": _version(index * 3 + 1),
            "evidence_href": f"/workspace/accounting?book={book}&period={period}&source={settle_id}",
        },
        {
            "id": bank_id,
            "description": "银行到账流水",
            "amount_cents": bank,
            "source_version": _version(index * 3 + 2),
            "evidence_href": f"/workspace/accounting?book={book}&period={period}&source={bank_id}",
        },
    ]
    entry = f"/workspace/accounting?book={book}&period={period}&source={bank_id}"
    handoff = {
        "owner_role": "accountant",
        "reason": "缺手续费或扣款明细，请向平台或银行取得凭据后重新核对",
        "action_target": entry,
    }
    if abstain:
        settlement = {
            "status": "no_source",
            "summary": "未读到结算单或银行流水。",
            "facts": {},
            "missing": ["settlement_source", "bank_source"],
            "handoff": handoff,
        }
        expected = {
            "mode": "answer",
            "conclusion_state": "abstained",
            "must_include": ["未能形成结论"],
            # 弃答不得出现金额或归因断定；「差额或数字」是固定段名，不能当作违规词。
            "must_not_include": [
                money(settle),
                money(bank),
                money(shift),
                "这是手续费",
                "属于手续费",
                "原因是手续费",
            ],
            "citations": [],
            "blockers": [],
        }
        return _sample(
            index,
            category,
            "revenue_mismatch",
            book,
            period,
            company,
            firm,
            {"kind": "revenue_mismatch", "settlement": settlement},
            {},
            expected,
        )

    facts = {
        "settlement_total_cents": settle,
        "settlement_total": money(settle),
        "bank_total_cents": bank,
        "bank_total": money(bank),
        "difference_cents": shift,
        "difference": money(shift),
        "reason": "fee_evidence" if fee_supported else "unresolved",
        "sources": sources,
    }
    fees = {}
    missing = []
    if fee_supported:
        fees[fee_id] = {
            "id": fee_id,
            "description": "平台手续费凭据",
            "amount_cents": shift,
            "source_version": _version(index * 3 + 3),
            "evidence_href": f"/workspace/accounting?book={book}&period={period}&source={fee_id}",
        }
        facts["fee_source"] = fees[fee_id]
        facts["fee_total_cents"] = shift
        facts["fee_total"] = money(shift)
        status = "complete"
        summary = f"结算单与银行到账相差 {money(shift)} 元，已匹配到同额手续费凭据。"
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": [money(settle), money(bank), money(shift)],
            "must_not_include": [],
            "citations": [settle_id, bank_id, fee_id],
            "blockers": [],
        }
    elif off_period:
        facts["reason"] = "fee_evidence_coverage"
        missing = ["fee_evidence_coverage"]
        status = "conflicting"
        summary = f"结算单与银行到账相差 {money(shift)} 元；现有手续费凭据不在本期，不能用于解释本期差额。"
        expected = {
            "mode": "answer",
            "conclusion_state": "unconfirmed",
            "must_include": [money(shift), "手续费凭据覆盖期间"],
            "must_not_include": [
                "这是手续费",
                "属于手续费",
                "原因是手续费",
                "可解释本期",
            ],
            "citations": [settle_id, bank_id],
            "blockers": [],
        }
    else:
        missing = ["fee_evidence"]
        status = "conflicting"
        summary = f"结算单与银行到账相差 {money(shift)} 元；原因待核对，缺手续费或扣款明细。"
        expected = {
            "mode": "answer",
            "conclusion_state": "unconfirmed",
            "must_include": [money(settle), money(bank), money(shift)],
            "must_not_include": ["这是手续费", "属于手续费", "原因是手续费", "银行手续费导致"],
            "citations": [settle_id, bank_id],
            "blockers": [],
        }
    settlement = {
        "status": status,
        "summary": summary,
        "facts": facts,
        "missing": missing,
        "handoff": handoff,
    }
    payload = {
        "kind": "revenue_mismatch",
        "settlement_source_id": settle_id,
        "bank_source_id": bank_id,
        "fee_source_id": fee_id if fee_supported else "",
    }
    return _sample(
        index,
        category,
        "revenue_mismatch",
        book,
        period,
        company,
        firm,
        payload,
        {"settlement": settlement, "fees": fees},
        expected,
    )


def _delivery_case(index, *, category, mode):
    book = f"ho-book-{index:03d}"
    period = "2026-10"
    company = f"ho-company-{(index % 5) + 1:02d}"
    firm = f"ho-firm-{(index % 2) + 1:02d}"
    payload = {"kind": "why_not_delivered"}
    if mode == "blocked":
        blockers = [
            {"code": "voucher_review_pending", "next_action": "完成本期凭证复核"},
            {"code": "unposted_manual", "next_action": "补录手工凭证"},
        ]
        close = {"status": "blocked", "version": 4 + index, "blockers": blockers}
        delivery = {"package": f"pkg-{index:03d}", "opened": False}
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": ["2 项阻断", "voucher_review_pending", "unposted_manual"],
            "must_not_include": ["可以按既有流程交付"],
            "citations": [],
            "blockers": ["voucher_review_pending", "unposted_manual"],
        }
    elif mode == "cross_period":
        blockers = [{"code": "cross_period_open_items", "next_action": "先处理上期未结清事项"}]
        close = {"status": "blocked", "version": 9 + index, "blockers": blockers}
        delivery = {"package": f"pkg-{index:03d}", "opened": False}
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": ["cross_period_open_items"],
            "must_not_include": ["可以按既有流程交付"],
            "citations": [],
            "blockers": ["cross_period_open_items"],
        }
    else:
        close = {"status": "no_run", "version": None, "blockers": []}
        delivery = {"package": None, "opened": False}
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": ["尚未确认", "没有月结预检记录"],
            "must_not_include": ["可以按既有流程交付", "未发现阻断项"],
            "citations": [],
            "blockers": [],
        }
    return _sample(
        index,
        category,
        "why_not_delivered",
        book,
        period,
        company,
        firm,
        payload,
        {"close": close, "delivery": delivery},
        expected,
    )


def _origin_case(index, *, category, mode):
    book = f"ho-book-{index:03d}"
    period = "2026-10"
    company = f"ho-company-{(index % 5) + 1:02d}"
    firm = f"ho-firm-{(index % 2) + 1:02d}"
    account_code = "6001"
    first = 1_200_000 + index * 9_000
    second = 800_000 + index * 5_000
    date_a = f"{period}-0{(index % 8) + 1}"
    date_b = f"{period}-1{(index % 8)}"
    if mode == "off_period":
        date_a = "2026-09-28"
    rows = [
        {
            "voucher_id": f"vch-{index:03d}-a",
            "voucher_no": 100 + index,
            "voucher_date": date_a,
            "description": "虚构收入",
            "amount_cents": first,
            "content_fingerprint": _version(index * 7 + 1),
        },
        {
            "voucher_id": f"vch-{index:03d}-b",
            "voucher_no": 200 + index,
            "voucher_date": date_b,
            "description": "虚构收入",
            "amount_cents": second,
            "content_fingerprint": _version(index * 7 + 2),
        },
    ]
    total = first + second
    if mode == "mismatch":
        total += 1_000
    payload = {"kind": "number_origin", "account_code": account_code}
    account = {"account_code": account_code, "total_cents": total, "rows": rows}
    if mode == "mismatch":
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": ["不一致", "勾稽"],
            "must_not_include": ["逐笔合计，共"],
            "citations": [row["voucher_id"] for row in rows],
            "blockers": [],
        }
    elif mode == "off_period":
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": ["不在本期", "跨期凭证"],
            "must_not_include": ["逐笔合计，共"],
            "citations": [row["voucher_id"] for row in rows],
            "blockers": [],
        }
    else:
        expected = {
            "mode": "answer",
            "conclusion_state": "confirmed",
            "must_include": [money(total), "2 张凭证"],
            "must_not_include": ["不一致", "跨期"],
            "citations": [row["voucher_id"] for row in rows],
            "blockers": [],
        }
    return _sample(
        index,
        category,
        "number_origin",
        book,
        period,
        company,
        firm,
        payload,
        {"account": account},
        expected,
    )


def _sample(index, category, question, book, period, company, firm, payload, reader, expected):
    sample = {
        "id": f"HO-{index:03d}",
        "category": category,
        "question": question,
        "book_id": book,
        "period": period,
        "company_id": company,
        "firm_id": firm,
        "allowed": True,
        "close": {"status": "no_run", "version": None, "blockers": []},
        "delivery": {"package": None, "opened": False},
        "settlement": {},
        "fees": {},
        "account": {},
        "rules": [],
    }
    sample.update(reader)
    sample["payload"] = payload
    sample["expected"] = expected
    return sample


def _refused(sample, index):
    """跨法人样本：当前岗位无权读取，期望是拒访而不是拼凑结论。"""
    sample = dict(sample)
    sample["allowed"] = False
    sample["category"] = "cross_entity_leak"
    sample["expected"] = {
        "mode": "refuse",
        "conclusion_state": "refused",
        "must_include": [],
        "must_not_include": ["100,000.00"],
        "citations": [],
        "blockers": [],
    }
    return sample


def build():
    samples = []
    index = 1
    # 1) 归因正确：凭据支持时才给结论。
    for _ in range(6):
        samples.append(_revenue_case(index, category="attribution_supported", fee_supported=True))
        index += 1
    for _ in range(5):
        samples.append(_delivery_case(index, category="attribution_supported", mode="blocked"))
        index += 1
    for _ in range(5):
        samples.append(_origin_case(index, category="attribution_supported", mode="complete"))
        index += 1
    # 2) 缺件拒答：缺凭据就不许下归因结论，读不到来源就弃答。
    for _ in range(5):
        samples.append(
            _revenue_case(index, category="missing_evidence_refusal", fee_supported=False)
        )
        index += 1
    for _ in range(1):
        samples.append(
            _revenue_case(
                index, category="missing_evidence_refusal", fee_supported=False, abstain=True
            )
        )
        index += 1
    for _ in range(5):
        samples.append(_delivery_case(index, category="missing_evidence_refusal", mode="no_run"))
        index += 1
    for _ in range(5):
        samples.append(_origin_case(index, category="missing_evidence_refusal", mode="mismatch"))
        index += 1
    # 3) 跨期拒用：凭据不在本期、凭证跨期，都不能当本期结论。
    for _ in range(5):
        samples.append(
            _revenue_case(
                index, category="cross_period_refusal", fee_supported=False, off_period=True
            )
        )
        index += 1
    for _ in range(1):
        samples.append(
            _revenue_case(index, category="cross_period_refusal", fee_supported=False, abstain=True)
        )
        index += 1
    for _ in range(5):
        samples.append(_delivery_case(index, category="cross_period_refusal", mode="cross_period"))
        index += 1
    for _ in range(5):
        samples.append(_origin_case(index, category="cross_period_refusal", mode="off_period"))
        index += 1
    # 4) 跨法人防泄漏：同一问题换到无权企业，必须拒访。
    for _ in range(6):
        samples.append(
            _refused(_revenue_case(index, category="cross_entity_leak", fee_supported=False), index)
        )
        index += 1
    for _ in range(5):
        samples.append(
            _refused(_delivery_case(index, category="cross_entity_leak", mode="blocked"), index)
        )
        index += 1
    for _ in range(5):
        samples.append(
            _refused(_origin_case(index, category="cross_entity_leak", mode="complete"), index)
        )
        index += 1
    return {
        "_notice": NOTICE,
        "schema": "plt08-holdout-v1",
        "synthetic": True,
        "samples": samples,
    }


def main():
    value = build()
    counts = {}
    for sample in value["samples"]:
        counts[sample["category"]] = counts.get(sample["category"], 0) + 1
    TARGET.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {TARGET} samples={len(value['samples'])} counts={counts}")


if __name__ == "__main__":
    main()
