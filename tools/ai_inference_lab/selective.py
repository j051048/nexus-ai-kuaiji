"""DL-R1B offline selective review with deterministic validation and resumable state."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import os

from tools.ocr_replay import replay
from .contracts import (
    CallRequest, CancelToken, Capabilities, Endpoint, EnterprisePolicy, Executor,
    Failure, MockTransport, RunLimits, Scope, Status, TransportError, TrustedConfig,
)

FIELDS = replay.FIELDS
LABELS = {
    "invoice_number": "发票号码",
    "issued_on": "开票日期",
    "seller_name": "销售方名称",
    "seller_tax_id": "销售方纳税人识别号",
    "buyer_name": "购买方名称",
    "buyer_tax_id": "购买方纳税人识别号",
    "gross_amount": "价税合计",
    "net_amount": "金额",
    "tax_amount": "税额",
    "currency": "币种",
}
AMOUNTS = {"gross_amount", "net_amount", "tax_amount"}


@dataclass(frozen=True)
class Policy:
    company_id: str
    allowed: bool = True
    allow_review: bool = True
    local_only: bool = False


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _strict_amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?", value):
        return False
    return True


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        from datetime import date
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def validate_fields(fields, pages=None, category="standard"):
    """Return structured anomalies from fields/evidence; never reads gold."""
    errors = []
    if not isinstance(fields, dict):
        return ["FIELD_STRUCTURE"]
    unknown = set(fields) - set(FIELDS)
    if unknown:
        errors.append("UNKNOWN_FIELD")
    for name, value in fields.items():
        if name not in FIELDS:
            continue
        if value is not None and not isinstance(value, str):
            errors.append(f"FIELD_TYPE:{name}")
            continue
        if name in AMOUNTS and value is not None and not _strict_amount(value):
            errors.append(f"INVALID_AMOUNT:{name}")
        if name == "issued_on" and value is not None and not _date(value):
            errors.append("INVALID_DATE")
        if value is not None and name not in AMOUNTS and name != "issued_on":
            if not value.strip() or len(value) > 200:
                errors.append(f"FIELD_VALUE:{name}")
    missing = [name for name, value in fields.items() if value is None]
    if missing:
        errors.append("MISSING_FIELD")
    if all(fields.get(n) is not None for n in AMOUNTS):
        try:
            if Decimal(fields["gross_amount"]) != Decimal(fields["net_amount"]) + Decimal(fields["tax_amount"]):
                errors.append("AMOUNT_MISMATCH")
        except InvalidOperation:
            errors.append("INVALID_AMOUNT")
    if fields.get("currency") and fields["currency"].upper() != "CNY":
        errors.append("UNSUPPORTED_CURRENCY")
    if category.startswith("unsupported"):
        errors.append("UNSUPPORTED_DOCUMENT")
    return sorted(set(errors))


def _quote_supports(field, value, quote):
    if not isinstance(quote, str) or LABELS[field] not in quote:
        return False
    if field in AMOUNTS:
        try:
            target = Decimal(value)
        except (InvalidOperation, TypeError):
            return False
        tokens = re.findall(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", quote)
        return any(Decimal(t) == target for t in tokens)
    return str(value) in quote


def validate_evidence(evidence, pages, fields):
    errors = []
    if not isinstance(evidence, list):
        return ["EVIDENCE_STRUCTURE"]
    page_map = {p["page"]: p["raw_text"] for p in pages}
    for ev in evidence:
        if not isinstance(ev, dict) or ev.get("field") not in FIELDS:
            errors.append("EVIDENCE_STRUCTURE")
            continue
        field, page, quote = ev.get("field"), ev.get("page"), ev.get("quote")
        if type(page) is not int or page not in page_map:
            errors.append(f"PAGE_NOT_FOUND:{field}")
        elif not isinstance(quote, str) or not quote or quote not in page_map[page]:
            errors.append(f"QUOTE_NOT_FOUND:{field}")
        elif fields.get(field) is not None and not _quote_supports(field, fields[field], quote):
            errors.append(f"EVIDENCE_NOT_SUPPORT:{field}")
    for field, value in fields.items():
        if value is not None and not any(isinstance(ev, dict) and ev.get("field") == field for ev in evidence):
            errors.append(f"MISSING_EVIDENCE:{field}")
    return sorted(set(errors))


def route(anomalies, policy, pages, budget_remaining):
    if not policy.allowed:
        return "blocked", "POLICY_BLOCKED"
    if not pages or len(pages) > 6:
        return "blocked", "MISSING_PAGE"
    if "UNSUPPORTED_DOCUMENT" in anomalies or "UNSUPPORTED_CURRENCY" in anomalies:
        return "blocked", "UNSUPPORTED"
    if not anomalies:
        return "none", ""
    if budget_remaining <= 0:
        return "review", "BUDGET_EXHAUSTED"
    if not policy.allow_review:
        return "blocked", "REVIEW_NOT_ALLOWED"
    return "review", ""


def load_responses(path):
    data = replay.read_json(Path(path))
    if not isinstance(data, dict):
        raise ValueError("review response fixture must be an object")
    return data


def _review_request(sample, policy, input_text):
    return CallRequest(
        request_id=f"dl02-{sample['sample_id']}",
        run_id="dl02-run",
        attempt_id=f"dl02-{sample['sample_id']}-attempt-1",
        sample_id=sample["sample_id"],
        scope=Scope(sample["firm_id"], sample["company_id"], sample["ledger_id"], sample["book_id"], sample["period"]),
        input_sha256=hashlib.sha256(input_text.encode()).hexdigest(),
        input_text=input_text,
        endpoint_id="mock-endpoint",
        scenario="invoice_review",
        schema_version="dl02-v1",
        policy_version="dl02-review-v1",
        provider="mock",
        model="mock",
        endpoint="mock://local",
        provider_is_local=True,
        allowed_data="ocr_text",
        max_output_tokens=500,
        max_calls=1,
        deadline_seconds=5,
        cancel=CancelToken(),
        capabilities=Capabilities(structured_output=True),
        required_capabilities=frozenset({"structured_output"}),
        output_schema={"type": "object", "required": ["fields", "evidence"], "properties": {"fields": {"type": "object"}, "evidence": {"type": "array"}}},
    )


def _review_call(sample, policy, response, fail_mode=None):
    text = "\n".join(p["raw_text"] for p in sample["ocr_pages"])
    request = _review_request(sample, policy, text)
    if fail_mode == "unknown":
        transport = MockTransport({sample["sample_id"]: TransportError("timeout_post_send", sent=True)})
    elif fail_mode == "cancel":
        transport = MockTransport({sample["sample_id"]: TransportError("cancelled_post_send", sent=True)})
    else:
        transport = MockTransport({sample["sample_id"]: response})
    enterprise = EnterprisePolicy(
        allowed_firm_id=sample["firm_id"],
        allowed_company_ids=frozenset({sample["company_id"]}),
        allowed_providers=frozenset({"mock"}),
        local_only=policy.local_only,
    )
    trusted = TrustedConfig(
        scopes={sample["company_id"]: request.scope},
        endpoints={"mock-endpoint": Endpoint(
            "mock-endpoint", "mock://local", "mock", "mock", True,
            Capabilities(structured_output=True, usage_receipt=True, cancellation=True),
        )},
        local_only=policy.local_only,
    )
    return Executor(
        transport, enterprise, trusted=trusted, limits=RunLimits(3, 30, 500)
    ).invoke(request)


def run(manifest_path, *, responses_path=None, policies=None, budget=2, resume=False, state_dir=None, fail_mode=None, bad_review=False, unknown_review=False):
    if unknown_review:
        fail_mode = "unknown"
    manifest = replay.load_manifest(Path(manifest_path))
    fixture_dir = Path(manifest_path).resolve().parent
    response_file = Path(responses_path or fixture_dir / "review_responses.json")
    responses = load_responses(response_file)
    if bad_review:
        responses = load_responses(fixture_dir / "review_responses_bad.json")
    policies = policies or {f["company_id"]: Policy(f["company_id"]) for f in manifest["firms"]}
    state_path = Path(state_dir) / "state.json" if state_dir else None
    fingerprint = _sha({
        "manifest": manifest["manifest_sha256"], "budget": budget,
        "responses": replay.sha256_path(response_file),
        "policies": {k: vars(v) for k, v in policies.items()},
        "mode": "mock",
    })
    state = {"schema_version": "dl02-v1", "fingerprint": fingerprint, "samples": {}, "status": "running"}
    if state_path and state_path.exists():
        old = replay.read_json(state_path)
        if old.get("fingerprint") != fingerprint:
            raise replay.ReplayError("INCOMPATIBLE_RUN", "输入、策略、预算或归档内容已变化")
        state = old
        if resume is not True:
            raise replay.ReplayError("OUTPUT_EXISTS", "运行状态已存在")
    if state_path:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        replay._atomic_json(state_path, state)

    results = []
    for sample in manifest["samples"]:
        sid = sample["sample_id"]
        old = state["samples"].get(sid)
        if old and old.get("status") == "completed" and old.get("result_sha256"):
            if old["result_sha256"] != _sha(old["result"]):
                raise replay.ReplayError("RESULT_CORRUPT", f"{sid} 结果被修改")
            validate_result(manifest, sample, old["result"])
            results.append(old["result"])
            continue
        if old and old.get("status") in {"sent_unknown", "review_sent"}:
            # Persisted send without a confirmed result: never re-send.
            result = old.get("result") or {
                "schema_version": "dl02-v1", "manifest_sha256": manifest["manifest_sha256"],
                "run_mode": "mock", "sample_id": sid, "firm_id": sample["firm_id"],
                "company_id": sample["company_id"], "ledger_id": sample["ledger_id"],
                "book_id": sample["book_id"], "period": sample["period"],
                "category": sample["category"], "split": sample.get("split", ""),
                "baseline_status": "review_required", "candidate_status": "unknown",
                "route": "review", "route_reason": "", "review_requested": True,
                "review_attempted": True, "review_completed": False,
                "review_status": "unknown", "before": {}, "patch": {}, "after": {},
                "anomalies_before": [], "anomalies_after": [], "resolved_anomalies": [],
                "new_anomalies": [], "logical_calls": 2, "actual_mock_calls": 1,
                "actual_calls": 1, "review_call": {"status": "unknown", "failure": "timeout_post_send", "usage_known": False, "cost_known": False, "cost": None, "sent": True},
                "inputs": {"ocr_sha256": sample["ocr_sha256"].lower()},
            }
            results.append(result)
            continue
        inp = replay.to_extraction_input(sample)
        extraction = replay.extract(inp, replay.POLICIES["baseline"])
        before = dict(extraction.get("fields", {}))
        anomalies_before = validate_fields(before, sample["ocr_pages"], sample["category"])
        anomalies_before += validate_evidence(extraction.get("evidence", []), sample["ocr_pages"], before)
        policy = policies.get(sample["company_id"], Policy(sample["company_id"], allowed=False))
        decision, reason = route(anomalies_before, policy, sample["ocr_pages"], budget)
        review_requested = decision == "review"
        review_attempted = False
        review_completed = False
        review_call = None
        after = dict(before)
        patch = {}
        review_status = "not_requested"
        if review_requested:
            state["samples"][sid] = {"status": "review_requested", "result": None}
            if state_path:
                replay._atomic_json(state_path, state)
            if reason == "BUDGET_EXHAUSTED":
                review_status = "budget_exhausted"
            else:
                review_attempted = True
                response = responses.get(sid)
                review_call = _review_call(sample, policy, response, fail_mode)
                if review_call.sent:
                    state["samples"][sid] = {"status": "sent_unknown", "result": None}
                    if state_path:
                        replay._atomic_json(state_path, state)
                if review_call.status == Status.SUCCEEDED:
                    review_completed = True
                    review_status = "completed"
                    fields = review_call.structured.get("fields", {}) if isinstance(review_call.structured, dict) else {}
                    evidence = review_call.structured.get("evidence", []) if isinstance(review_call.structured, dict) else []
                    patch = {k: v for k, v in fields.items() if k in FIELDS and k not in {"gross_amount", "net_amount", "tax_amount"} or k in AMOUNTS}
                    proposed = {**before, **patch}
                    merged_evidence = list(extraction.get("evidence", [])) + list(evidence)
                    errors = validate_evidence(merged_evidence, sample["ocr_pages"], proposed)
                    if errors:
                        review_status = "rejected_evidence"
                        review_call = review_call
                    else:
                        after = proposed
                        anomalies_after_evidence = errors
                        review_status = "applied"
                elif review_call.status == Status.UNKNOWN:
                    review_status = "unknown"
                elif review_call.status == Status.CANCELLED:
                    review_status = "cancelled"
                else:
                    review_status = "failed"
            anomalies_after = validate_fields(after, sample["ocr_pages"], sample["category"])
            anomalies_after += validate_evidence(
                list(extraction.get("evidence", [])) + (
                    list(review_call.structured.get("evidence", [])) if review_call and isinstance(review_call.structured, dict) else []
                ),
                sample["ocr_pages"], after,
            )
        else:
            review_status = "blocked" if decision == "blocked" else "not_requested"
            anomalies_after = anomalies_before
        baseline_status = "blocked" if decision == "blocked" else ("review_required" if anomalies_before else "completed")
        if review_call and review_call.status == Status.UNKNOWN:
            candidate_status = "unknown"
        elif review_call and review_call.status in {Status.FAILED, Status.CANCELLED}:
            candidate_status = "failed" if review_call.status == Status.FAILED else "review_required"
        elif review_status == "budget_exhausted":
            candidate_status = "review_required"
        elif decision == "blocked":
            candidate_status = "blocked"
        elif review_requested:
            candidate_status = "review_required"
        elif anomalies_after:
            candidate_status = "review_required"
        else:
            candidate_status = "completed"
        resolved = sorted(set(anomalies_before) - set(anomalies_after))
        new = sorted(set(anomalies_after) - set(anomalies_before))
        result = {
            "schema_version": "dl02-v1", "manifest_sha256": manifest["manifest_sha256"],
            "run_mode": "mock", "sample_id": sid,
            "firm_id": sample["firm_id"], "company_id": sample["company_id"],
            "ledger_id": sample["ledger_id"], "book_id": sample["book_id"], "period": sample["period"],
            "category": sample["category"], "split": sample.get("split", ""),
            "baseline_status": baseline_status, "candidate_status": candidate_status,
            "route": decision, "route_reason": reason,
            "review_requested": review_requested, "review_attempted": review_attempted,
            "review_completed": review_completed, "review_status": review_status,
            "before": before, "patch": patch, "after": after,
            "evidence": list(extraction.get("evidence", [])) + (
                list(review_call.structured.get("evidence", []))
                if review_call and review_call.status == Status.SUCCEEDED and isinstance(review_call.structured, dict)
                else []
            ),
            "anomalies_before": anomalies_before, "anomalies_after": anomalies_after,
            "resolved_anomalies": resolved, "new_anomalies": new,
            "logical_calls": 1 + (1 if review_requested else 0),
            "actual_mock_calls": 1 if review_call and review_call.sent else 0,
            "actual_calls": 1 if review_call and review_call.sent else 0,
            "review_call": {
                "status": review_call.status.value if review_call else None,
                "failure": review_call.failure.value if review_call and review_call.failure else None,
                "usage_known": review_call.usage.known if review_call else False,
                "cost_known": review_call.cost.known if review_call else False,
                "cost": str(review_call.cost.amount) if review_call and review_call.cost.known else None,
                "sent": review_call.sent if review_call else False,
            },
            "inputs": {"ocr_sha256": sample["ocr_sha256"].lower()},
        }
        state["samples"][sid] = {"status": "completed", "result": result, "result_sha256": _sha(result)}
        if state_path:
            replay._atomic_json(state_path, state)
        results.append(result)
    state["status"] = "completed"
    if state_path:
        replay._atomic_json(state_path, state)
    report = {"schema_version": "dl02-v1", "manifest_sha256": manifest["manifest_sha256"], "fingerprint": fingerprint,
              "model_verified": False, "results": results, "summary": summarize(manifest, results)}
    return report


def validate_result(manifest, sample, result):
    for key in ("sample_id", "firm_id", "company_id", "ledger_id", "book_id", "period"):
        if result.get(key) != sample[key]:
            raise replay.ReplayError("RESULT_SCOPE", f"{sample['sample_id']} {key} 不一致")
    if result.get("schema_version") != "dl02-v1":
        raise replay.ReplayError("RESULT_SCHEMA", "结果 schema 不一致")
    if result.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise replay.ReplayError("RESULT_DATASET", "结果数据集不一致")
    if result.get("inputs", {}).get("ocr_sha256") != sample["ocr_sha256"].lower():
        raise replay.ReplayError("RESULT_INPUT", f"{sample['sample_id']} 输入摘要不一致")
    if result.get("run_mode") != "mock":
        raise replay.ReplayError("RESULT_MODE", "运行模式不一致")
    return True


def compare(manifest_path, baseline_report, candidate_report):
    manifest = replay.load_manifest(Path(manifest_path))
    ids = [s["sample_id"] for s in manifest["samples"]]
    by_id = {s["sample_id"]: s for s in manifest["samples"]}

    def index(report, label):
        rows = report.get("results") if isinstance(report, dict) else None
        if not isinstance(rows, list) or not rows:
            raise replay.ReplayError("NO_RESULTS", f"{label} 没有结果，不能比较")
        found = {}
        for row in rows:
            sid = row.get("sample_id")
            if sid in found:
                raise replay.ReplayError("DUPLICATE_RESULT", f"{label} 重复样本 {sid}")
            if sid not in by_id:
                raise replay.ReplayError("UNKNOWN_SAMPLE", f"{label} 未知样本 {sid}")
            validate_result(manifest, by_id[sid], row)
            found[sid] = row
        if set(found) != set(ids):
            raise replay.ReplayError("RESULT_SET", f"{label} 结果集不完整")
        return found

    base, cand = index(baseline_report, "baseline"), index(candidate_report, "candidate")
    if baseline_report.get("manifest_sha256") != candidate_report.get("manifest_sha256"):
        raise replay.ReplayError("DATASET_MISMATCH", "基线与候选数据集不同")
    summary = summarize(manifest, candidate_report["results"])
    return {"manifest_sha256": manifest["manifest_sha256"], "status": summary["status"], "summary": summary}


def summarize(manifest, results):
    by_id = {s["sample_id"]: s for s in manifest["samples"]}
    supported = [r for r in results if not by_id[r["sample_id"]]["category"].startswith("unsupported")]
    fixed_fields, regressed_fields, critical = [], [], []
    for r in supported:
        gold = by_id[r["sample_id"]]["gold_fields"]
        for field, expected in gold.items():
            b = field in r["before"] and replay._same(field, expected, r["before"].get(field))
            a = field in r["after"] and replay._same(field, expected, r["after"].get(field))
            if not b and a:
                fixed_fields.append({"sample_id": r["sample_id"], "field": field})
            if b and not a:
                regressed_fields.append({"sample_id": r["sample_id"], "field": field})
                if field in {"gross_amount", "net_amount", "tax_amount", "invoice_number", "seller_tax_id", "buyer_tax_id"}:
                    critical.append(r["sample_id"])
    new_critical = [
        code for r in supported for code in r.get("new_anomalies", [])
        if code in {"AMOUNT_MISMATCH", "INVALID_DATE", "INVALID_AMOUNT", "EVIDENCE_NOT_SUPPORT", "MISSING_EVIDENCE"}
    ]
    status = "regression" if critical or new_critical else ("promising_offline" if fixed_fields else "no_improvement")
    if any(r["candidate_status"] in {"unknown", "failed"} for r in supported):
        status = "incomplete"
    return {
        "samples": len(results), "supported": len(supported), "unsupported": len(results) - len(supported),
        "review_requested": sum(1 for r in results if r["review_requested"]),
        "review_attempted": sum(1 for r in results if r["review_attempted"]),
        "review_completed": sum(1 for r in results if r["review_completed"]),
        "completed": sum(1 for r in results if r["candidate_status"] == "completed"),
        "review_required": sum(1 for r in results if r["candidate_status"] == "review_required"),
        "blocked": sum(1 for r in results if r["candidate_status"] == "blocked"),
        "failed": sum(1 for r in results if r["candidate_status"] == "failed"),
        "unknown": sum(1 for r in results if r["candidate_status"] == "unknown"),
        "fixed_fields": fixed_fields, "fixed_samples": sorted({x["sample_id"] for x in fixed_fields}),
        "regressed_fields": regressed_fields, "regressed_samples": sorted(set(critical)),
        "critical_regressions": sorted(set(critical)), "status": status,
        "logical_calls": sum(r["logical_calls"] for r in results),
        "actual_mock_calls": sum(r["actual_mock_calls"] for r in results),
        "model_effect_verified": False,
    }
