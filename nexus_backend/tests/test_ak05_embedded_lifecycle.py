"""Offline lifecycle regression: live workers retain their database until drained."""

import threading
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.legacy_migration.worker as migration_module
import app.jobs.worker as queue_module
import app.unified_worker as unified_module
from app.assistant.worker import AssistantWorker
from app.batch_ops.worker import BatchWorker
from app.legacy_migration.worker import MigrationWorker

QueueWorker = queue_module.QueueWorker


def _application(
    tmp_path,
    monkeypatch,
    *,
    failed_start=None,
    failed_stop=None,
    drain_result=True,
):
    events, closed, workers = [], [], []
    clock = [0.0]
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    monkeypatch.setenv("ACCOUNTING_STORAGE_BACKEND", "local")
    monkeypatch.delenv("ACCOUNTING_PROJECTION_DATABASE_URL", raising=False)
    monkeypatch.setattr(main_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_close = main_module.Database.close

    def close(database):
        closed.append(database)
        events.append(("close",))
        original_close(database)

    monkeypatch.setattr(main_module.Database, "close", close)

    def worker_type(name):
        class Worker:
            def __init__(self, *args, **kwargs):
                self.name = name
                workers.append(self)

            def start(self):
                events.append(("start", name))
                if failed_start == name:
                    raise RuntimeError("injected startup failure")

            def stop(self):
                events.append(("stop", name))
                if failed_stop == name:
                    raise RuntimeError("injected stop failure")

            def wait_for_idle(self, timeout):
                events.append(("wait", name, timeout))
                clock[0] += 7
                if isinstance(drain_result, Exception):
                    raise drain_result
                return drain_result

        return Worker

    for attribute in ("QueueWorker", "MigrationWorker", "AssistantWorker", "BatchWorker"):
        monkeypatch.setattr(main_module, attribute, worker_type(attribute))
    application = main_module.create_app(
        tmp_path / "embedded.db",
        seed=False,
        attachment_root=tmp_path / "originals",
        queue_worker_enabled=True,
    )
    return application, events, closed, workers


def _client(application):
    return TestClient(application, base_url="http://127.0.0.1")


def test_stop_all_before_shared_deadline_drain_then_close(tmp_path, monkeypatch):
    application, events, closed, _ = _application(tmp_path, monkeypatch)
    with _client(application):
        pass
    waits = [event for event in events if event[0] == "wait"]
    assert len(waits) == 4, events
    first_wait = next(index for index, event in enumerate(events) if event[0] == "wait")
    assert len([event for event in events[:first_wait] if event[0] == "stop"]) == 4
    assert [event[2] for event in waits] == [30, 23, 16, 9]
    assert events[-1] == ("close",)
    assert len(closed) == 1


@pytest.mark.parametrize("drain_result", [False, RuntimeError("drain failed")])
def test_incomplete_drain_preserves_database(tmp_path, monkeypatch, drain_result):
    application, events, closed, _ = _application(
        tmp_path,
        monkeypatch,
        drain_result=drain_result,
    )
    with _client(application):
        pass
    assert len([event for event in events if event[0] == "wait"]) == 4
    assert application.state.unified_drain_incomplete is True
    assert not closed, "Live worker threads must retain their database"


def test_partial_start_failure_stops_and_drains_constructed_workers(tmp_path, monkeypatch):
    application, events, closed, workers = _application(
        tmp_path,
        monkeypatch,
        failed_start="AssistantWorker",
    )
    with pytest.raises(RuntimeError, match="injected startup failure"):
        with _client(application):
            pass
    assert len(workers) == 3
    assert {event[1] for event in events if event[0] == "stop"} == {w.name for w in workers}
    assert len([event for event in events if event[0] == "wait"]) == 3
    assert len(closed) == 1


def test_stop_error_still_signals_and_waits_all_lanes_without_closing_database(
    tmp_path, monkeypatch
):
    application, events, closed, _ = _application(
        tmp_path,
        monkeypatch,
        failed_stop="BatchWorker",
    )
    with _client(application):
        pass
    assert len([event for event in events if event[0] == "stop"]) == 4
    assert len([event for event in events if event[0] == "wait"]) == 4
    assert application.state.unified_drain_incomplete
    assert not closed


def test_restore_hold_check_failure_closes_unstarted_database(tmp_path, monkeypatch):
    application, events, closed, _ = _application(tmp_path, monkeypatch)

    def reject(database):
        raise RuntimeError("isolated restore hold")

    monkeypatch.setattr(main_module.Database, "assert_restore_hold", reject)
    with pytest.raises(RuntimeError, match="isolated restore hold"):
        with _client(application):
            pass
    assert events == [("close",)]
    assert len(closed) == 1


def test_projection_initialization_failure_closes_primary_database(tmp_path, monkeypatch):
    application, events, closed, _ = _application(tmp_path, monkeypatch)
    database_type = main_module.Database

    def database(path, **kwargs):
        if kwargs.get("default_role") == "accounting_projection":
            raise RuntimeError("isolated projection startup failure")
        return database_type(path, **kwargs)

    monkeypatch.setenv("ACCOUNTING_PROJECTION_DATABASE_URL", "postgresql://not-used.invalid/db")
    monkeypatch.setattr(main_module, "Database", database)
    with pytest.raises(RuntimeError, match="isolated projection startup failure"):
        with _client(application):
            pass
    assert events == [("close",)]
    assert len(closed) == 1


@pytest.mark.parametrize(
    "worker_type", [AssistantWorker, BatchWorker, MigrationWorker, QueueWorker]
)
def test_stop_is_signal_only_and_wait_for_idle_tracks_live_thread(worker_type):
    service = SimpleNamespace(db=SimpleNamespace(is_postgres=False))
    worker = (
        worker_type(service, None, None, monitor=SimpleNamespace(tick=lambda **kwargs: None))
        if worker_type is AssistantWorker
        else worker_type(service)
    )
    entered, release = threading.Event(), threading.Event()

    def run():
        entered.set()
        release.wait(3)

    worker._loop = run
    worker.start()
    try:
        assert entered.wait(1)
        stopper = threading.Thread(target=worker.stop)
        stopper.start()
        stopper.join(0.2)
        assert not stopper.is_alive(), "stop() must signal every lane before waiting"
        assert worker.wait_for_idle(0.01) is False
    finally:
        release.set()
        worker._thread.join(2)
    assert worker.wait_for_idle(0.2) is True


def test_assistant_inflight_excludes_claim_and_decrements_after_failure(monkeypatch):
    worker = AssistantWorker(SimpleNamespace(db=object()), None, None, monitor=object())
    claimed, release_claim = threading.Event(), threading.Event()
    processing, release_processing = threading.Event(), threading.Event()
    caught = []

    def claim():
        claimed.set()
        release_claim.wait(2)
        return {"id": "synthetic-turn"}

    def process(turn, started_at):
        processing.set()
        release_processing.wait(2)
        raise RuntimeError("injected processing failure")

    monkeypatch.setattr(worker, "claim", claim)
    monkeypatch.setattr(worker, "_process_claimed", process, raising=False)

    def run():
        try:
            worker.run_once()
        except Exception as error:
            caught.append(str(error))

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert claimed.wait(1)
        assert worker.inflight_count() == 0
        release_claim.set()
        assert processing.wait(1)
        assert worker.inflight_count() == 1
    finally:
        release_claim.set()
        release_processing.set()
        thread.join(2)
    assert caught == ["injected processing failure"]
    assert worker.inflight_count() == 0


@pytest.mark.parametrize("worker_type", [AssistantWorker, MigrationWorker])
def test_executor_children_are_included_in_drain(worker_type, monkeypatch):
    service = SimpleNamespace(db=object())
    worker = (
        worker_type(service, None, None, monitor=SimpleNamespace(tick=lambda **kwargs: None))
        if worker_type is AssistantWorker
        else worker_type(service)
    )
    worker.poll_seconds = 0.01
    entered, release = threading.Event(), threading.Event()

    def execute(*args):
        entered.set()
        release.wait(3)

    if worker_type is AssistantWorker:
        monkeypatch.setattr(worker, "run_once", execute)
    else:
        claimed = [False]

        def claim():
            if claimed[0]:
                return None
            claimed[0] = True
            return {"id": "synthetic-migration"}

        monkeypatch.setattr(worker, "claim", claim)
        monkeypatch.setattr(worker, "execute", execute)
    worker.start()
    try:
        assert entered.wait(1)
        worker.stop()
        assert worker.wait_for_idle(0.01) is False
    finally:
        release.set()
        assert worker.wait_for_idle(2) is True


@pytest.mark.parametrize(
    "worker_type", [AssistantWorker, BatchWorker, MigrationWorker, QueueWorker]
)
def test_stopped_lane_does_not_claim_until_explicit_prepare(worker_type, monkeypatch):
    claims = []
    service = SimpleNamespace(
        db=SimpleNamespace(is_postgres=False),
        recover=lambda: None,
        claim=lambda *args: claims.append("claim"),
    )
    worker = (
        worker_type(service, None, None, monitor=object())
        if worker_type is AssistantWorker
        else worker_type(service)
    )
    if worker_type not in {BatchWorker, QueueWorker}:
        monkeypatch.setattr(worker, "claim", lambda: claims.append("claim"))
    if worker_type is QueueWorker:
        monkeypatch.setattr(worker, "maintenance", lambda *args, **kwargs: None)
    worker.stop()
    worker.run_once()
    assert not claims
    worker.prepare_run()
    worker.run_once()
    assert claims == ["claim"]


def test_batch_stop_during_recovery_prevents_later_claim():
    claims = []
    service = SimpleNamespace(db=object(), claim=lambda *args: claims.append("claim"))
    worker = BatchWorker(service)
    service.recover = worker.stop
    assert worker.run_once() is False
    assert not claims


@pytest.mark.parametrize(
    "worker_type", [AssistantWorker, BatchWorker, MigrationWorker, QueueWorker]
)
def test_prepare_refuses_live_dispatcher_and_allows_drained_restart(worker_type):
    service = SimpleNamespace(db=SimpleNamespace(is_postgres=False))
    worker = (
        worker_type(service, None, None, monitor=object())
        if worker_type is AssistantWorker
        else worker_type(service)
    )
    entered, release = threading.Event(), threading.Event()

    def run():
        entered.set()
        release.wait(3)

    worker._loop = run
    worker.start()
    try:
        assert entered.wait(1)
        worker.stop()
        with pytest.raises(RuntimeError):
            worker.prepare_run()
        assert worker._stop.is_set()
    finally:
        release.set()
        assert worker.wait_for_idle(2)
    worker.prepare_run()
    assert not worker._stop.is_set()


def test_real_embedded_workers_start_and_drain_before_database_closes(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    monkeypatch.delenv("ACCOUNTING_PROJECTION_DATABASE_URL", raising=False)
    application = main_module.create_app(
        tmp_path / "real-lifecycle.db",
        seed=False,
        attachment_root=tmp_path / "originals",
        queue_worker_enabled=True,
    )
    with _client(application):
        workers = [
            application.state.queue_worker,
            application.state.assistant_worker,
            application.state.batch_worker,
            application.state.migration_worker,
        ]
        assert all(worker._thread.is_alive() for worker in workers)
    assert not application.state.unified_drain_incomplete
    assert all(not worker._thread.is_alive() for worker in workers)
    assert application.state.service.db._closed


@pytest.mark.parametrize("start_fails", [False, True])
@pytest.mark.parametrize("drained", [False, True, RuntimeError("drain failed")])
@pytest.mark.parametrize("worker_module", [migration_module, queue_module])
def test_worker_cli_cleans_partial_start_and_respects_drain(
    tmp_path,
    monkeypatch,
    start_fails,
    drained,
    worker_module,
):
    import app.accounting.database as database_module
    import app.storage.factory as storage_module
    import app.legacy_migration.service as service_module

    events = []

    class Database:
        def __init__(self, url):
            pass

        def close(self):
            events.append("close")

        def assert_restore_hold(self):
            pass

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            events.append("start")
            if start_fails:
                raise RuntimeError("partial start")

        def stop(self):
            events.append("stop")

        def wait_for_idle(self, timeout):
            assert timeout == 30
            events.append("wait")
            if isinstance(drained, Exception):
                raise drained
            return drained

    def ready():
        raise RuntimeError("finish offline lifecycle test")

    monkeypatch.setattr(sys, "argv", ["migration-worker"])
    monkeypatch.setenv("ACCOUNTING_WORKER_DATABASE_URL", str(tmp_path / "offline.db"))
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    for name in ("APP_ENV", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(database_module, "Database", Database)
    monkeypatch.setattr(
        storage_module,
        "create_attachment_store",
        lambda root: SimpleNamespace(
            verify_private=lambda: None,
        ),
    )
    monkeypatch.setattr(service_module, "LegacyMigrationService", lambda *args: object())
    if worker_module is migration_module:
        monkeypatch.setattr(migration_module, "MigrationWorker", Worker)
    else:
        import app.jobs.restore_hold as hold_module
        import app.intelligence.service as intelligence_module

        monkeypatch.setattr(hold_module, "enforce_restore_hold", lambda: None)
        monkeypatch.setattr(intelligence_module, "IntelligenceService", lambda *args: object())
        monkeypatch.setattr(queue_module, "QueueService", lambda *args: object())
        monkeypatch.setattr(queue_module, "QueueWorker", Worker)
    assert worker_module.main(ready_callback=ready) == 1
    assert events == ["start", "stop", "wait"] + (["close"] if drained is True else [])


@pytest.mark.parametrize("mode", ["normal", "partial_start", "once"])
@pytest.mark.parametrize("drained", [True, False, RuntimeError("unknown drain")])
def test_unified_cli_drains_normal_once_and_partial_start(tmp_path, monkeypatch, mode, drained):
    import signal
    import app.accounting.database as database_module
    import app.storage.factory as storage_module
    import app.intelligence.service as intelligence_module
    import app.jobs.service as queue_service_module
    import app.jobs.restore_hold as hold_module

    events = []

    class Database:
        is_postgres = False

        def __init__(self, *args, **kwargs):
            pass

        def assert_restore_hold(self):
            pass

        def close(self):
            events.append("close")

    class Worker:
        def __init__(self, **kwargs):
            pass

        def start(self):
            events.append("start")
            if mode == "partial_start":
                raise RuntimeError("partial start")

        def run_until_idle(self):
            events.append("once")

        def stop(self):
            events.append("stop")

        def wait_for_idle(self, timeout):
            assert timeout == 30
            events.append("wait")
            if isinstance(drained, Exception):
                raise drained
            return drained

    monkeypatch.setattr(sys, "argv", ["unified-worker"] + (["--once"] if mode == "once" else []))
    monkeypatch.setenv("ACCOUNTING_WORKER_DATABASE_URL", str(tmp_path / "unified-offline.db"))
    monkeypatch.setenv("ACCOUNTING_ENV", "test")
    monkeypatch.setenv("ACCOUNTING_UNIFIED_ROLES", "queue")
    for name in ("APP_ENV", "ENV", "ENVIRONMENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(database_module, "Database", Database)
    monkeypatch.setattr(hold_module, "enforce_restore_hold", lambda: None)
    monkeypatch.setattr(
        storage_module,
        "create_attachment_store",
        lambda root: SimpleNamespace(
            verify_private=lambda: None,
        ),
    )
    monkeypatch.setattr(intelligence_module, "IntelligenceService", lambda *args: object())
    monkeypatch.setattr(queue_service_module, "QueueService", lambda *args: object())
    monkeypatch.setattr(queue_module, "QueueWorker", lambda *args, **kwargs: object())
    monkeypatch.setattr(unified_module, "UnifiedWorker", Worker)
    monkeypatch.setattr(
        unified_module,
        "threading",
        SimpleNamespace(
            Event=lambda: SimpleNamespace(wait=lambda seconds: True),
        ),
    )
    monkeypatch.setattr(signal, "signal", lambda *args: None)
    expected = 1 if mode == "partial_start" else 0
    assert unified_module.main() == expected
    assert events == (["once"] if mode == "once" else ["start"]) + ["stop", "wait"] + (
        ["close"] if drained is True else []
    )


def _standalone_assistant(monitor):
    worker = AssistantWorker(SimpleNamespace(db=object()), None, None, monitor=monitor)
    worker.poll_seconds = 0.01
    worker.monitor_interval = 0.02
    return worker


def test_standalone_assistant_slow_heartbeat_does_not_block_first_task(monkeypatch):
    heartbeat_entered, release, processed = threading.Event(), threading.Event(), threading.Event()

    class Monitor:
        def heartbeat(self, **kwargs):
            heartbeat_entered.set()
            release.wait(3)

        def tick(self, **kwargs):
            self.heartbeat(**kwargs)

    worker = _standalone_assistant(Monitor())
    monkeypatch.setattr(worker, "run_once", lambda: processed.set())
    worker.start()
    try:
        assert heartbeat_entered.wait(1)
        assert processed.wait(0.3), "Database heartbeat must not block task submission"
    finally:
        worker.stop()
        release.set()
        assert worker.wait_for_idle(2)


def test_standalone_assistant_slow_refresh_does_not_block_heartbeat(monkeypatch):
    refresh_entered, release, repeated = threading.Event(), threading.Event(), threading.Event()
    observations = []

    class Monitor:
        def heartbeat(self, **kwargs):
            observations.append(kwargs)
            if len(observations) >= 3:
                repeated.set()

        def refresh(self):
            refresh_entered.set()
            release.wait(3)

        def tick(self, **kwargs):
            self.heartbeat(**kwargs)
            self.refresh()

    worker = _standalone_assistant(Monitor())
    monkeypatch.setattr(worker, "run_once", lambda: None)
    worker.start()
    try:
        assert refresh_entered.wait(1)
        assert repeated.wait(0.4), "Alert refresh must not block periodic liveness writes"
    finally:
        worker.stop()
        release.set()
        assert worker.wait_for_idle(2)


def test_standalone_assistant_keeps_real_heartbeat_while_draining(monkeypatch):
    processing, release, draining_heartbeats = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    observations = []
    worker = None

    class Monitor:
        def heartbeat(self, **kwargs):
            observations.append(kwargs)
            if (
                worker._stop.is_set()
                and sum(item == {"inflight": 1, "state": "active"} for item in observations) >= 3
            ):
                draining_heartbeats.set()

        def tick(self, **kwargs):
            self.heartbeat(**kwargs)

    worker = _standalone_assistant(Monitor())
    monkeypatch.setattr(worker, "claim", lambda: {"id": "synthetic-turn"})

    def process(turn, started_at):
        processing.set()
        release.wait(3)
        return turn["id"]

    monkeypatch.setattr(worker, "_process_claimed", process)
    worker.start()
    try:
        assert processing.wait(1)
        worker.stop()
        assert worker.wait_for_idle(0.01) is False
        assert draining_heartbeats.wait(0.4), "Draining work must retain periodic active heartbeat"
    finally:
        release.set()
        assert worker.wait_for_idle(2)
    assert observations[-1] == {"inflight": 0, "state": "stopped"}


def test_standalone_assistant_drain_includes_final_monitor_database_write(monkeypatch):
    first_tick, stopped_write, release = threading.Event(), threading.Event(), threading.Event()

    class Monitor:
        def heartbeat(self, **kwargs):
            first_tick.set()
            if kwargs["state"] == "stopped":
                stopped_write.set()
                release.wait(3)

        def tick(self, **kwargs):
            self.heartbeat(**kwargs)

    worker = _standalone_assistant(Monitor())
    monkeypatch.setattr(worker, "run_once", lambda: None)
    worker.start()
    try:
        assert first_tick.wait(1)
        worker.stop()
        assert stopped_write.wait(1)
        assert worker.wait_for_idle(0.01) is False
    finally:
        release.set()
        assert worker.wait_for_idle(2)


def test_assistant_external_claimed_call_prevents_false_idle(monkeypatch):
    processing, release = threading.Event(), threading.Event()
    worker = _standalone_assistant(object())
    monkeypatch.setattr(worker, "claim", lambda: {"id": "synthetic-turn"})

    def process(turn, started_at):
        processing.set()
        release.wait(3)

    monkeypatch.setattr(worker, "_process_claimed", process)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    try:
        assert processing.wait(1)
        assert worker.wait_for_idle(0.01) is False
    finally:
        release.set()
        thread.join(2)
    assert worker.wait_for_idle(0.2)
