import json
from copy import deepcopy
import pytest
from app.assistant.observation import ObservationLedger, ObservationError
from test_delivery_followups import business, case, close_case, ready_case


def test_real_unknown_investigation_is_read_only_and_version_bound(case):
    from app.assistant.analysis import analyze
    from app.assistant.observation import AnalysisObservation
    from app.handoff.service import HandoffService
    from test_dcp_notification_investigation import unknown, record
    from test_n03_notifications import notification

    row = unknown(case)
    result = analyze(
        case.db, case.manager, case.book["id"], "2026-10", "expense_bridge", account_code="6602.02"
    )
    scope = {k: result[k] for k in ("company_id", "book_id", "period")}
    observed = {
        **scope,
        "event_id": "unknown-local",
        "event_version": 1,
        "source_version": result["source_version"],
        "kind": "expense_bridge",
        "params": {"account_code": "6602.02"},
        "notification_id": row["id"],
    }
    resolver = AnalysisObservation(case.db, case.manager, scope)
    assert ObservationLedger().record(observed, resolver=resolver)["action_target"] is None
    record(case, row)
    source = next(
        i["source"]
        for i in HandoffService(case.db).queue(case.manager)["items"]
        if i["source"]["kind"] == "notification_investigation" and i["source"]["id"] == row["id"]
    )
    observed.update(
        notification_fingerprint=source["fingerprint"], notification_version=source["version"]
    )
    value = ObservationLedger().record(observed, resolver=resolver)
    assert "handoff_kind=notification_investigation" in value["action_target"]
    assert value["would_notify"] is False
    assert (
        notification(case)["attempts"] == row["attempts"]
        and notification(case)["state"] == "unknown"
    )
    with pytest.raises(ObservationError):
        resolver({**observed, "notification_version": 0})


def event():
    return {
        "event_id": "e1",
        "event_version": 1,
        "company_id": "c",
        "book_id": "b",
        "period": "2026-10",
        "source_version": "a" * 64,
        "authorized": True,
    }


def resolver(e):
    return {
        "authorized": True,
        "source_version": e["source_version"],
        "analysis": {
            "status": "partial",
            "handoff": {"source_ref": "b:v1", "action_target": "/workspace/accounting?book=b"},
        },
    }


def test_persistent_replay_is_immutable_and_reauthorizes(tmp_path):
    args = {
        "directory": tmp_path,
        "scope": {"company_id": "c", "book_id": "b", "period": "2026-10"},
        "principal": "owner",
        "max_events": 2,
        "seconds": 60,
    }
    ledger = ObservationLedger(**args)
    first = ledger.record(event(), resolver=resolver)
    assert first["would_handoff"]
    first["reason_code"] = "tampered"
    recovered = ObservationLedger(**args)
    assert recovered.record(event(), resolver=resolver)["reason_code"] == "handoff"
    with pytest.raises(ObservationError):
        recovered.record({**event(), "event_version": 2}, resolver=resolver)
    assert not recovered.record(event(), resolver=lambda e: {"authorized": False})["would_handoff"]
    with pytest.raises(ObservationError):
        recovered.record({**event(), "company_id": "foreign"}, resolver=resolver)


def test_budget_does_not_reset_and_corrupt_snapshot_fails(tmp_path):
    args = {
        "directory": tmp_path,
        "scope": {"company_id": "c", "book_id": "b", "period": "2026-10"},
        "principal": "p",
        "max_events": 1,
        "seconds": 60,
    }
    ObservationLedger(**args).record(event(), resolver=resolver)
    ledger = ObservationLedger(**args)
    assert (
        ledger.record({**event(), "event_id": "e2"}, resolver=resolver)["reason_code"]
        == "budget_exhausted"
    )
    files = list(tmp_path.glob("step-*.json"))
    assert len(files) == 1
    files[0].write_text("{}", encoding="utf-8")
    with pytest.raises(ObservationError):
        ObservationLedger(**args).record(event(), resolver=resolver)


def test_unknown_requires_existing_authorized_verification(tmp_path):
    ledger = ObservationLedger()
    r = ledger.record(
        {**event(), "notification_state": "unknown"},
        resolver=lambda e: {
            "authorized": True,
            "source_version": e["source_version"],
            "notification_state": "unknown",
        },
    )
    assert r["action_target"] is None
    assert r["would_notify"] is False


def test_failed_atomic_write_preserves_previous_checkpoint(tmp_path, monkeypatch):
    from app.assistant import observation

    ledger = ObservationLedger(
        directory=tmp_path,
        scope={"company_id": "c", "book_id": "b", "period": "2026-10"},
        principal="p",
    )
    first = ledger.record(event(), resolver=resolver)
    monkeypatch.setattr(
        observation, "atomic_create", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError):
        ledger.record({**event(), "event_id": "e2"}, resolver=resolver)
    assert ledger.record(event(), resolver=resolver) == first


def test_exhausted_budget_stops_before_diagnostic_read(tmp_path):
    calls = []

    def counted(e):
        calls.append(e["event_id"])
        return resolver(e)

    args = dict(
        directory=tmp_path,
        scope={"company_id": "c", "book_id": "b", "period": "2026-10"},
        principal="p",
        max_events=1,
    )
    ObservationLedger(**args).record(event(), resolver=counted)
    assert (
        ObservationLedger(**args).record({**event(), "event_id": "e2"}, resolver=counted)[
            "reason_code"
        ]
        == "budget_exhausted"
    )
    assert calls == ["e1"]


def _process_record(directory, ready, start, result):
    try:
        ledger = ObservationLedger(
            directory=directory,
            scope={"company_id": "c", "book_id": "b", "period": "2026-10"},
            principal="p",
        )
        ready.put(True)
        if not start.wait(15):
            raise RuntimeError("barrier timeout")
        result.put(ledger.record(event(), resolver=resolver))
    except Exception as error:
        result.put({"error": type(error).__name__})


def test_two_processes_restart_cannot_duplicate_or_rewrite(tmp_path):
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    ready, result, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    children = [
        ctx.Process(target=_process_record, args=(str(tmp_path), ready, start, result))
        for _ in range(2)
    ]
    try:
        for child in children:
            child.start()
        assert all(ready.get(timeout=20) for _ in children)
        start.set()
        records = [result.get(timeout=20) for _ in children]
        assert records[0] == records[1] and records[0]["would_handoff"]
        for child in children:
            child.join(20)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
    assert len(list(tmp_path.glob("step-*.json"))) == 1
    restored = ObservationLedger(
        directory=tmp_path,
        scope={"company_id": "c", "book_id": "b", "period": "2026-10"},
        principal="p",
    )
    with pytest.raises(ObservationError):
        restored.record({**event(), "event_version": 2}, resolver=resolver)
