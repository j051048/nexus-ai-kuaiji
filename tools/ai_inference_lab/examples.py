"""Read-only enterprise example retrieval and regression evaluation (AG05)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable


class RetrievalError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Example:
    example_id: str
    firm_id: str
    company_id: str
    book_id: str
    vendor_id: str
    business_type: str
    direction: str
    accounting_policy: str
    account_code: str
    amount: str
    reviewed: bool = True
    status: str = "active"
    evidence_complete: bool = True
    split: str = "train"
    source_sample_ids: tuple[str, ...] = ()
    source_hash: str = ""
    ancestor_source_hashes: tuple[str, ...] = ()
    policy_effective_from: str = ""
    policy_effective_to: str | None = None
    historical_period: str = ""


@dataclass(frozen=True)
class Query:
    firm_id: str
    company_id: str
    book_id: str
    vendor_id: str
    business_type: str
    direction: str
    accounting_policy: str
    sample_id: str
    period: str
    source_hash: str = ""
    evaluation_ids: tuple[str, ...] = ()
    evaluation_source_hashes: tuple[str, ...] = ()


def _period(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value) is not None


def _digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _identity(item: Query | Example) -> None:
    is_query = isinstance(item, Query)
    code = "query_identity_invalid" if is_query else "example_identity_invalid"
    keys = (
        "firm_id", "company_id", "book_id", "vendor_id", "business_type",
        "direction", "accounting_policy",
    )
    if (
        not isinstance(item, (Query, Example))
        or any(not _text(getattr(item, key)) for key in keys)
        or (is_query and (
            not _text(item.sample_id)
            or not isinstance(item.evaluation_ids, tuple)
            or any(not _text(value) for value in item.evaluation_ids)
            or not isinstance(item.evaluation_source_hashes, tuple)
        ))
        or (not is_query and (
            any(not _text(getattr(item, key)) for key in ("example_id", "account_code", "status", "split"))
            or type(item.reviewed) is not bool
            or type(item.evidence_complete) is not bool
            or not isinstance(item.source_sample_ids, tuple)
            or any(not _text(value) for value in item.source_sample_ids)
            or not isinstance(item.ancestor_source_hashes, tuple)
        ))
    ):
        raise RetrievalError(code, "范围身份必须为非空文本，审核/证据标志必须为布尔值")


def _hashes(item: Example) -> set[str]:
    values = (item.source_hash, *item.ancestor_source_hashes)
    if not all(_digest(value) for value in values):
        raise RetrievalError("example_source_missing", "样例缺少可核验来源及派生来源摘要")
    return {value.lower() for value in values}


def retrieve(query: Query, examples: Iterable[Example]) -> list[Example]:
    if not isinstance(query, Query):
        raise RetrievalError("query_identity_invalid", "查询须为受控 Query")
    _identity(query)
    if not _period(query.period) or not _digest(query.source_hash):
        raise RetrievalError("query_provenance_missing", "查询须指定期间和当前来源摘要")
    if any(not _digest(value) for value in query.evaluation_source_hashes):
        raise RetrievalError("query_provenance_missing", "验收来源摘要无效")
    excluded_ids = {query.sample_id, *query.evaluation_ids}
    excluded_hashes = {query.source_hash.lower(), *(value.lower() for value in query.evaluation_source_hashes)}
    result = []
    for item in examples:
        if not isinstance(item, Example):
            raise RetrievalError("example_identity_invalid", "样例须为受控 Example")
        _identity(item)
        if item.firm_id != query.firm_id or item.company_id != query.company_id:
            continue
        if item.book_id != query.book_id:
            continue
        if not item.reviewed or item.status != "active" or not item.evidence_complete:
            continue
        if item.split != "train":
            continue
        if excluded_ids.intersection({item.example_id, *item.source_sample_ids}):
            continue
        if _hashes(item).intersection(excluded_hashes):
            continue
        if item.vendor_id != query.vendor_id:
            continue
        if item.business_type != query.business_type or item.direction != query.direction:
            continue
        if item.accounting_policy != query.accounting_policy:
            continue
        if (
            not _period(item.policy_effective_from)
            or not _period(item.historical_period)
            or item.historical_period > query.period
            or item.policy_effective_from > query.period
            or (item.policy_effective_to is not None and (
                not _period(item.policy_effective_to)
                or item.policy_effective_to < item.policy_effective_from
                or item.policy_effective_to < query.period
            ))
        ):
            continue
        result.append(item)
    return sorted(result, key=lambda item: (item.business_type, item.example_id))


@dataclass(frozen=True)
class CandidateConfig:
    """Expected configuration supplied by the offline runner, never by a result."""

    candidate_id: str
    policy_version: str
    policy_prompt: str
    context_version: str
    schema_version: str = "ocr-replay-v1"
    mode: str = "mock"

    @property
    def version(self) -> str:
        return self.policy_version

    @property
    def prompt(self) -> str:
        return self.policy_prompt

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluationSpec:
    """Trusted runner inputs. The digest is pinned before candidate execution.

    A matching digest detects changes; it does not prove author identity. This
    local API assumes the caller controls the spec, not the candidate output.
    """

    manifest_path: Path
    manifest_sha256: str
    baseline: CandidateConfig
    candidate: CandidateConfig


@dataclass(frozen=True)
class UnknownAttempt:
    """Runner observation, not a model result relabelled to pass H01.

    H01 continues rejecting status=unknown JSON results. This separate envelope
    retains an identity-checked unknown attempt in the fixed denominator.
    """

    sample_id: str
    attempt_id: str
    manifest_sha256: str
    candidate_fingerprint: str
    input_hash: str
    gold_sha256: str
    source_hash: str
    request_fingerprint: str


def evaluate(baseline, candidate, *, spec: EvaluationSpec | None = None) -> dict[str, Any]:
    """Compatibility name; the same frozen spec is mandatory at every entry."""
    return evaluate_samples(baseline, candidate, spec=spec)


def evaluation_report(result: dict[str, Any]) -> dict[str, str]:
    if result.get("schema_version") != "ag05-evaluation-v2" or not isinstance(result.get("samples"), list):
        raise RetrievalError("evaluation_report_invalid", "只能展示强绑定评分器的报告")
    json_report = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    # Escape evidence-controlled text; the generated Markdown is not executable.
    def plain(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ")
    lines = [
        "# 样例评测报告",
        "",
        f"- 固定分母：{result['denominator']}",
        f"- 数据集 SHA-256：{plain(result['manifest_sha256'])}",
        f"- 基线配置：{plain(result['configurations']['baseline']['fingerprint'])}",
        f"- 候选配置：{plain(result['configurations']['candidate']['fingerprint'])}",
        f"- 相对结论：{plain(result['relative_result'])}",
        f"- 最终验收：{plain(result['acceptance'])}",
        f"- 候选正确率：{result['average']}",
        f"- 绝对安全门槛失败：{result['absolute_safety_failed']}",
        f"- 缺点数：{result['missing_count']}；未知数：{result['unknown_count']}",
        f"- 基线缺失：{plain(result['baseline']['missing'])}；基线未知：{plain(result['baseline']['unknown'])}",
        f"- 风险统计：{plain(result['candidate']['risk'])}",
        "",
        "| 样本/企业/账套/期间 | 基线 | 候选 | 风险真值 | 原因 | 来源/输入/金标摘要 |",
        "|---|---|---|---|---|---|",
    ]
    for row in result["samples"]:
        reasons = row["baseline"]["reasons"] + row["candidate"]["reasons"] + row["regressions"]
        scope = row["scope"]
        lines.append("| " + " | ".join(plain(value) for value in (
            f"{row['sample_id']} / {scope['company_id']} / {scope['book_id']} / {scope['period']}",
            f"{row['baseline']['status']} / correct={row['baseline']['correct']}",
            f"{row['candidate']['status']} / correct={row['candidate']['correct']}",
            row["risk_required"], ", ".join(reasons) or "ok",
            f"{row['source_hash']} / {row['input_hash']} / {row['gold_sha256']}",
        )) + " |")
    lines.extend(["", "仅限离线评测；不授权模型发布、会计审核、记账或导出。"])
    return {"json": json_report, "markdown": "\n".join(lines) + "\n"}


def validate_splits(
    rows: Iterable[Example], evaluation_ids: set[str], *,
    evaluation_source_hashes: set[str] | None = None,
) -> None:
    forbidden_hashes = {value.lower() for value in (evaluation_source_hashes or set())}
    if any(not _digest(value) for value in forbidden_hashes):
        raise RetrievalError("example_source_missing", "验收来源摘要无效")
    seen = set()
    for row in rows:
        if not isinstance(row, Example):
            raise RetrievalError("example_identity_invalid", "样例须为受控 Example")
        _identity(row)
        if row.example_id in seen:
            raise RetrievalError("example_duplicate", "样例重复")
        seen.add(row.example_id)
        if evaluation_ids.intersection({row.example_id, *row.source_sample_ids}):
            raise RetrievalError("example_leak", "待测样例泄漏到检索集")
        if row.split != "train":
            raise RetrievalError("example_split_invalid", "检索集仅允许 train")
        if _hashes(row).intersection(forbidden_hashes):
            raise RetrievalError("example_leak", "同源或派生来源泄漏到检索集")


def validate_manifest(
    manifest: dict[str, Any],
    expected: dict[str, Any],
    *,
    allowed_differences: set[str] | None = None,
) -> None:
    required = ("dataset_id", "policy_version", "prompt_version", "context_version", "split", "sample_ids")
    if not isinstance(manifest, dict) or not isinstance(expected, dict):
        raise RetrievalError("manifest_invalid", "manifest 必须是对象")
    for key in required:
        if key not in manifest or key not in expected:
            raise RetrievalError("manifest_missing", f"manifest 缺少 {key}")
        if manifest[key] in (None, "", []) or expected[key] in (None, "", []):
            raise RetrievalError("manifest_invalid", f"manifest {key} 为空")
    if not isinstance(manifest["dataset_id"], str) or not isinstance(expected["dataset_id"], str):
        raise RetrievalError("manifest_invalid", "dataset_id 必须是字符串")
    for key in ("policy_version", "prompt_version", "context_version", "split"):
        if not isinstance(manifest[key], str) or not isinstance(expected[key], str):
            raise RetrievalError("manifest_invalid", f"{key} 必须是字符串")
    if manifest["split"] not in {"train", "dev", "test", "holdout", "validation"}:
        raise RetrievalError("manifest_split_invalid", "split 不在允许列表")
    if not isinstance(manifest["sample_ids"], list) or not isinstance(expected["sample_ids"], list):
        raise RetrievalError("manifest_invalid", "sample_ids 必须是数组")
    for rows in (manifest["sample_ids"], expected["sample_ids"]):
        if any(not isinstance(item, str) or not item for item in rows):
            raise RetrievalError("manifest_invalid", "sample_ids 必须是非空字符串")
        if len(set(rows)) != len(rows):
            raise RetrievalError("manifest_invalid", "sample_ids 不能重复")
    if allowed_differences is not None and not isinstance(allowed_differences, set):
        raise RetrievalError("manifest_invalid", "allowed_differences 必须是集合")
    allowed = allowed_differences or set()
    if not allowed.issubset({"prompt_version", "policy_version", "context_version"}):
        raise RetrievalError("manifest_difference_not_allowed", "实验差异只允许 prompt/policy/context 版本")
    for key in required:
        if key in allowed:
            continue
        if manifest[key] != expected[key]:
            raise RetrievalError("manifest_stale", f"{key} 与评分候选不一致")


def evaluate_samples(
    baseline: list[dict[str, Any] | UnknownAttempt],
    candidate: list[dict[str, Any] | UnknownAttempt],
    *,
    spec: EvaluationSpec | None = None,
) -> dict[str, Any]:
    """Validate frozen sources first, derive scores from gold, then compare.

    Candidate flags such as amount_correct/risk/cross_company are ignored.
    A forged result identity raises, rather than becoming a lower score.
    """
    from tools.ocr_replay import replay
    from scripts.evaluate_ocr_fields import canonical, FIELDS

    if not isinstance(spec, EvaluationSpec):
        raise RetrievalError("evaluation_spec_required", "评分必须指定可信冻结 manifest 和候选配置")
    for config in (spec.baseline, spec.candidate):
        if not isinstance(config, CandidateConfig) or any(
            not isinstance(value, str) or not value.strip() for value in asdict(config).values()
        ):
            raise RetrievalError("evaluation_config_invalid", "候选配置缺少明确版本")
        if config.schema_version != replay.SCHEMA or config.mode not in {"mock", "archived_results"}:
            raise RetrievalError("evaluation_config_invalid", "仅允许明确的离线候选")
    if spec.baseline.mode != spec.candidate.mode:
        raise RetrievalError("evaluation_mode_mismatch", "比较必须使用相同离线运行模式")
    path = Path(spec.manifest_path)
    if not _digest(spec.manifest_sha256) or replay.sha256_path(path) != spec.manifest_sha256.lower():
        raise RetrievalError("evaluation_manifest_changed", "数据集与执行前冻结摘要不一致")
    try:
        manifest = replay.load_manifest(path)
    except replay.ReplayError as error:
        raise RetrievalError(error.code, error.message) from None
    if manifest["manifest_sha256"] != spec.manifest_sha256.lower():
        raise RetrievalError("evaluation_manifest_changed", "读取期间数据集发生变化")
    samples = {sample["sample_id"]: sample for sample in manifest["samples"]}
    if not samples:
        raise RetrievalError("evaluation_empty", "冻结样本集为空")
    truths = {}
    for sid, sample in samples.items():
        # load_manifest validates paths and hashes. Revalidate the read here so
        # the additional risk gold is bound to those same bytes.
        gold = replay.read_json(Path(sample["gold_path"]))
        if replay.sha256_path(Path(sample["gold_path"])).lower() != sample["gold_sha256"].lower():
            raise RetrievalError("GOLD_HASH", "金标在读取期间变化")
        labels = gold.get("evaluation")
        if (
            gold.get("sample_id") != sid
            or not isinstance(labels, dict)
            or type(labels.get("risk_required")) is not bool
            or type(labels.get("abstention_expected")) is not bool
            or (labels["abstention_expected"] and not labels["risk_required"])
            or (sample["category"].startswith("unsupported") and not labels["abstention_expected"])
            or gold.get("fields") != sample["gold_fields"]
        ):
            raise RetrievalError("evaluation_gold_invalid", f"{sid} 缺少一致且冻结的风险/弃答金标")
        try:
            for field, value in sample["gold_fields"].items():
                canonical(field, value)
        except (ValueError, ArithmeticError):
            raise RetrievalError("evaluation_gold_invalid", f"{sid} 金标字段无效") from None
        truths[sid] = labels

    def index(rows, label, config):
        if not isinstance(rows, list):
            raise RetrievalError("evaluation_results_invalid", "结果必须是数组")
        found = {}
        for row in rows:
            sid = row.sample_id if isinstance(row, UnknownAttempt) else row.get("sample_id") if isinstance(row, dict) else None
            if not isinstance(sid, str) or sid not in samples:
                raise RetrievalError("evaluation_identity_mismatch", f"{label} 含未冻结样本")
            if sid in found:
                raise RetrievalError("evaluation_duplicate", f"{label} 样本重复")
            sample = samples[sid]
            bindings = {
                "manifest_sha256": spec.manifest_sha256.lower(),
                "candidate_fingerprint": config.fingerprint,
                "input_hash": sample["ocr_sha256"].lower(),
                "gold_sha256": sample["gold_sha256"].lower(),
                "source_hash": sample["source_hash"],
                "request_fingerprint": replay.expected_request_fingerprint(sample, config),
            }
            actual = asdict(row) if isinstance(row, UnknownAttempt) else row
            if any(actual.get(key) != value for key, value in bindings.items()):
                raise RetrievalError("evaluation_binding_mismatch", f"{label}/{sid} 来源/金标/候选绑定不一致")
            if isinstance(row, UnknownAttempt):
                if not isinstance(row.attempt_id, str) or not row.attempt_id.strip():
                    raise RetrievalError("evaluation_attempt_invalid", "未知执行缺少 attempt 身份")
            else:
                try:
                    # Mandatory for every completed H01 result. Never coerce
                    # unknown JSON to succeeded/failed to bypass H01.
                    replay.validate_result_identity(manifest, sample, row, config)
                except replay.ReplayError as error:
                    raise RetrievalError(error.code, error.message) from None
                if set(row["fields"]) - set(FIELDS):
                    raise RetrievalError("evaluation_fields_invalid", f"{sid} 含未知字段")
                if any(value is not None and not isinstance(value, str) for value in row["fields"].values()):
                    raise RetrievalError("evaluation_fields_invalid", f"{sid} 字段只能为文本或 null")
                if row["status"] == "failed" and row["review_required"] is not True:
                    raise RetrievalError("evaluation_failed_unrouted", "失败执行必须转人工")
            found[sid] = row
        return found

    base = index(baseline, "baseline", spec.baseline)
    cand = index(candidate, "candidate", spec.candidate)

    def score(sid, row):
        truth, sample = truths[sid], samples[sid]
        if row is None or isinstance(row, UnknownAttempt):
            state = "missing" if row is None else "unknown"
            return {
                "status": state, "correct": False, "amount_correct": False,
                "evidence_complete": False, "field_correct": {}, "risk_cell": "unobserved",
                "review_required": None, "correct_abstention": False,
                "safety_failed": True, "reasons": [state],
            }
        fields = row["fields"]
        # An execution failure cannot count as a domain decision even if the
        # expected output is abstention. Contradictory error-bearing "success"
        # envelopes also remain incomplete, rather than becoming a correct TP.
        execution_failed = row["status"] == "failed" or any(
            row.get(key) is not None for key in ("error_code", "failure_kind", "error")
        )
        field_correct = {}
        for field, value in sample["gold_fields"].items():
            try:
                field_correct[field] = (
                    not execution_failed and row["status"] == "succeeded" and field in fields
                    and canonical(field, fields[field]) == canonical(field, value)
                )
            except (ValueError, ArithmeticError):
                field_correct[field] = False
        try:
            evidence_errors = replay.validate_evidence(row.get("evidence", []), sample["ocr_pages"], fields)
        except (ArithmeticError, ValueError, TypeError):
            evidence_errors = ["invalid-evidence-value"]
        evidence_complete = not evidence_errors
        review = row["review_required"]
        risk_cell = ("tp" if review else "fn") if truth["risk_required"] else ("fp" if review else "tn")
        decision = row.get("decision")
        abstained = (
            not execution_failed and row["status"] == "succeeded"
            and type(decision) is dict
            and decision == {"kind": "abstain", "reason": "unsupported_case"}
            and not fields and not row.get("evidence") and review is True
        )
        # A declared decision is part of every result's domain contract, even
        # when the gold expects ordinary extraction. Never ignore contradictory
        # fields plus abstention, unknown decisions, or an explicit null.
        decision_valid = "decision" not in row or abstained
        correct_abstention = truth["abstention_expected"] and abstained and review
        exact = all(field_correct.values())
        amount_keys = [field for field in field_correct if field.endswith("_amount")]
        amount_correct = bool(amount_keys) and all(field_correct[field] for field in amount_keys)
        # A planned unsupported-case abstention is a valid result, not a failed
        # execution. Any other failed execution prevents comparison.
        state = "failed" if execution_failed else "expected_abstention" if correct_abstention else row["status"]
        reasons = [f"field_incorrect:{field}" for field, ok in field_correct.items() if not ok]
        if correct_abstention:
            reasons = ["correct_abstention"]
        elif execution_failed:
            reasons.append("execution_failed")
        if not decision_valid:
            reasons.append("domain_decision_invalid")
        reasons += [f"evidence:{error}" for error in evidence_errors]
        if risk_cell in {"fn", "fp"}:
            reasons.append("risk_false_negative" if risk_cell == "fn" else "risk_false_positive")
        if truth["abstention_expected"] and not correct_abstention:
            reasons.append("expected_abstention_missing")
        safety_failed = bool(
            risk_cell == "fn" or not evidence_complete or not decision_valid
            or (truth["abstention_expected"] and not correct_abstention)
            or execution_failed
            or (not truth["abstention_expected"] and (not exact or row["status"] != "succeeded"))
        )
        return {
            "status": state,
            "correct": bool(decision_valid and (correct_abstention or (exact and evidence_complete and risk_cell in {"tp", "tn"} and not truth["abstention_expected"]))),
            "amount_correct": amount_correct, "field_correct": field_correct,
            "evidence_complete": evidence_complete, "review_required": review,
            "risk_cell": risk_cell, "correct_abstention": bool(correct_abstention),
            "safety_failed": safety_failed, "reasons": reasons,
        }

    details, regressions, fixes = [], [], []
    for sid in sorted(samples):
        b, c = score(sid, base.get(sid)), score(sid, cand.get(sid))
        reasons = []
        if b["status"] not in {"missing", "unknown", "failed"} and c["status"] not in {"missing", "unknown", "failed"}:
            reasons += [f"field_regression:{field}" for field, ok in b["field_correct"].items() if ok and not c["field_correct"].get(field)]
            if b["evidence_complete"] and not c["evidence_complete"]:
                reasons.append("evidence_regression")
            if b["risk_cell"] in {"tp", "tn"} and c["risk_cell"] in {"fn", "fp"}:
                reasons.append("risk_regression")
            if b["correct"] and not c["correct"]:
                reasons.append("sample_regression")
            if not b["correct"] and c["correct"]:
                fixes.append({"sample_id": sid, "reason": "sample_corrected"})
        regressions += [{"sample_id": sid, "reason": reason} for reason in reasons]
        sample = samples[sid]
        details.append({
            "sample_id": sid, "scope": {key: sample[key] for key in ("firm_id", "company_id", "ledger_id", "book_id", "period")},
            "split": sample["split"], "source_hash": sample["source_hash"],
            "input_hash": sample["ocr_sha256"].lower(), "gold_sha256": sample["gold_sha256"].lower(),
            "risk_required": truths[sid]["risk_required"], "abstention_expected": truths[sid]["abstention_expected"],
            "baseline": b, "candidate": c, "regressions": reasons,
        })

    def summary(label):
        risk = {key: [] for key in ("tp", "tn", "fp", "fn", "unobserved", "correct_abstentions")}
        missing, unknown, failed = [], [], []
        for row in details:
            state = row[label]
            risk[state["risk_cell"]].append(row["sample_id"])
            if state["correct_abstention"]:
                risk["correct_abstentions"].append(row["sample_id"])
            if state["status"] == "missing":
                missing.append(row["sample_id"])
            elif state["status"] == "unknown":
                unknown.append(row["sample_id"])
            elif state["status"] == "failed":
                failed.append(row["sample_id"])
        correct = sum(row[label]["correct"] for row in details)
        return {
            "correct": correct, "average": round(correct / len(samples), 4),
            "missing": missing, "missing_count": len(missing),
            "unknown": unknown, "unknown_count": len(unknown), "failed": failed,
            "risk": risk, "complete": not (missing or unknown or failed),
        }
    b_summary, c_summary = summary("baseline"), summary("candidate")
    comparable = b_summary["complete"] and c_summary["complete"]
    relative = "not_comparable" if not comparable else "regression" if regressions else "improvement" if fixes else "no_improvement"
    safety_failed = any(row["candidate"]["safety_failed"] for row in details)
    quality_failed = c_summary["correct"] != len(samples)
    acceptance = "incomplete" if not comparable else "rejected" if safety_failed or quality_failed or regressions else "accepted_offline"
    # Recheck external inputs before declaring the score valid (detect changes
    # during evaluation; no mutable gold/source payload is returned to callers).
    try:
        end = replay.load_manifest(path)
    except replay.ReplayError as error:
        raise RetrievalError(error.code, error.message) from None
    if end["manifest_sha256"] != spec.manifest_sha256.lower():
        raise RetrievalError("evaluation_manifest_changed", "评分期间 manifest 发生变化")
    return {
        "schema_version": "ag05-evaluation-v2", "manifest_sha256": spec.manifest_sha256.lower(),
        "configurations": {label: {**asdict(config), "fingerprint": config.fingerprint} for label, config in (("baseline", spec.baseline), ("candidate", spec.candidate))},
        "denominator": len(samples), "baseline": b_summary, "candidate": c_summary,
        "candidate_correct": c_summary["correct"], "average": c_summary["average"],
        "regressions": regressions, "regression": bool(regressions), "fixes": fixes,
        "missing": c_summary["missing"], "missing_count": c_summary["missing_count"],
        "unknown": c_summary["unknown"], "unknown_count": c_summary["unknown_count"],
        "absolute_safety_failed": safety_failed, "quality_failed": quality_failed, "risk": c_summary["risk"],
        "relative_result": relative, "comparable": comparable, "acceptance": acceptance,
        "result": relative, "result_code": acceptance, "samples": details,
        "mode": spec.candidate.mode, "model_verified": False,
    }


def h01_manifest_identity(path: str | Path) -> dict[str, Any]:
    """Thin read-only adapter over H01's frozen manifest validator."""
    from tools.ocr_replay import replay

    manifest = replay.load_manifest(Path(path))
    return {
        "dataset_id": manifest["manifest_sha256"],
        "sample_ids": [sample["sample_id"] for sample in manifest["samples"]],
        "companies": {sample["sample_id"]: sample["company_id"] for sample in manifest["samples"]},
        "splits": {sample["sample_id"]: sample.get("split", "test") for sample in manifest["samples"]},
    }


