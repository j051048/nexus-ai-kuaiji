"""Bounded offline investigation and human handoff (AG04).

This is an offline contract, not a production queue or worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

INVESTIGATION_VERSION = "investigation-v1"


class InvestigationError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Budget:
    steps: int = 3
    calls: int = 2
    seconds: int = 10
    tokens: int = 1000


@dataclass(frozen=True)
class Result:
    kind: str
    value: Any = None
    evidence_id: str = ""
    company_id: str = ""
    book_id: str = ""
    version: int = 0
    instruction: str = ""
    tokens: int = 0
    usage_known: bool = True


@dataclass
class Investigation:
    run_id: str
    company_id: str
    book_id: str
    fact_version: int
    budget: Budget
    status: str = "running"
    stop_reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    handoff: dict[str, Any] | None = None
    tokens_used: int = 0
    version: str = INVESTIGATION_VERSION


def _validate_scope(result: Result, investigation: Investigation) -> None:
    if result.company_id != investigation.company_id or result.book_id != investigation.book_id:
        raise InvestigationError("investigation_scope_mismatch", "工具返回了其他企业或账套资料")
    if result.version != investigation.fact_version:
        raise InvestigationError("investigation_version_stale", "工具返回旧事实版本")


def _add_evidence(investigation: Investigation, result: Result) -> bool:
    if not result.evidence_id:
        return False
    if any(item["evidence_id"] == result.evidence_id for item in investigation.evidence):
        return False
    _validate_scope(result, investigation)
    investigation.evidence.append({
        "evidence_id": result.evidence_id,
        "kind": result.kind,
        "value": result.value,
        "version": result.version,
    })
    return True


def investigate(
    *,
    run_id: str,
    company_id: str,
    book_id: str,
    fact_version: int,
    budget: Budget,
    resolver: Callable[[Investigation], list[Result]],
    revoked: bool = False,
    conflict: tuple[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    authorization_check: Callable[[], bool] | None = None,
    fact_version_check: Callable[[], int] | None = None,
) -> Investigation:
    investigation = Investigation(run_id, company_id, book_id, fact_version, budget)
    started = clock()
    if revoked:
        investigation.status = "stopped"
        investigation.stop_reason = "revoked"
        return investigation
    if budget.steps <= 0 or budget.calls <= 0 or budget.seconds <= 0 or budget.tokens <= 0:
        investigation.status = "stopped"
        investigation.stop_reason = "budget_exhausted"
        return investigation
    while True:
        if authorization_check is not None and not authorization_check():
            investigation.status = "stopped"
            investigation.stop_reason = "revoked"
            break
        if fact_version_check is not None and fact_version_check() != fact_version:
            investigation.status = "stopped"
            investigation.stop_reason = "fact_version_changed"
            break
        if clock() - started >= budget.seconds:
            investigation.status = "stopped"
            investigation.stop_reason = "time_budget"
            break
        if investigation.tokens_used >= budget.tokens:
            investigation.status = "stopped"
            investigation.stop_reason = "token_budget"
            break
        if len(investigation.steps) >= budget.steps:
            investigation.status = "stopped"
            investigation.stop_reason = "step_budget"
            break
        if investigation.calls >= budget.calls:
            investigation.status = "stopped"
            investigation.stop_reason = "call_budget"
            break
        before = len(investigation.evidence)
        investigation.calls += 1
        try:
            results = resolver(investigation)
        except InvestigationError as error:
            investigation.steps.append({"tool": "resolver", "error": error.code})
            investigation.status = "stopped"
            investigation.stop_reason = error.code
            break
        usage = 0
        if any(not result.usage_known for result in results):
            investigation.steps.append({"step": len(investigation.steps) + 1, "tool": "resolver", "error": "usage_unknown"})
            investigation.status = "stopped"
            investigation.stop_reason = "usage_unknown"
            break
        usage = sum(max(0, result.tokens) for result in results)
        investigation.tokens_used += usage
        if clock() - started >= budget.seconds:
            investigation.status = "stopped"
            investigation.stop_reason = "time_budget_late"
            break
        if authorization_check is not None and not authorization_check():
            investigation.status = "stopped"
            investigation.stop_reason = "revoked"
            break
        if fact_version_check is not None and fact_version_check() != fact_version:
            investigation.status = "stopped"
            investigation.stop_reason = "fact_version_changed"
            break
        if investigation.tokens_used > budget.tokens:
            investigation.status = "stopped"
            investigation.stop_reason = "token_budget_overrun"
            break
        # Validate the whole batch before accepting any evidence. Exact repeat
        # identities are harmless; same ID with different content is rejected.
        rejected = ""
        accepted = []
        seen_batch: dict[str, tuple] = {}
        for result in results:
            if result.instruction:
                continue
            identity = result.evidence_id
            signature = (result.kind, result.value, result.company_id, result.book_id, result.version)
            try:
                _validate_scope(result, investigation)
                existing = next(
                    (item for item in investigation.evidence if item["evidence_id"] == identity),
                    None,
                )
                prior = seen_batch.get(identity)
                if prior is not None and prior != signature:
                    raise InvestigationError("investigation_evidence_conflict", "同 ID 证据内容不一致")
                if existing is not None and (
                    existing["kind"], existing["value"], existing["version"]
                ) != (result.kind, result.value, result.version):
                    raise InvestigationError("investigation_evidence_conflict", "同 ID 证据内容不一致")
                seen_batch[identity] = signature
            except InvestigationError as error:
                rejected = error.code
                break
            accepted.append(result)
        if rejected:
            investigation.steps.append({"step": len(investigation.steps) + 1, "tool": "resolver", "error": rejected})
            investigation.status = "stopped"
            investigation.stop_reason = rejected
            break
        new = 0
        for result in accepted:
            if _add_evidence(investigation, result):
                new += 1
        if rejected:
            investigation.steps.append({"step": len(investigation.steps) + 1, "tool": "resolver", "error": rejected})
            investigation.status = "stopped"
            investigation.stop_reason = "revoked" if rejected == "investigation_revoked" else rejected
            break
        investigation.steps.append({
            "step": len(investigation.steps) + 1,
            "tool": "resolver",
            "new_evidence": new,
            "evidence_count": len(investigation.evidence),
        })
        if conflict and conflict[0] and conflict[1] and len(investigation.evidence) >= 2:
            try:
                left, right = Decimal(str(conflict[0])), Decimal(str(conflict[1]))
                if not left.is_finite() or not right.is_finite():
                    raise InvalidOperation
                difference = abs(left - right)
            except (InvalidOperation, ValueError):
                investigation.status = "handoff"
                investigation.stop_reason = "conflict_invalid"
                break
            investigation.handoff = {
                "kind": "offline_accountant_handoff",
                "conflict": {"left": format(left, "f"), "right": format(right, "f"), "difference": format(difference, "f")},
                "suggested_role": "accountant",
                "stop_reason": "conflicting_evidence",
                "resolved": False,
            }
            investigation.status = "handoff"
            investigation.stop_reason = "conflicting_evidence"
            break
        if new == 0:
            investigation.status = "stopped"
            investigation.stop_reason = "no_new_evidence"
            break
        if len(investigation.evidence) > before and any(
            item["kind"] == "resolved" for item in investigation.evidence
        ):
            investigation.status = "resolved"
            investigation.stop_reason = "evidence_resolved"
            break
    if investigation.status == "stopped" and investigation.stop_reason not in {"revoked"}:
        investigation.handoff = {
            "kind": "offline_accountant_handoff",
            "conflict": None,
            "suggested_role": "accountant",
            "stop_reason": investigation.stop_reason,
            "resolved": False,
            "evidence": [item["evidence_id"] for item in investigation.evidence],
        }
    return investigation


def _digest(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "snapshot_hash"}
    return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(investigation: Investigation, path: str | Path) -> None:
    payload = asdict(investigation)
    payload["version"] = INVESTIGATION_VERSION
    payload["snapshot_hash"] = _digest(payload)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def resume_as_handoff(investigation: Investigation) -> Investigation:
    """Safely restore without automatic continuation or a new resolver call."""
    if investigation.status == "resolved":
        return investigation
    investigation.status = "handoff"
    investigation.stop_reason = "manual_resume_required"
    investigation.handoff = {
        "kind": "offline_accountant_handoff",
        "suggested_role": "accountant",
        "stop_reason": "manual_resume_required",
        "resolved": False,
        "evidence": [item["evidence_id"] for item in investigation.evidence],
    }
    return investigation


def load(
    path: str | Path,
    *,
    expected_scope: dict[str, str] | None = None,
    expected_run_id: str | None = None,
) -> Investigation:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise InvestigationError("investigation_snapshot_invalid", "调查快照不可读或已损坏") from None
    if not isinstance(payload, dict) or payload.get("version") != INVESTIGATION_VERSION:
        raise InvestigationError("investigation_version_unknown", "未知或缺失调查记录版本")
    if payload.get("snapshot_hash") != _digest(payload):
        raise InvestigationError("investigation_snapshot_integrity", "调查快照完整性校验失败")
    if expected_run_id is not None and payload.get("run_id") != expected_run_id:
        raise InvestigationError("investigation_run_mismatch", "调查快照不属于当前运行")
    if expected_scope:
        for key in ("company_id", "book_id"):
            if expected_scope.get(key) and payload.get(key) != expected_scope[key]:
                raise InvestigationError("investigation_scope_mismatch", "调查快照企业或账套不匹配")
    try:
        budget = Budget(**payload["budget"])
        result = Investigation(
            run_id=payload["run_id"], company_id=payload["company_id"], book_id=payload["book_id"],
            fact_version=payload["fact_version"], budget=budget, status=payload["status"],
            stop_reason=payload.get("stop_reason", ""), steps=payload.get("steps", []),
            evidence=payload.get("evidence", []), calls=payload.get("calls", 0),
            handoff=payload.get("handoff"), tokens_used=payload.get("tokens_used", 0),
        )
    except (KeyError, TypeError, ValueError):
        raise InvestigationError("investigation_snapshot_structure", "调查快照结构无效") from None
    if result.status not in {"running", "stopped", "handoff", "resolved"}:
        raise InvestigationError("investigation_status_invalid", "调查快照终态无效")
    if result.calls < 0 or result.tokens_used < 0 or result.calls > result.budget.calls:
        raise InvestigationError("investigation_counter_invalid", "调查快照计数不一致")
    return result
