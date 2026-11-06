"""Generate IMP07 deterministic baseline/candidate reports.

Rows are produced by a local deterministic policy, not by a model. The report
never claims real-model quality. It exists to prove that the scorer distinguishes
a clean replay, a field regression and an unknown-heavy run.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.imp07_eval import load_corpus, save_report, score  # noqa: E402


CORPUS = ROOT / "nexus_backend/tests/fixtures/imp07_eval/corpus.json"
OUT = ROOT / "tmp_test/imp/imp-20261106-a/imp07"


def fingerprint(policy: str) -> str:
    code = (ROOT / "tools/imp07_eval/__init__.py").read_bytes()
    return hashlib.sha256(code + policy.encode("utf-8")).hexdigest()


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


def rows_for(corpus, version: str):
    rows = []
    for item in corpus["items"]:
        if item["abstention_expected"]:
            rows.append(
                {
                    "item_id": item["item_id"],
                    "candidate_version": version,
                    "status": "abstained",
                    "fields": {},
                    "review_required": item["risk_required"],
                    "cost_cny": "0",
                }
            )
            continue
        fields = parse_invoice(item["ocr_text"])
        status = "succeeded" if len(fields) == len(LABELS) else "unknown"
        rows.append(
            {
                "item_id": item["item_id"],
                "candidate_version": version,
                "status": status,
                "fields": fields,
                "review_required": item["risk_required"],
                "cost_cny": "0.01",
            }
        )
    return rows


def unknown_rows(corpus, version: str):
    return [
        {
            "item_id": item["item_id"],
            "candidate_version": version,
            "status": "unknown",
            "fields": {},
            "cost_cny": None,
        }
        for item in corpus["items"]
    ]


def main() -> int:
    corpus = load_corpus(CORPUS)
    baseline_candidate = {"version": "imp07-baseline-v1", "fingerprint": fingerprint("baseline")}
    good_candidate = {"version": "imp07-good-v1", "fingerprint": fingerprint("good")}
    bad_candidate = {"version": "imp07-bad-v1", "fingerprint": fingerprint("bad")}
    unknown_candidate = {"version": "imp07-unknown-v1", "fingerprint": fingerprint("unknown")}

    baseline_rows = rows_for(corpus, baseline_candidate["version"])
    good_rows = rows_for(corpus, good_candidate["version"])
    bad_rows = [deepcopy(row) for row in good_rows]
    for row in bad_rows:
        row["candidate_version"] = bad_candidate["version"]
    for row in bad_rows:
        if row["status"] != "succeeded":
            continue
        key = next((key for key in row["fields"] if "invoice" in key), None)
        if key and row["fields"][key]:
            row["fields"][key] = row["fields"][key].lstrip("0") or "0"
            break
    unknown = unknown_rows(corpus, unknown_candidate["version"])

    reports = {
        "good": score(
            corpus,
            good_rows,
            candidate=good_candidate,
            baseline_rows=baseline_rows,
            baseline_candidate=baseline_candidate,
        ),
        "regressed": score(
            corpus,
            bad_rows,
            candidate=bad_candidate,
            baseline_rows=baseline_rows,
            baseline_candidate=baseline_candidate,
        ),
        "unknown": score(corpus, unknown, candidate=unknown_candidate),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, report in reports.items():
        save_report(report, OUT / name)
    (OUT / "summary.json").write_text(
        json.dumps(
            {
                "corpus": {
                    "path": str(CORPUS.relative_to(ROOT)),
                    "dataset_id": corpus["dataset_id"],
                    "dataset_sha256": reports["good"]["dataset_sha256"],
                    "sources": corpus["source_count"],
                    "variants": corpus["variant_count"],
                    "dev_sources": corpus["source_count"] - 10,
                    "holdout_sources": 10,
                },
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
    print(json.dumps({name: report["acceptance"] for name, report in reports.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
