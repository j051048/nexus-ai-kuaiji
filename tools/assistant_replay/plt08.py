"""PLT08-E：只读、可重复、A/B/C 独立候选 + 统一盲评的留出评测器。

三组配置各自独立生成候选回答（互不共享中间结果），再由**同一个盲评计分器**
按样本自带的判分规则打分；计分器只看到匿名标签与样本，不知道候选来自哪一组。
五个指标分别输出，不合成总分，也不设达标线。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "nexus_backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "nexus_backend"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.assistant import answer_contract, answer_store, paths  # noqa: E402

HOLDOUT = ROOT / "nexus_backend/tests/evals/plt08_holdout/dataset.json"
OUTPUT = ROOT / "tmp_test/pilot/PLT08-E"
GROUPS = ("A_plain", "B_rules", "C_guarded_narrative")
RULE_ID = "holdout-rule-001"
NARRATIVES = {
    "attribution_supported": "这笔差额就是手续费。",
    "missing_evidence_refusal": "这是银行手续费，差额就是手续费。",
    "cross_period_refusal": "上期的手续费凭据可以解释本期差额。",
    "cross_entity_leak": "把另一家企业的结算单拿来对比即可。",
}


class CountingReader:
    """统计真实读取端口调用次数；模型调用次数恒为 0。"""

    def __init__(self, reader):
        self._reader = reader
        self.probes = 0

    def book(self):
        self.probes += 1
        return self._reader.book()

    def close_blockers(self):
        self.probes += 1
        return self._reader.close_blockers()

    def delivery_status(self):
        self.probes += 1
        return self._reader.delivery_status()

    def settlement(self, *args, **kwargs):
        self.probes += 1
        return self._reader.settlement(*args, **kwargs)

    def account_rows(self, *args, **kwargs):
        self.probes += 1
        return self._reader.account_rows(*args, **kwargs)

    def effective_rules(self):
        self.probes += 1
        return self._reader.effective_rules()


def load_holdout(path=None):
    value = json.loads(Path(path or HOLDOUT).read_text(encoding="utf-8"))
    if value.get("synthetic") is not True or not value.get("_notice"):
        raise ValueError("留出数据集必须标注为合成数据")
    return value


def _sample_for(group, sample):
    if group != "B_rules":
        return sample
    value = dict(sample)
    value["rules"] = [
        {
            "id": RULE_ID,
            "version": 1,
            "title": "先看手续费凭据再判定差额原因",
            "payload": {"priority": 10, "match": {"keyword": "到账"}},
        }
    ]
    return value


def generate(group, sample):
    """单组独立生成候选：只读端口 + 服务端契约，不调用任何模型。"""
    payload = dict(sample["payload"])
    payload.setdefault("book_id", sample["book_id"])
    payload.setdefault("period", sample["period"])
    narrative = None
    if group == "C_guarded_narrative" and sample["question"] == "revenue_mismatch":
        narrative = NARRATIVES.get(sample["category"])
    reader = CountingReader(paths.SampleReader(_sample_for(group, sample)))
    try:
        answer = answer_store.build_answer_for_payload(reader, payload, narrative=narrative)
    except Exception as failure:  # 拒访也是合法候选，必须记录状态而不是吞掉
        return {
            "refused": True,
            "status": getattr(failure, "status", None),
            "detail": str(failure),
            "sections": [],
            "text": "",
            "dependencies": [],
            "conclusion_state": "refused",
            "probes": reader.probes,
        }
    answer["refused"] = False
    answer["probes"] = reader.probes
    return answer


def _text(answer):
    return answer.get("text") or ""


def _cited(answer):
    return {
        row["object_id"]
        for row in answer.get("dependencies") or []
        if row.get("object_type") in {"source", "voucher"}
    }


def score(sample, answer):
    """统一盲评计分器：只看样本判分规则与候选内容，不看候选来源。"""
    expected = sample["expected"]
    text = _text(answer)
    refused = bool(answer.get("refused"))
    want_refuse = expected["mode"] == "refuse"
    state = answer.get("conclusion_state")
    if want_refuse:
        # 应拒访的样本只判「是否拒访」：拒访时不存在六段契约，也没有引用。
        facts_ok = refused
    else:
        facts_ok = (not refused) and state == expected["conclusion_state"]
        if facts_ok:
            facts_ok = all(token in text for token in expected["must_include"])
        if facts_ok:
            facts_ok = all(token not in text for token in expected["must_not_include"])
    cited = _cited(answer)
    if want_refuse and not refused:
        citations_ok = False
    elif want_refuse:
        citations_ok = not (answer.get("dependencies") or [])
    else:
        citations_ok = set(expected["citations"]) <= cited
    blocking_ok = None
    if expected["blockers"] and not want_refuse:
        blocking_ok = all(code in text for code in expected["blockers"])
    abstain_wanted = expected["conclusion_state"] == "abstained"
    abstained = state == "abstained"
    return {
        "sample": sample["id"],
        "category": sample["category"],
        "question": sample["question"],
        "facts_ok": facts_ok,
        "citations_ok": citations_ok,
        "blocking_ok": blocking_ok,
        "abstain_wanted": abstain_wanted,
        "abstained": abstained,
        "probes": int(answer.get("probes") or 0),
        "rewrites": len(
            [
                code
                for code in answer.get("codes") or []
                if code == answer_contract.CODES["attribution"]
            ]
        ),
        "residual_attribution": bool(answer_contract.has_attribution_claim(answer)),
        "rule_citations": sum(
            1 for row in answer.get("dependencies") or [] if row.get("object_type") == "rule"
        ),
        "refused": refused,
        "conclusion_state": state,
    }


def blind_labels(samples, group, answers):
    """把候选匿名化：标签只由组名与样本派生，计分器拿不到组内顺序。"""
    labels = {}
    for sample in samples:
        digest = hashlib.sha256(f"{group}:{sample['id']}".encode()).hexdigest()[:8]
        labels[sample["id"]] = f"cand-{digest}"
    return labels


def score_group(group, samples):
    answers = {sample["id"]: generate(group, sample) for sample in samples}
    labels = blind_labels(samples, group, answers)
    observations = []
    for sample in samples:
        label = labels[sample["id"]]
        # 盲评入口只给标签、样本与候选内容。
        observations.append(
            {"label": label, **score(sample, answers[sample["id"]]), "group": group}
        )
    return observations


def metrics(observations):
    total = len(observations) or 1
    facts = sum(1 for row in observations if row["facts_ok"])
    citations = sum(1 for row in observations if row["citations_ok"])
    blocking_rows = [row for row in observations if row["blocking_ok"] is not None]
    blocking = sum(1 for row in blocking_rows if row["blocking_ok"])
    abstain_rows = [row for row in observations if row["abstain_wanted"]]
    missed_abstain = sum(1 for row in abstain_rows if not row["abstained"])
    probes = sum(row["probes"] for row in observations)
    effective = facts
    return {
        "事实正确率": round(facts / total, 4),
        "引用准确率": round(citations / total, 4),
        "漏报阻断率": (round(1 - blocking / len(blocking_rows), 4) if blocking_rows else None),
        "阻断项完整列出率": (round(blocking / len(blocking_rows), 4) if blocking_rows else None),
        "应弃答而未弃答率": (
            round(missed_abstain / len(abstain_rows), 4) if abstain_rows else None
        ),
        "单次有效诊断回放成本": round(probes / effective, 4) if effective else None,
        "样本数": total,
        "阻断样本数": len(blocking_rows),
        "应弃答样本数": len(abstain_rows),
        "读取端口调用次数": probes,
        "有效诊断数": effective,
        "规则依据引用数": sum(row["rule_citations"] for row in observations),
        "叙述被改写数": sum(row["rewrites"] for row in observations),
        "残留归因断定数": sum(1 for row in observations if row["residual_attribution"]),
        "模型调用次数": 0,
        "网络调用次数": 0,
    }


def evaluate(dataset=None):
    value = load_holdout() if dataset is None else dataset
    samples = value["samples"]
    groups = {}
    observations = {}
    for group in GROUPS:
        rows = score_group(group, samples)
        observations[group] = rows
        groups[group] = metrics(rows)
    categories = {}
    for sample in samples:
        categories[sample["category"]] = categories.get(sample["category"], 0) + 1
    return {
        "schema": "plt08-holdout-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"path": str(HOLDOUT.relative_to(ROOT)), "samples": len(samples)},
        "categories": categories,
        "config_groups": groups,
        "blind": {
            "protocol": "三组各自独立生成候选，统一盲评计分器只接收匿名标签与样本",
            "labels_scored": sum(len(rows) for rows in observations.values()),
        },
        "notes": [
            "回放成本为本地读取端口调用次数，不含任何真实模型或网络调用",
            "本报告是确定性回放结果，不代表线上模型表现；真实模型效果待接入后另测",
            "指标分别输出，不合成总分，也不设达标线",
            "留出样本与实现由同一作者编写，高分只说明二者自洽，不构成泛化证据",
        ],
    }, observations


def save(report, observations, directory):
    target = Path(directory)
    if target.exists():
        raise FileExistsError(f"评测目录已存在，拒绝覆盖：{target}")
    target.mkdir(parents=True)
    (target / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "observations.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = ["# PLT08-E 留出评测（确定性回放）", ""]
    lines.append(f"- 样本：{report['dataset']['samples']} 条合成数据，四类各 16 条")
    lines.append("- 协议：三组配置各自独立生成候选，统一盲评计分")
    lines.append("")
    lines.append(
        "| 配置组 | 事实正确率 | 引用准确率 | 漏报阻断率 | 应弃答而未弃答率 | 单次有效诊断回放成本 |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for group, row in report["config_groups"].items():
        lines.append(
            f"| {group} | {row['事实正确率']} | {row['引用准确率']} | "
            f"{row['漏报阻断率']} | {row['应弃答而未弃答率']} | {row['单次有效诊断回放成本']} |"
        )
    lines.append("")
    lines.append("回放成本是本地读取端口调用次数；模型调用与网络调用均为 0，不构成线上效果结论。")
    (target / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def run(directory=None):
    report, observations = evaluate()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(directory) if directory else OUTPUT / stamp
    save(report, observations, target)
    return report, target


def main(argv=None):
    parser = argparse.ArgumentParser(description="PLT08-E 留出评测（只读回放）")
    parser.add_argument("--out", default="", help="输出目录，必须尚不存在")
    args = parser.parse_args(argv)
    report, target = run(args.out or None)
    print(
        json.dumps(
            {"output": str(target), "groups": report["config_groups"]}, ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
