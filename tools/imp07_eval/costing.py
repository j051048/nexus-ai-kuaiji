"""Whole-task cost accounting for IMP07 offline evaluation.

The functions here are pure: they never call a provider, read a customer record
or trust a candidate-supplied total. Costs are kept as decimal strings so the
evaluation report cannot silently turn an unknown call into zero.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re

_COST = re.compile(r"-?\d+(?:\.\d{1,2})?")
_STATUSES = {"succeeded", "failed", "unknown", "not_sent"}


class CostingError(ValueError):
    pass


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decimal(value, name):
    if isinstance(value, bool) or not isinstance(value, str) or not _COST.fullmatch(value):
        raise CostingError(f"cost_{name}_invalid")
    try:
        return Decimal(value)
    except InvalidOperation as error:
        raise CostingError(f"cost_{name}_invalid") from error


def summarize_attempts(attempts, *, currency="CNY"):
    """Summarize physical attempts without counting one attempt twice.

    A repeated physical attempt with identical content is ignored. The same id
    with different content is an identity conflict, not a second charge.
    Missing cost is unknown; an explicit "0" is known zero.
    """
    if not isinstance(currency, str) or not currency:
        raise CostingError("cost_currency_invalid")
    seen = {}
    total = Decimal("0")
    unknown = 0
    failed = 0
    known = 0
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise CostingError("attempt_invalid")
        attempt_id = attempt.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise CostingError("attempt_id")
        status = attempt.get("status")
        if status not in _STATUSES:
            raise CostingError("attempt_status")
        canonical = _canonical(attempt)
        if attempt_id in seen:
            if seen[attempt_id] != canonical:
                raise CostingError("attempt_conflict")
            continue
        seen[attempt_id] = canonical
        if attempt.get("currency", currency) != currency:
            raise CostingError("cost_currency_mismatch")
        if status in {"failed", "unknown", "not_sent"}:
            failed += 1
        cost = attempt.get("cost_cny")
        if cost is None:
            unknown += 1
            continue
        total += _decimal(cost, "amount")
        known += 1
    return {
        "physical_attempts": len(seen),
        "known_calls": known,
        "unknown_calls": unknown,
        "failed_calls": failed,
        "currency": currency,
        "known_subtotal": f"{total:.2f}" if known else None,
        "total": f"{total:.2f}" if unknown == 0 and known else None,
        "complete": unknown == 0,
    }


def compare_cost(candidate, baseline):
    """Compare complete, same-currency totals; never invent a zero baseline."""
    if not isinstance(candidate, dict) or not isinstance(baseline, dict):
        raise CostingError("cost_summary_invalid")
    if candidate.get("currency") != baseline.get("currency"):
        return {"comparable": False, "saving": None, "reason": "currency_mismatch"}
    if not candidate.get("complete") or not baseline.get("complete"):
        return {"comparable": False, "saving": None, "reason": "incomplete_cost"}
    if candidate.get("total") is None or baseline.get("total") is None:
        return {"comparable": False, "saving": None, "reason": "missing_total"}
    base = _decimal(baseline["total"], "baseline")
    cand = _decimal(candidate["total"], "candidate")
    if base == 0:
        return {"comparable": False, "saving": None, "reason": "baseline_zero"}
    saving = base - cand
    return {
        "comparable": True,
        "saving": f"{saving:.2f}",
        "saving_ratio": f"{(saving / base):.6f}",
        "reason": None,
    }
