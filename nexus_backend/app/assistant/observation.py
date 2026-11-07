"""Offline, side-effect-free projection of existing diagnostics into would-handoff records.

This module intentionally does not write business state, notifications, outboxes or tasks.
It is a local observation/verification seam for IMP08; production subscriptions are out of scope.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from tools.ai_inference_lab.rsi.snapshots import atomic_create, canonical, digest


class ObservationError(ValueError):
    pass


class ObservationConflict(ObservationError):
    pass


def observe(event: dict[str, Any], *, trusted=False) -> dict[str, Any]:
    """Project one authorized, versioned event snapshot. Never authorizes an action."""
    event_id = event.get("event_id")
    version = event.get("event_version")
    if not isinstance(event_id, str) or not event_id:
        raise ObservationError("event_id is required")
    if type(version) is not int or version < 1:
        raise ObservationError("event_version must be a positive integer")
    if event.get("revoked") is True or event.get("authorized") is not True:
        return _record(event_id, version, "authorization_revoked", None, None)
    if event.get("budget_exhausted") is True:
        return _record(event_id, version, "budget_exhausted", None, None)
    if event.get("notification_state") == "unknown":
        return _record(
            event_id,
            version,
            "verify_unknown",
            None,
            event.get("verification_target") if trusted else None,
        )
    analysis = event.get("analysis")
    if not isinstance(analysis, dict):
        return _record(event_id, version, "no_change", None, None)
    source_ref = (
        analysis.get("handoff", {}).get("source_ref")
        if isinstance(analysis.get("handoff"), dict)
        else None
    )
    if analysis.get("status") == "complete":
        return _record(event_id, version, "no_change", source_ref, None)
    if not trusted:
        return _record(event_id, version, "authorization_revoked", None, None)
    handoff = analysis.get("handoff")
    if not isinstance(handoff, dict):
        raise ObservationError("partial analysis requires structured handoff")
    return _record(
        event_id,
        version,
        "handoff",
        handoff.get("source_ref") or source_ref,
        handoff.get("action_target"),
    )


def _record(
    event_id: str, version: int, reason: str, source_ref: str | None, action_target: str | None
) -> dict[str, Any]:
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


class ObservationLedger:
    """Local-only, bounded append-only checkpoints, using RSI atomic create.

    Atomic sequence allocation is the cross-process fence. No database writes,
    automatic notification, model calls or application subscription are created.
    """

    def __init__(
        self,
        *,
        directory=None,
        scope=None,
        principal=None,
        max_events=100,
        seconds=300,
        clock=time.time,
    ):
        if (
            type(max_events) is not int
            or not 1 <= max_events <= 1000
            or type(seconds) is not int
            or not 1 <= seconds <= 3600
        ):
            raise ObservationError("invalid_budget")
        self.directory = Path(directory).resolve() if directory is not None else None
        self.scope, self.principal, self.clock = deepcopy(scope), principal, clock
        self.seen = {}
        self.header = {
            "schema": "imp08-observation-run-v2",
            "scope": scope,
            "principal": principal,
            "max_events": max_events,
            "seconds": seconds,
            "started_at": clock(),
        }
        if self.directory:
            if (
                not principal
                or not isinstance(scope, dict)
                or any(not scope.get(key) for key in ("company_id", "book_id", "period"))
            ):
                raise ObservationError("trusted_scope_required")
            path = self.directory / "run.json"
            try:
                atomic_create(path, canonical(self.header))
            except FileExistsError:
                prior = self._read(path)
                if {k: v for k, v in prior.items() if k != "started_at"} != {
                    k: v for k, v in self.header.items() if k != "started_at"
                }:
                    raise ObservationError("recovery_binding_mismatch")
                if type(prior.get("started_at")) not in (int, float):
                    raise ObservationError("invalid_snapshot")
                self.header = prior
        self.binding = digest(self.header)
        self._load()

    def _read(self, path):
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ObservationError("snapshot_size_or_path")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise ObservationError("invalid_snapshot") from error

    def _load(self):
        if not self.directory:
            return
        seen = {}
        previous = self.binding
        files = sorted(self.directory.glob("step-*.json"))
        if len(files) > self.header["max_events"]:
            raise ObservationError("budget_snapshot_invalid")
        for seq, path in enumerate(files, 1):
            row = self._read(path)
            if (
                not isinstance(row, dict)
                or row.get("seq") != seq
                or path.name != f"step-{seq:06d}.json"
                or row.get("previous") != previous
                or row.get("hash") != digest({k: v for k, v in row.items() if k != "hash"})
            ):
                raise ObservationError("snapshot_chain_invalid")
            key = row["event"]["event_id"]
            if key in seen:
                raise ObservationError("duplicate_event")
            seen[key] = (canonical(row["event"]), deepcopy(row["record"]))
            previous = row["hash"]
        self.seen, self.previous = seen, previous

    def record(self, event: dict[str, Any], *, resolver=None) -> dict[str, Any]:
        original = deepcopy(event)
        if len(canonical(original)) > 128_000:
            raise ObservationError("event_too_large")
        if self.scope and any(original.get(k) != v for k, v in self.scope.items()):
            raise ObservationError("event_scope_mismatch")
        projected = observe(original)  # shape validation, never authorizes a handoff
        self._load()
        existing = self.seen.get(original["event_id"])
        if existing and existing[0] != canonical(original):
            raise ObservationConflict("same event id with different content")
        elapsed = self.clock() - self.header["started_at"]
        if (
            elapsed < 0
            or elapsed >= self.header["seconds"]
            or (not existing and len(self.seen) >= self.header["max_events"])
        ):
            return _record(
                original["event_id"], original["event_version"], "budget_exhausted", None, None
            )
        # Even duplicate events re-authorize against current data before replay.
        if resolver:
            context = resolver(deepcopy(original))
            if context.get("authorized") is not True:
                return _record(
                    original["event_id"],
                    original["event_version"],
                    "authorization_revoked",
                    None,
                    None,
                )
            if context.get("source_version") != original.get("source_version"):
                raise ObservationError("source_version_changed")
            projected = observe(
                {
                    "event_id": original["event_id"],
                    "event_version": original["event_version"],
                    **context,
                },
                trusted=True,
            )
        elif self.directory:
            raise ObservationError("trusted_resolver_required")
        for _ in range(4):
            self._load()
            existing = self.seen.get(original["event_id"])
            if existing:
                if existing[0] != canonical(original):
                    raise ObservationConflict("same event id with different content")
                return deepcopy(existing[1])
            elapsed = self.clock() - self.header["started_at"]
            if (
                len(self.seen) >= self.header["max_events"]
                or elapsed < 0
                or elapsed >= self.header["seconds"]
            ):
                return _record(
                    original["event_id"], original["event_version"], "budget_exhausted", None, None
                )
            if self.directory:
                seq = len(self.seen) + 1
                row = {
                    "seq": seq,
                    "previous": self.previous,
                    "event": original,
                    "record": projected,
                }
                row["hash"] = digest(row)
                try:
                    atomic_create(self.directory / f"step-{seq:06d}.json", canonical(row))
                except FileExistsError:
                    continue
            self.seen[original["event_id"]] = (canonical(original), deepcopy(projected))
            return deepcopy(projected)
        raise ObservationConflict("concurrent_observation_busy")


class AnalysisObservation:
    """Trusted local runner adapter. Scope/principal come from the caller's service,
    never event authorization flags. Uses the same R2 reader and existing unknown
    verification records. It cannot dispatch or assign any business action.
    """

    def __init__(self, service, actor, scope):
        self.service, self.actor, self.scope = service, actor, deepcopy(scope)

    def __call__(self, event):
        from .analysis import analyze

        if any(event.get(k) != v for k, v in self.scope.items()):
            raise ObservationError("event_scope_mismatch")
        result = analyze(
            self.service,
            self.actor,
            self.scope["book_id"],
            self.scope["period"],
            event["kind"],
            expected_source_version=event["source_version"],
            **event.get("params", {}),
        )
        if result["company_id"] != self.scope["company_id"]:
            raise ObservationError("event_scope_mismatch")
        context = {
            "authorized": True,
            "source_version": result["source_version"],
            "analysis": result,
        }
        if event.get("notification_id"):
            from ..delivery.followups import DeliveryFollowupService
            from urllib.parse import urlencode

            service = DeliveryFollowupService(getattr(self.service, "db", self.service))
            rows = service.notifications(self.actor, self.scope["book_id"], self.scope["period"])[
                "items"
            ]
            row = next((r for r in rows if r["id"] == event["notification_id"]), None)
            if not row or row["state"] != "unknown":
                raise ObservationError("unknown_record_not_visible")
            context["notification_state"] = "unknown"
            if row.get("investigation"):
                from ..handoff.service import HandoffService

                source = next(
                    (
                        item["source"]
                        for item in HandoffService(service.db).queue(self.actor)["items"]
                        if item["source"]["kind"] == "notification_investigation"
                        and item["source"]["id"] == row["id"]
                    ),
                    None,
                )
                if (
                    source is None
                    or event.get("notification_fingerprint") != source["fingerprint"]
                    or event.get("notification_version") != source["version"]
                ):
                    raise ObservationError("notification_source_changed")
                context["verification_target"] = "/workspace/monthly?" + urlencode(
                    {
                        "book": self.scope["book_id"],
                        "company": self.scope["company_id"],
                        "period": self.scope["period"],
                        **{"handoff_" + key: str(value) for key, value in source.items()},
                    }
                )
        return context
