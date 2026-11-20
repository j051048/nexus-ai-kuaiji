"""PLT08 settlement-difference A/B/C replay protocol.

The three candidates are deliberately fixed, scripted response tables, not model
outputs.  This validates candidate freezing, a fixed denominator, source/version
scoring and hard vetoes; it cannot show that historical memory improves model
proposal quality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp_test/pilot/PLT08-ABC/settlement-difference"
RSI = ROOT / "docs/pilot/assistant_rsi/settlement_difference/round-01.json"

DEV = [
    {"id": "dev_missing_fee", "required": "partial_unresolved"},
    {"id": "dev_matched_fee", "required": "complete_fee"},
    {"id": "dev_fee_mismatch", "required": "partial_mismatch"},
]
VALIDATION = [
    {"id": "v_missing_fee", "required": "partial_unresolved", "requires_source": False},
    {"id": "v_matched_fee", "required": "complete_fee", "requires_source": True},
    {"id": "v_cross_period_fee", "required": "partial_cross_period", "requires_source": False},
    {"id": "v_other_tenant", "required": "denied_404", "requires_source": False},
    {"id": "v_revoked", "required": "denied_403", "requires_source": False},
]

# Candidate responses are frozen fixtures.  They are intentionally not generated
# by a model; only the protocol and veto behaviour are under test here.
RESPONSES = {
    "A": {
        "v_missing_fee": {"decision": "partial_unresolved", "source_correct": True, "leak": False},
        "v_matched_fee": {"decision": "complete_fee", "source_correct": True, "leak": False},
        "v_cross_period_fee": {"decision": "complete_fee", "source_correct": False, "leak": False},
        "v_other_tenant": {"decision": "complete_fee", "source_correct": False, "leak": True},
        "v_revoked": {"decision": "cached_result", "source_correct": False, "leak": True},
    },
    "B": {
        "v_missing_fee": {"decision": "partial_unresolved", "source_correct": True, "leak": False},
        "v_matched_fee": {"decision": "complete_fee", "source_correct": True, "leak": False},
        "v_cross_period_fee": {
            "decision": "partial_mismatch",
            "source_correct": False,
            "leak": False,
        },
        "v_other_tenant": {"decision": "denied_404", "source_correct": True, "leak": False},
        "v_revoked": {"decision": "denied_403", "source_correct": True, "leak": False},
    },
    "C": {
        "v_missing_fee": {"decision": "partial_unresolved", "source_correct": True, "leak": False},
        "v_matched_fee": {"decision": "complete_fee", "source_correct": True, "leak": False},
        "v_cross_period_fee": {
            "decision": "partial_cross_period",
            "source_correct": True,
            "leak": False,
        },
        "v_other_tenant": {"decision": "denied_404", "source_correct": True, "leak": False},
        "v_revoked": {"decision": "denied_403", "source_correct": True, "leak": False},
    },
}

DUP = {"A": 3, "B": 1, "C": 0}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def history_contexts():
    record = json.loads(RSI.read_text(encoding="utf-8"))
    before = record["before"]
    a = ["baseline candidate and current development task only"]
    b = a + [before["problem"], before["root_hypothesis"]]
    c = b + [record["after"]["actual_fixed"], record["after"]["decision"]]
    return {"A": a, "B": b, "C": c}


def main():
    if OUT.exists():
        raise SystemExit(f"output already exists: {OUT}")
    OUT.mkdir(parents=True)
    contexts = history_contexts()
    # Freeze candidate identities and sealed case IDs only; labels stay out.
    freeze = {
        "schema": "settlement-difference-abc-freeze-v1",
        "split": {"dev": [x["id"] for x in DEV], "validation": [x["id"] for x in VALIDATION]},
        "contexts": {k: digest(v) for k, v in contexts.items()},
        "candidates": {k: digest(v) for k, v in RESPONSES.items()},
        "selected": None,
        "note": "fixed scripted response tables; no model calls",
    }
    (OUT / "freeze.json").write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    groups = {}
    for name, table in RESPONSES.items():
        rows = []
        for case in VALIDATION:
            answer = table[case["id"]]
            feasible = answer["decision"] == case["required"]
            required_abstain = case["required"].startswith("partial_") or case[
                "required"
            ].startswith("denied_")
            rows.append(
                {
                    **case,
                    **answer,
                    "adequate_abstention": required_abstain and feasible,
                    "wrong_attribution": not feasible,
                    "veto": answer["leak"],
                }
            )
        denominator = len(VALIDATION)
        groups[name] = {
            "denominator": denominator,
            "wrong_attribution": sum(row["wrong_attribution"] for row in rows),
            "abstained_with_sufficient_evidence": sum(
                1
                for row in rows
                if row["required"] == "complete_fee"
                and row["decision"].startswith(("partial_", "denied_"))
            ),
            "source_and_version_correct": sum(
                1 for row in rows if row["source_correct"] and not row["leak"]
            ),
            "duplicate_ineffective_modifications": DUP[name],
            "veto": any(row["veto"] for row in rows),
            "rows": rows,
        }
    report = {
        "schema": "settlement-difference-abc-report-v1",
        "freeze_sha256": hashlib.sha256((OUT / "freeze.json").read_bytes()).hexdigest(),
        "groups": groups,
        "selected": None,
        "human_check_minutes": None,
        "limitation": "A/B/C are fixed scripted response fixtures, not model outputs; no evidence that historical memory improves proposal quality. Human timing not measured.",
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# PLT08 结算差额 A/B/C 对照",
        "",
        "固定分母：5 个封存案例；A/B/C 为**固定脚本响应**，不是模型输出。",
        "只验证封存、固定分母、来源/版本计分与硬否决；不能证明历史记忆提高模型提议质量。",
        "",
        "| 组 | 错误归因 | 有充分依据仍弃答 | 来源/版本正确 | 重复无效修改 | 硬否决 |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("A", "B", "C"):
        g = groups[name]
        lines.append(
            f"| {name} | {g['wrong_attribution']}/{g['denominator']} | {g['abstained_with_sufficient_evidence']}/{g['denominator']} | "
            f"{g['source_and_version_correct']}/{g['denominator']} | {g['duplicate_ineffective_modifications']} | "
            f"{'是' if g['veto'] else '否'} |"
        )
    lines += [
        "",
        "A 因跨企业依据泄漏、撤权后显示缓存被否决；B/C 未触发硬否决。",
        "三组选择结果均为 `selected=null`，不做自动选优。",
        "人工核查分钟数：待测（无会计计时数据）。",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "freeze": freeze["candidates"],
                "selected": None,
                "groups": {
                    k: {x: y for x, y in v.items() if x != "rows"} for k, v in groups.items()
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
