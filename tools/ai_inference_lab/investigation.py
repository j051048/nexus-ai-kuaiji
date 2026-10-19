"""Bounded offline investigation and human handoff (AG04).

This is an offline contract, not a production queue or worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
from pathlib import Path
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
) -> Investigation:
    investigation = Investigation(run_id, company_id, book_id, fact_version, budget)
    if revoked:
        investigation.status = "stopped"
        investigation.stop_reason = "revoked"
        return investigation
    while True:
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
        new = 0
        rejected = ""
        for result in results:
            if result.instruction:
                # Instructions in evidence are data, never authority.
                continue
            try:
                if _add_evidence(investigation, result):
                    new += 1
            except InvestigationError as error:
                rejected = error.code
                break
        if rejected:
            investigation.steps.append({"step": len(investigation.steps) + 1, "tool": "resolver", "error": rejected})
            investigation.status = "stopped"
            investigation.stop_reason = rejected
            break
        investigation.steps.append({
            "step": len(investigation.steps) + 1,
            "tool": "resolver",
            "new_evidence": new,
            "evidence_count": len(investigation.evidence),
        })
        if conflict and conflict[0] and conflict[1] and len(investigation.evidence) >= 2:
            difference = abs(float(conflict[0]) - float(conflict[1]))
            investigation.handoff = {
                "kind": "offline_accountant_handoff",
                "conflict": {"left": conflict[0], "right": conflict[1], "difference": f"{difference:.2f}"},
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


def save(investigation: Investigation, path: str | Path) -> None:
    Path(path).write_text(json.dumps(asdict(investigation), ensure_ascii=False, sort_keys=True), encoding="utf-8")


def load(path: str | Path) -> Investigation:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") == "investigation-v0":
        raise InvestigationError("investigation_version_unknown", "未知调查记录版本")
    budget = Budget(**payload["budget"])
    result = Investigation(
        run_id=payload["run_id"], company_id=payload["company_id"], book_id=payload["book_id"],
        fact_version=payload["fact_version"], budget=budget, status=payload["status"],
        stop_reason=payload.get("stop_reason", ""), steps=payload.get("steps", []),
        evidence=payload.get("evidence", []), calls=payload.get("calls", 0), handoff=payload.get("handoff"),
    )
    return result