def h01_validate_result(
    manifest_path: str | Path,
    sample_id: str,
    result: dict[str, Any],
    policy=None,
) -> None:
    """Result-level H01 identity check; no duplicate manifest rules."""
    from tools.ocr_replay import replay

    manifest = replay.load_manifest(Path(manifest_path))
    sample = next((item for item in manifest["samples"] if item["sample_id"] == sample_id), None)
    if sample is None:
        raise RetrievalError("h01_sample_unknown", "H01 manifest 中不存在该样本")
    try:
        replay.validate_result_identity(manifest, sample, result, policy)
    except replay.ReplayError as error:
        raise RetrievalError(error.code, error.message) from None


def money(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception:
        raise RetrievalError("example_amount_invalid", "样例金额无效") from None
    if not result.is_finite():
        raise RetrievalError("example_amount_invalid", "样例金额无效")
    return result


def compile_example_context(
    query: Query, examples: Iterable[Example], *,
    current_facts, current_evidence, budget: dict[str, int],
    prompt_version: str, schema_version: str,
):
    """Use the real AG02 compiler; history never becomes a current fact.

    Current facts/evidence are trusted inputs supplied by the local harness.
    Historical examples get their own namespaced key and version 0; no caller
    must scrape their amounts back into an approved_fact.
    """
    from tools.ai_inference_lab.context import Fact, compile_context_pack

    facts = list(current_facts)
    evidence = list(current_evidence)
    if not facts or any(
        not isinstance(item, Fact) or item.source not in {"approved_fact", "ocr_unreviewed"}
        or type(item.version) is not int or item.version < 1
        or item.key.startswith("history:")
        for item in facts
    ):
        raise RetrievalError("current_facts_invalid", "当前事实必须有独立来源和正版本")
    history = [
        Fact(f"history:{item.example_id}:gross_amount", str(money(item.amount)), 0, "history")
        for item in retrieve(query, examples)
    ]
    return compile_context_pack(
        task="journal_suggest",
        scope={"firm_id": query.firm_id, "company_id": query.company_id, "book_id": query.book_id, "period": query.period},
        facts=[*facts, *history], evidence=evidence,
        budget=budget, prompt_version=prompt_version, schema_version=schema_version,
        policy_version=query.accounting_policy, required_keys=[item.key for item in facts],
    )


def validate_context_suggestion(pack, suggestion):
    """AG03 accepts only current ContextPack facts, never historical examples."""
    from tools.ai_inference_lab.answers import EvidenceFact, validate_suggestion

    # This thin adapter only supports citations to current frozen facts.
    # AG03's general constant-arithmetic contract has no input provenance;
    # allowing it here would let historical 100 replace current 120 via 50+50.
    if any(claim.kind != "source_fact" for claim in suggestion.claims):
        raise RetrievalError("context_claim_kind_unsupported", "当前事实适配只允许可核验的 source_fact 引用")
    by_id = {row["id"]: row for row in pack.evidence}
    evidence = []
    for fact in pack.facts:
        if fact["source"] not in {"approved_fact", "ocr_unreviewed"}:
            continue
        source = by_id.get(fact["evidence_id"])
        if (
            source is None or source["field"] != fact["key"] or source["value"] != fact["value"]
            or source["version"] != fact["version"] or fact["version"] != pack.fact_version
        ):
            raise RetrievalError("current_evidence_mismatch", "当前事实与 ContextPack 证据不一致")
        currencies = [item["value"] for item in pack.facts if item["key"] == "currency" and item["source"] in {"approved_fact", "ocr_unreviewed"}]
        currency = currencies[0] if len(currencies) == 1 else ""
        evidence.append(EvidenceFact(
            fact["key"], str(fact["value"]), pack.scope["company_id"], pack.scope["book_id"],
            fact["version"], currency, fact["evidence_id"],
        ))
    return validate_suggestion(
        suggestion, company_id=pack.scope["company_id"], book_id=pack.scope["book_id"],
        fact_version=pack.fact_version, evidence=evidence,
    )
