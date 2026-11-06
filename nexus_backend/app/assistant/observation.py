"""Offline, side-effect-free projection of existing diagnostics into would-handoff records.

This module intentionally does not write business state, notifications, outboxes or tasks.
It is a local observation/verification seam for IMP08; production subscriptions are out of scope.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class ObservationError(ValueError):
    pass


class ObservationConflict(ObservationError):
    pass


def observe(event: dict[str, Any]) -> dict[str, Any]:
    """Project one authorized, versioned event snapshot. Never authorizes an action."""
    event_id = event.get("event_id")
    version = event.get("event_version")
    if not isinstance(event_id, str) or not event_id:
        raise ObservationError("event_id is required")
    if not isinstance(version, int) or version < 1:
        raise ObservationError("event_version must be a positive integer")
    if event.get("revoked") is True or event.get("authorized") is False:
        return _record(event_id, version, "authorization_revoked", None, None)
    if event.get("budget_exhausted") is True:
        return _record(event_id, version, "budget_exhausted", None, None)
    if event.get("notification_state") == "unknown":
        return _record(event_id, version, "verify_unknown", None, "existing-verification")
    analysis = event.get("analysis")
    if not isinstance(analysis, dict):
        return _record(event_id, version, "no_change", None, None)
    source_ref = analysis.get("handoff", {}).get("source_ref") if isinstance(analysis.get("handoff"), dict) else None
    if analysis.get("status") == "complete":
        return _record(event_id, version, "no_change", source_ref, None)
    handoff = analysis.get("handoff")
    if not isinstance(handoff, dict):
        raise ObservationError("partial analysis requires structured handoff")
    return _record(event_id, version, "handoff", handoff.get("source_ref") or source_ref, handoff.get("action_target"))


def _record(event_id: str, version: int, reason: str, source_ref: str | None, action_target: str | None) -> dict[str, Any]:
    return {
        "schema": "imp08-observation-v1",
        "event_id": event_id,
        "event_version": version,
        "reason_code": reason,
        "source_ref": source_ref,
        "would_handoff": reason == "handoff",
        "would_notify": False,
        "action_target": action_target,
        "status": "observed",
    }


@dataclass
class ObservationLedger:
    """Idempotency for exactly one local run; not a business task store."""

    seen: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        record = observe(event)
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        existing = self.seen.get(record["event_id"])
        if existing is not None:
            if existing[0] != canonical:
                raise ObservationConflict("same event id with different content")
            return existing[1]
        self.seen[record["event_id"]] = (canonical, record)
        return record
