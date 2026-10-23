"""Offline regressions for scheduler, monitoring and claimed-work lifecycle."""

import threading
import time

from app.unified_worker import UnifiedWorker


def eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


class Lane:
    def __init__(self, result=False):
        self.calls = 0
        self.result = result
        self.entered = threading.Event()

    def run_once(self):
        self.calls += 1
        self.entered.set()
        return self.result


def test_empty_lanes_obey_idle_interval_instead_of_spinning():
    assistant, queue = Lane(), Lane()
    worker = UnifiedWorker(assistant_worker=assistant, queue_worker=queue, idle_seconds=1)
    worker.start()
    try:
        assert queue.entered.wait(1) and assistant.entered.wait(1)
        time.sleep(0.15)
        assert assistant.calls == queue.calls == 1
    finally:
        worker.stop()
        assert worker.wait_for_idle(2)


def test_slow_monitor_cannot_block_dispatch_and_must_be_drained():
    entered, release = threading.Event(), threading.Event()

    class Monitor:
        def heartbeat(self, **kwargs):
            entered.set()
            assert release.wait(5)

    assistant, queue = Lane(), Lane()
    assistant.monitor = Monitor()
    worker = UnifiedWorker(assistant_worker=assistant, queue_worker=queue, idle_seconds=0.05)
    worker.start()
    try:
        assert entered.wait(1)
        assert queue.entered.wait(0.5), "heartbeat blocked dispatch"
        worker.stop()
        assert not worker.wait_for_idle(0.02), "monitor still owns database resources"
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(2)


def test_drain_reports_each_lane_and_keeps_periodic_heartbeats():
    release, started = threading.Event(), threading.Event()
    assistant_states, queue_states = [], []

    class Monitor:
        def heartbeat(self, inflight=0, state="idle"):
            assistant_states.append((inflight, state))

    assistant = Lane()
    assistant.monitor = Monitor()

    class Queue:
        heartbeat_seconds = 0.02

        def run_once(self):
            started.set()
            assert release.wait(5)
            return True

        def inflight_count(self):
            return int(started.is_set() and not release.is_set())

        def heartbeat(self, count=0):
            queue_states.append((count, "active" if count else "idle"))

        def maintenance(self, count=0, stopped=False):
            queue_states.append((count, "stopped" if stopped else "active"))

    worker = UnifiedWorker(assistant_worker=assistant, queue_worker=Queue(), idle_seconds=0.05)
    worker.monitor_interval = 0.02
    worker.start()
    try:
        assert started.wait(1)
        worker.stop()
        before = len(queue_states)
        assert eventually(lambda: len(queue_states) >= before + 2, 0.5)
        assert all(count == 0 for count, _ in assistant_states)
        assert not any(state == "stopped" for _, state in queue_states)
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(2)
    assert queue_states[-1] == (0, "stopped")
    assert assistant_states[-1] == (0, "stopped")


def test_once_visits_later_queues_but_is_a_bounded_pass():
    queue, batch, migration = Lane(), Lane(True), Lane(True)
    worker = UnifiedWorker(queue_worker=queue, batch_worker=batch, migration_worker=migration)
    try:
        assert worker.run_until_idle(0.5)
        assert queue.calls == 1
        assert batch.calls == 2
        assert migration.calls == 1
    finally:
        worker.stop()
        assert worker.wait_for_idle(2)


def test_claimed_job_survives_observability_failure():
    from app.jobs.worker import QueueWorker

    class Queue:
        db = type("DB", (), {"is_postgres": False})()

        def recover(self):
            pass

        def claim(self, *args):
            return {"id": "claimed"}

    worker = QueueWorker(Queue())
    processed = []
    worker.process = lambda job: processed.append(job["id"])

    def broken_maintenance(*args, **kwargs):
        raise RuntimeError("synthetic observation failure")

    worker.maintenance = broken_maintenance
    worker.heartbeat = lambda count: None
    assert worker.run_once() is True
    assert processed == ["claimed"]
    assert worker.inflight_count() == 0


def test_busy_background_does_not_spin_on_another_due_role():
    entered, release = threading.Event(), threading.Event()

    class Slow(Lane):
        def run_once(self):
            entered.set()
            assert release.wait(5)
            return False

    worker = UnifiedWorker(queue_worker=Lane(True), batch_worker=Slow(), idle_seconds=0.05)
    ticks = []
    tick = worker.run_tick

    def measured():
        ticks.append(time.monotonic())
        return tick()

    worker.run_tick = measured
    worker.start()
    try:
        assert entered.wait(1)
        before = len(ticks)
        time.sleep(0.15)
        assert len(ticks) - before < 8
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(2)


def test_queue_stop_during_maintenance_prevents_new_claim():
    from app.jobs.worker import QueueWorker

    claims = []
    entered, release = threading.Event(), threading.Event()

    class Queue:
        db = type("DB", (), {"is_postgres": False})()

        def recover(self):
            pass

        def claim(self, *args):
            claims.append(True)

    worker = QueueWorker(Queue())

    def maintenance(*args):
        entered.set()
        assert release.wait(5)

    worker.maintenance = maintenance
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    try:
        assert entered.wait(1)
        worker.stop()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert not claims


def test_once_returns_with_monitor_threads_joined():
    class Monitor:
        def heartbeat(self, **kwargs):
            pass

    assistant = Lane()
    assistant.monitor = Monitor()
    worker = UnifiedWorker(assistant_worker=assistant)
    assert not worker.run_until_idle(1)
    assert all(not thread.is_alive() for thread in worker._monitor_threads)
    worker.start()
    worker.stop()
    assert worker.wait_for_idle(2)


def test_repeated_once_prepares_a_new_finite_pass():
    queue = Lane(True)
    worker = UnifiedWorker(queue_worker=queue, queue_budget=1)
    assert worker.run_until_idle(1)
    assert worker.run_until_idle(1)
    assert queue.calls == 2


def test_standalone_queue_slow_heartbeat_does_not_block_claim():
    from app.jobs.worker import QueueWorker

    entered, release, claimed = threading.Event(), threading.Event(), threading.Event()

    class Queue:
        db = type("DB", (), {"is_postgres": False})()

        def recover(self):
            pass

        def claim(self, *args):
            claimed.set()

    worker = QueueWorker(Queue(), poll_seconds=0.2)
    worker.maintenance = lambda *args, **kwargs: None

    def heartbeat(*args):
        entered.set()
        assert release.wait(5)

    worker.heartbeat = heartbeat
    worker.start()
    try:
        assert entered.wait(1)
        assert claimed.wait(0.5)
        worker.stop()
        assert not worker.wait_for_idle(0.02)
    finally:
        release.set()
        worker.stop()
        assert worker.wait_for_idle(2)


def test_processed_work_flushes_alerts_but_idle_polls_do_not_repeat_scans():
    from app.jobs.worker import QueueWorker

    scans = []

    class Queue:
        db = type("DB", (), {"is_postgres": False})()
        claims = 0

        def recover(self):
            pass

        def claim(self, *args):
            self.claims += 1
            return {"id": "synthetic-job"} if self.claims <= 2 else None

    worker = QueueWorker(Queue())
    worker.maintenance = lambda *args: scans.append(True)
    worker.heartbeat = lambda *args: None
    worker.process = lambda job: None
    assert worker.run_once() and worker.run_once()
    assert not worker.run_once()
    assert len(scans) == 3  # initial maintenance + two completed jobs
