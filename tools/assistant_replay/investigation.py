"""Thin task adapter: H01 persistence, AG05 manifest, existing candidate binding.

No ranking, provider, model, production database or publication API. Tests supply
isolated services and fault injection; the adapter invokes the real domain reader.
"""

from pathlib import Path
import json

from tools.assistant_replay.baseline import candidate_identity, digest
from tools.ocr_replay import replay
from tools.ai_inference_lab.examples import validate_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "nexus_backend/tests/fixtures/assistant_task/dcp_investigation.json"


def load_dataset():
    value = replay.read_json(MANIFEST)
    ids = [s["id"] for s in value["samples"]]
    if len(ids) != 6 or len(set(ids)) != 6:
        raise ValueError("Frozen six-case manifest required")
    for split in ("dev", "holdout"):
        manifest = {
            k: value[k]
            for k in ("dataset_id", "policy_version", "prompt_version", "context_version")
        }
        manifest.update(
            split=split, sample_ids=[s["id"] for s in value["samples"] if s["split"] == split]
        )
        validate_manifest(manifest, manifest)
    return {**value, "sha256": replay.sha256_path(MANIFEST)}


def observe(dataset, candidate, sample_id, service, actor, book_id, period, **hooks):
    from app.assistant.investigation import investigate
    from app.accounting.domain import AccountingError

    if dataset != load_dataset() or candidate != candidate_identity():
        raise ValueError("Candidate or input changed before execution")
    sample = next((s for s in dataset["samples"] if s["id"] == sample_id), None)
    if sample is None:
        raise ValueError("Unknown sample")
    try:
        with service.db.transaction(actor=actor) as conn:
            company_id = service.access.book(conn, actor, book_id, "read")["company_id"]
    except AccountingError:
        company_id = None
    request = {
        "sample_id": sample_id,
        "firm_id": actor.firm_id,
        "user_id": actor.user_id,
        "company_id": company_id,
        "book_id": book_id,
        "period": period,
        "candidate": candidate["fingerprint"],
        "manifest": dataset["sha256"],
        "policy": dataset["policy_version"],
    }
    result = investigate(service, actor, book_id, period, **hooks)
    return {
        "sample_id": sample_id,
        "request": request,
        "request_sha256": digest(request),
        "result": result,
        "result_sha256": digest(result),
    }


def evaluate(dataset, observations, candidate):
    from app.assistant.investigation import TOOLS, evidence_fingerprint

    if dataset != load_dataset() or candidate != candidate_identity():
        raise ValueError("Candidate or frozen input changed")
    expected = {s["id"]: s for s in dataset["samples"]}
    indexed = {}
    for row in observations:
        sid = row["sample_id"]
        if sid not in expected or sid in indexed:
            raise ValueError("Unexpected or duplicate sample")
        request = row["request"]
        if (
            digest(request) != row["request_sha256"]
            or digest(row["result"]) != row["result_sha256"]
            or request["sample_id"] != sid
            or request["candidate"] != candidate["fingerprint"]
            or request["manifest"] != dataset["sha256"]
        ):
            raise ValueError("Request/result binding changed")
        indexed[sid] = row
    samples = []
    for sid, gold in expected.items():
        row = indexed.get(sid)
        failures = []
        if row is None:
            failures.append("missing")
        else:
            result, request = row["result"], row["request"]
            facts, missing, steps = (
                result.get("findings"),
                result.get("missing"),
                result.get("steps"),
            )
            try:
                names = list(facts)
                if (
                    not isinstance(facts, dict)
                    or not isinstance(missing, list)
                    or len(set(names + missing)) != len(TOOLS)
                    or len(names + missing) != len(TOOLS)
                    or set(names + missing) != set(TOOLS)
                    or result.get("coverage") != {"read": len(names), "of": len(TOOLS)}
                    or (result.get("status") == "complete" and missing)
                ):
                    failures.append("evidence_coverage")
                if not isinstance(steps, list) or len(steps) > len(TOOLS):
                    failures.append("execution_steps")
                else:
                    step_names = [step["tool"] for step in steps]
                    if (
                        step_names != list(TOOLS[: len(steps)])
                        or any(type(step.get("ok")) is not bool for step in steps)
                        or any(not any(s["tool"] == n and s["ok"] for s in steps) for n in names)
                    ):
                        failures.append("execution_steps")
                evidence = result.get("evidence")
                if names:
                    if (
                        evidence["firm_id"] != request["firm_id"]
                        or evidence["company_id"] != request["company_id"]
                        or evidence["fingerprint"] != evidence_fingerprint(result)
                    ):
                        failures.append("evidence_identity")
                elif evidence is not None:
                    failures.append("unsafe_stale_evidence")
            except (KeyError, TypeError, ValueError):
                failures.append("evidence_invalid")
            if (
                result.get("book_id") != request["book_id"]
                or result.get("period") != request["period"]
            ):
                failures.append("scope")
            if result.get("status") != gold["expected_status"]:
                failures.append("status")
            if result.get("coverage") != {"read": gold["expected_read"], "of": 3}:
                failures.append("coverage")
            if result.get("model_calls") != 0:
                failures.append("unexpected_model")
            if gold["expected_read"] == 0 and (result.get("findings") or result.get("evidence")):
                failures.append("unsafe_stale_evidence")
            if gold["notification_state"]:
                states = (
                    result.get("findings", {}).get("delivery_status", {}).get("notifications", {})
                )
                if states.get(gold["notification_state"], 0) < 1:
                    failures.append("unknown_not_preserved")
        samples.append(
            {
                "sample_id": sid,
                "split": gold["split"],
                "passed": not failures,
                "failures": failures,
                "request_sha256": row["request_sha256"] if row else None,
            }
        )
    return {
        "status": "passed_offline" if all(s["passed"] for s in samples) else "regression",
        "candidate": candidate,
        "manifest_sha256": dataset["sha256"],
        "denominator": len(expected),
        "passed": sum(s["passed"] for s in samples),
        "samples": samples,
        "model_calls": 0,
        "splits": {
            split: {
                "passed": sum(s["passed"] for s in samples if s["split"] == split),
                "denominator": sum(s["split"] == split for s in samples),
            }
            for split in ("dev", "holdout")
        },
        "scope": "synthetic domain workflow; no model quality or production claim",
    }


def save(report, observations, directory):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Use a new immutable evidence directory")
    directory.mkdir(parents=True)
    replay._atomic_json(directory / "observations.json", observations)
    replay._atomic_json(
        directory / "report.json",
        {**report, "observations_sha256": replay.sha256_path(directory / "observations.json")},
    )
    lines = [
        "# DCP 受限调查离线回放",
        "",
        f"结论：{report['status']}；固定分母 {report['passed']}/{report['denominator']}",
        "无模型调用；holdout 只验收、不选优。不代表自主 Agent 或真实模型效果。",
        "",
        "| 样本 | 分组 | 通过 | 缺口 |",
        "|---|---|---|---|",
    ]
    for s in report["samples"]:
        lines.append(
            f"| {s['sample_id']} | {s['split']} | {s['passed']} | {', '.join(s['failures'])} |"
        )
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
