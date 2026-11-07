"""Generate IMP07 deterministic baseline/candidate reports.

Rows are produced by a local deterministic policy, not by a model. The report
never claims real-model quality. It exists to prove that the scorer distinguishes
a clean replay, a field regression and an unknown-heavy run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.imp07_eval import (
    load_corpus,
    save_report,
    score,
    result_identity,
    execution_input,
    evidence_for,
    corpus_digest,
    EvaluationError,
)  # noqa: E402
from tools.ai_inference_lab.rsi.snapshots import atomic_create, canonical, digest  # noqa: E402


CORPUS = ROOT / "nexus_backend/tests/fixtures/imp07_eval/corpus.json"


def fingerprint(version: str, policy="safe") -> str:
    code = b"".join(
        (ROOT / name).read_bytes()
        for name in (
            "scripts/imp07_run_eval.py",
            "tools/imp07_eval/__init__.py",
            "tools/imp07_eval/costing.py",
            "tools/ai_inference_lab/examples.py",
            "tools/ocr_replay/replay.py",
        )
    )
    return hashlib.sha256(
        code + canonical({"version": version, "policy": policy, "schema": "imp07-result-v2"})
    ).hexdigest()


LABELS = {
    "发票号码": "invoice_number",
    "开票日期": "issued_on",
    "购买方名称": "buyer_name",
    "购买方纳税人识别号": "buyer_tax_id",
    "销售方名称": "seller_name",
    "销售方纳税人识别号": "seller_tax_id",
    "价税合计（小写）": "gross_amount",
    "金额": "net_amount",
    "税额": "tax_amount",
    "币种": "currency",
}


def parse_invoice(text: str) -> dict:
    """Deterministic OCR-text parser. It never reads gold labels."""
    fields = {}
    for raw in text.splitlines():
        line = raw.replace("∶", "：").strip()
        line = re.sub(r"\s*：\s*", "：", line)
        for label, key in LABELS.items():
            prefix = label + "："
            if line.startswith(prefix):
                fields[key] = line[len(prefix) :].strip()
                break
    return fields


def execute(request, *, policy="safe"):
    """Executor receives no gold, split membership, risk or abstention label."""
    text = request["ocr_text"]
    fields = parse_invoice(text)
    if policy == "strip_zeros" and fields.get("invoice_number"):
        fields["invoice_number"] = fields["invoice_number"].lstrip("0") or "0"
    return {
        "status": "succeeded" if len(fields) == len(LABELS) else "unknown",
        "fields": fields,
        "review_required": "人工核对提示" in text,
        "evidence": evidence_for(text, fields),
        "cost_cny": "0",
        "model_calls": 0,
    }


def rows_for(corpus, version: str, *, policy="safe"):
    if policy not in {"safe", "strip_zeros"}:
        raise EvaluationError("policy_not_allowed")
    candidate = {"version": version, "fingerprint": fingerprint(version, policy)}
    return [
        {
            **result_identity(corpus, item, candidate),
            **execute(execution_input(corpus, item), policy=policy),
        }
        for item in corpus["items"]
    ]


def _split(corpus, split):
    items = [row for row in corpus["items"] if row["split"] == split]
    sources = [row for row in corpus["sources"] if row["split"] == split]
    result = {
        **corpus,
        "items": items,
        "sources": sources,
        "source_count": len(sources),
        "variant_count": len(items),
        "item_index": {row["item_id"]: row for row in items},
        "source_index": {row["source_id"]: row for row in sources},
    }
    result["_frozen_sha256"] = corpus_digest(result)
    return result


def run_comparison(corpus, policies, directory):
    """Execute only development rows until an immutable winner is frozen.

    The runner owns policies, writes raw results and reuses the AG05 judge.
    Callers cannot supply a gold-aware executor, a score or result identities.
    """
    if not 1 <= len(policies) <= 3 or len(set(policies)) != len(policies):
        raise EvaluationError("candidate_limit")
    if any(p not in {"safe", "strip_zeros"} for p in policies):
        raise EvaluationError("policy_not_allowed")
    if corpus_digest(corpus) != corpus["_frozen_sha256"]:
        raise EvaluationError("corpus_changed")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=False)
    dev = _split(corpus, "dev")
    base = {"version": "imp07-baseline-v2", "fingerprint": fingerprint("imp07-baseline-v2")}
    base_dev = rows_for(dev, base["version"])
    candidates = []
    for index, policy in enumerate(policies):
        version = f"imp07-policy-{policy}-v2"
        config = {"version": version, "fingerprint": fingerprint(version, policy), "policy": policy}
        rows = rows_for(dev, version, policy=policy)
        atomic_create(output / f"dev-{index}.json", canonical({"candidate": config, "rows": rows}))
        report = score(dev, rows, candidate=config, baseline_rows=base_dev, baseline_candidate=base)
        candidates.append((report, config, rows))
    eligible = [c for c in candidates if c[0]["acceptance"] == "accepted_offline"]
    if not eligible:
        atomic_create(output / "selection.json", canonical({"status": "no_eligible_candidate"}))
        return {"acceptance": "rejected", "denominator": len(corpus["items"])}
    selected = sorted(eligible, key=lambda c: (-c[0]["success_count"], c[1]["fingerprint"]))[0]
    frozen = {
        "schema": "imp07-selection-v1",
        "dataset_sha256": corpus_digest(corpus),
        "selected": selected[1],
        "selection_ids": [i["item_id"] for i in dev["items"]],
        "baseline": base,
        "dev_rows_sha256": digest(selected[2]),
    }
    atomic_create(output / "frozen.json", canonical(frozen))
    holdout = _split(corpus, "holdout")
    if selected[1]["fingerprint"] != fingerprint(selected[1]["version"], selected[1]["policy"]):
        raise EvaluationError("executor_changed")
    rows = selected[2] + rows_for(holdout, selected[1]["version"], policy=selected[1]["policy"])
    baseline = base_dev + rows_for(holdout, base["version"])
    atomic_create(
        output / "execution.json",
        canonical(
            {"frozen_sha256": digest(frozen), "candidate_rows": rows, "baseline_rows": baseline}
        ),
    )
    result = score(
        corpus,
        rows,
        candidate=selected[1],
        baseline_rows=baseline,
        baseline_candidate=base,
        selection_ids=frozen["selection_ids"],
    )
    save_report(result, output / "acceptance")
    return result


def unknown_rows(corpus, version: str):
    candidate = {"version": version, "fingerprint": fingerprint(version)}
    return [
        {
            **result_identity(corpus, item, candidate),
            "status": "unknown",
            "fields": {},
            "cost_cny": None,
        }
        for item in corpus["items"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    corpus = load_corpus(CORPUS)
    baseline_candidate = {
        "version": "imp07-baseline-v1",
        "fingerprint": fingerprint("imp07-baseline-v1"),
    }
    good_candidate = {"version": "imp07-good-v1", "fingerprint": fingerprint("imp07-good-v1")}
    bad_candidate = {
        "version": "imp07-bad-v1",
        "fingerprint": fingerprint("imp07-bad-v1", "strip_zeros"),
    }
    unknown_candidate = {
        "version": "imp07-unknown-v1",
        "fingerprint": fingerprint("imp07-unknown-v1"),
    }

    # Demonstrations are development-only. The holdout may first execute in
    # run_comparison, after its immutable selected candidate has been saved.
    demonstration = _split(corpus, "dev")
    baseline_rows = rows_for(demonstration, baseline_candidate["version"])
    good_rows = rows_for(demonstration, good_candidate["version"])
    bad_rows = rows_for(demonstration, bad_candidate["version"], policy="strip_zeros")
    unknown = unknown_rows(demonstration, unknown_candidate["version"])

    reports = {
        "good": score(
            demonstration,
            good_rows,
            candidate=good_candidate,
            baseline_rows=baseline_rows,
            baseline_candidate=baseline_candidate,
        ),
        "regressed": score(
            demonstration,
            bad_rows,
            candidate=bad_candidate,
            baseline_rows=baseline_rows,
            baseline_candidate=baseline_candidate,
        ),
        "unknown": score(demonstration, unknown, candidate=unknown_candidate),
    }
    for name, report in reports.items():
        save_report(report, output / name)
    run_comparison(corpus, ["strip_zeros", "safe"], output / "selection")
    (output / "summary.json").write_text(
        json.dumps(
            {
                "corpus": {
                    "path": str(CORPUS.relative_to(ROOT)),
                    "dataset_id": corpus["dataset_id"],
                    "dataset_sha256": corpus_digest(corpus),
                    "sources": corpus["source_count"],
                    "variants": corpus["variant_count"],
                    "dev_sources": corpus["source_count"] - 10,
                    "holdout_sources": 10,
                },
                "demonstration_split": "dev",
                "real_model_evaluated": False,
                "production_ready": False,
                "reports": {
                    name: {
                        "acceptance": report["acceptance"],
                        "success": report["success_count"],
                        "denominator": report["denominator"],
                        "missing": report["missing_count"],
                        "unknown": report["unknown_count"],
                        "regressions": len(report["regressions"]),
                        "safety_failures": report["safety_failure_count"],
                    }
                    for name, report in reports.items()
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {name: report["acceptance"] for name, report in reports.items()}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
