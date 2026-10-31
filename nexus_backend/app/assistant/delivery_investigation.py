"""Bounded fixed domain reads, not a model-selected tool execution framework.

The existing domain services/RLS remain authoritative. Checks are cooperative at
read boundaries (they do not interrupt a running SQL statement). Only stable
scope and output digests enter the frozen snapshot; clocks never do. The worker
still re-reads and compares the entire snapshot before sending and accepting.
"""

from copy import deepcopy
from dataclasses import dataclass
import math
import time

from app.accounting.domain import AccountingError
from app.accounting.service import fingerprint

STEPS = {
    "scope_required": (),
    "selected_items": ("selected_materials",),
    "selected_book": ("selected_materials", "month_close", "package_receipt"),
}
SOURCE_KINDS = (set(), {"plan", "delivery_readiness", "close", "reconciliation"}, {"package"})
MATERIAL_ISSUES = {
    "event_outside_period",
    "missing_original",
    "amount_conflict",
    "event_checks_pending",
    "draft_review",
}


@dataclass(frozen=True)
class ReadBudget:
    # Server-owned, never taken from a model or request payload.
    max_steps: int = 3
    seconds: float = 10.0

    def __post_init__(self):
        if type(self.max_steps) is not int or not 0 <= self.max_steps <= 3:
            raise ValueError("invalid delivery read step budget")
        if (
            type(self.seconds) not in (int, float)
            or not math.isfinite(self.seconds)
            or not 0 <= self.seconds <= 30
        ):
            raise ValueError("invalid delivery read time budget")


def _scope(view):
    return {key: view[key] for key in ("company_id", "book_id", "period", "coverage")}


def _step(tool, seq, view, source_start, issue_start, source_end, issue_end):
    return {
        "seq": seq,
        "tool": tool,
        "source_range": [source_start, source_end],
        "issue_range": [issue_start, issue_end],
        "outputs_sha256": fingerprint(
            {
                "scope": _scope(view),
                "sources": view["sources"][source_start:source_end],
                "issues": view["issues"][issue_start:issue_end],
            }
        ),
    }


def handoff(view):
    groups = {}
    for index, issue in enumerate(view["issues"]):
        groups.setdefault(issue["owner_role"], []).append(index)
    return {
        "schema": "delivery-handoff-v1",
        **_scope(view),
        "source_fingerprint": fingerprint({"sources": view["sources"], "issues": view["issues"]}),
        "assigned": False,  # Guidance by role is not an actual assignment.
        "requires_accountant_review": True,
        "groups": [
            {"owner_role": role, "issue_indices": indices} for role, indices in groups.items()
        ],
    }


class DeliveryReads:
    """Only the three fixed readers in delivery_context may call this coordinator."""

    def __init__(self, view, authorize, *, budget=None, clock=None):
        self.view = view
        self.authorize = authorize
        self.budget = budget or ReadBudget()
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.steps = []
        self.plan = STEPS[view["coverage"]]

    def _check(self, *, before):
        def deadline():
            elapsed = self.clock() - self.started
            if (
                not math.isfinite(elapsed)
                or elapsed < 0
                or elapsed >= self.budget.seconds
                or (before and len(self.steps) >= self.budget.max_steps)
            ):
                raise AccountingError(
                    "交付取证预算已用尽，请缩小资料范围后重新核对；未形成完整结论", 422
                )

        deadline()
        self.authorize()
        deadline()  # Authorization queries consume the same wall-clock budget.

    def read(self, tool, callback):
        position = len(self.steps)
        if position >= len(self.plan) or tool != self.plan[position]:
            raise AccountingError("交付取证步骤无效，请重新发起核对", 422)
        self._check(before=True)
        before = deepcopy(self.view)
        source_start, issue_start = len(before["sources"]), len(before["issues"])
        callback()
        self._check(before=False)  # Do not accept a late/revoked result.
        if (
            _scope(self.view) != _scope(before)
            or self.view["sources"][:source_start] != before["sources"]
            or self.view["issues"][:issue_start] != before["issues"]
        ):
            raise AccountingError("交付取证范围或前序依据发生变化，请重新核对", 409)
        self.steps.append(
            _step(
                tool,
                position + 1,
                self.view,
                source_start,
                issue_start,
                len(self.view["sources"]),
                len(self.view["issues"]),
            )
        )

    def finish(self):
        if len(self.steps) != len(self.plan):
            raise AccountingError("交付取证尚未完整，请重新核对", 422)
        self.view["investigation"] = {
            "schema": "delivery-reads-v1",
            **_scope(self.view),
            "read_complete": True,
            "model_calls": 0,
            "steps": deepcopy(self.steps),
        }
        self.view["handoff"] = handoff(self.view)


def validate_trace(view):
    """Validate the complete envelope, not just a caller-recomputed top hash."""
    trace = view.get("investigation")
    if not isinstance(trace, dict) or not isinstance(trace.get("steps"), list):
        raise ValueError("delivery investigation missing")
    plan = STEPS[view["coverage"]]
    if len(trace["steps"]) != len(plan):
        raise ValueError("delivery investigation incomplete")
    expected_steps, source_start, issue_start = [], 0, 0
    for position, (tool, step) in enumerate(zip(plan, trace["steps"])):
        if not isinstance(step, dict):
            raise ValueError("delivery read invalid")
        ranges = [step.get("source_range"), step.get("issue_range")]
        for span, start, limit in zip(
            ranges, (source_start, issue_start), (len(view["sources"]), len(view["issues"]))
        ):
            if (
                not isinstance(span, list)
                or len(span) != 2
                or any(type(n) is not int for n in span)
                or span[0] != start
                or not start <= span[1] <= limit
            ):
                raise ValueError("delivery output ranges invalid")
        source_end, issue_end = ranges[0][1], ranges[1][1]
        if any(
            source.get("kind") not in SOURCE_KINDS[position]
            for source in view["sources"][source_start:source_end]
        ):
            raise ValueError("delivery source outside reader")
        codes = [issue["code"] for issue in view["issues"][issue_start:issue_end]]
        if (position == 0 and any(code not in MATERIAL_ISSUES for code in codes)) or (
            position == 2 and any(code != "awaiting_customer_ack" for code in codes)
        ):
            raise ValueError("delivery issue outside reader")
        expected_steps.append(
            _step(tool, position + 1, view, source_start, issue_start, source_end, issue_end)
        )
        source_start, issue_start = source_end, issue_end
    if source_start != len(view["sources"]) or issue_start != len(view["issues"]):
        raise ValueError("delivery outputs not fully covered")
    expected = {
        "schema": "delivery-reads-v1",
        **_scope(view),
        "read_complete": True,
        "model_calls": 0,
        "steps": expected_steps,
    }
    # Canonical encoding distinguishes bool from integer and rejects extra keys.
    if fingerprint(trace) != fingerprint(expected) or fingerprint(
        view.get("handoff")
    ) != fingerprint(handoff(view)):
        raise ValueError("delivery investigation/handoff binding invalid")
