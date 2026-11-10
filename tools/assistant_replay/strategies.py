"""Bounded execution comparison over H01 storage / AG05 task-gold adapter.

This runner compares two fixed policies; it never tunes on holdout or deploys a
winner. Durations are local observations, not model quality or capacity claims.
"""

from pathlib import Path
import math
import os
import time

from .investigation import load_dataset, observe, evaluate
from .baseline import candidate_identity, digest
from tools.ocr_replay import replay

POLICIES = {
    "serial": {"parallelism": 1, "max_reads": 3},
    "parallel2": {"parallelism": 2, "max_reads": 3},
}


def observe_strategy(dataset, candidate, strategy, *args, **hooks):
    policy = POLICIES.get(strategy)
    if policy is None or os.getenv("ACCOUNTING_ASSISTANT_READ_PARALLELISM", "1") != str(
        policy["parallelism"]
    ):
        raise ValueError("Execution policy differs from configured strategy")
    start = time.monotonic()
    row = observe(dataset, candidate, *args, **hooks)
    elapsed = round((time.monotonic() - start) * 1000, 3)
    execution = row["result"].get("execution")
    if (strategy == "parallel2" and (not execution or execution["parallelism"] != 2)) or (
        strategy == "serial" and execution
    ):
        raise ValueError("Executed policy differs from requested strategy")
    row["request"].update(strategy=strategy, execution_policy=policy)
    row["request_sha256"] = digest(row["request"])
    return {
        **row,
        "strategy": strategy,
        "measurement": {"elapsed_ms": elapsed, "model_calls": 0, "cost_cny": None},
    }


def compare(dataset, observations, candidate):
    if set(observations) != set(POLICIES):
        raise ValueError("Both fixed strategies required")
    reports = {}
    for strategy, rows in observations.items():
        for row in rows:
            if (
                row["strategy"] != strategy
                or row["request"].get("strategy") != strategy
                or row["request"].get("execution_policy") != POLICIES[strategy]
            ):
                raise ValueError("Strategy identity changed")
            measurement = row["measurement"]
            elapsed = measurement["elapsed_ms"]
            if (
                type(elapsed) not in (int, float)
                or not math.isfinite(elapsed)
                or elapsed < 0
                or measurement["model_calls"] != 0
                or measurement["cost_cny"] is not None
            ):
                raise ValueError("Invalid local measurement")
        reports[strategy] = evaluate(dataset, rows, candidate)
    indexed = {s: {r["sample_id"]: r for r in rows} for s, rows in observations.items()}
    comparisons = []
    for sample in dataset["samples"]:
        sid = sample["id"]
        left, right = (indexed[s].get(sid) for s in POLICIES)
        same = False
        if left and right:
            # No timestamps, thread order or prose in the semantic oracle.
            keys = ("book_id", "period", "status", "coverage", "findings", "missing", "evidence")
            same = all(left["result"].get(k) == right["result"].get(k) for k in keys)
            scope = ("firm_id", "company_id", "book_id", "period", "user_id")
            same = same and all(left["request"][k] == right["request"][k] for k in scope)
        comparisons.append(
            {
                "sample_id": sid,
                "split": sample["split"],
                "equivalent": same,
                "elapsed_ms": {
                    s: indexed[s][sid]["measurement"]["elapsed_ms"] if sid in indexed[s] else None
                    for s in POLICIES
                },
            }
        )
    passed = all(r["status"] == "passed_offline" for r in reports.values()) and all(
        r["equivalent"] for r in comparisons
    )
    return {
        "status": "equivalent_offline" if passed else "regression",
        "candidate": candidate,
        "manifest_sha256": dataset["sha256"],
        "denominator": len(dataset["samples"]),
        "strategies": reports,
        "samples": comparisons,
        "selected_strategy": None,
        "model_cost_cny": None,
        "model_calls": 0,
        "scope": "虚构数据、确定性领域读取；holdout 仅验收，无真实模型质量、费用或生产容量结论",
    }


def save_comparison(report, observations, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    replay._atomic_json(directory / "observations.json", observations)
    replay._atomic_json(
        directory / "report.json",
        {**report, "observations_sha256": replay.sha256_path(directory / "observations.json")},
    )
    lines = [
        "# 任务级串行与有界并行对拍",
        "",
        f"结论：{report['status']}；固定分母 {report['denominator']}",
        report["scope"],
        "不选优、不自动启用。耗时包含本地授权和审计开销。",
        "",
        "| 样本 | 分组 | 事实一致 | 串行 ms | 双分支 ms |",
        "|---|---|---|---|---|",
    ]
    for row in report["samples"]:
        lines.append(
            f"| {row['sample_id']} | {row['split']} | {row['equivalent']} | {row['elapsed_ms']['serial']} | {row['elapsed_ms']['parallel2']} |"
        )
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
