"""统一后台：优先级、每轮预算、异常隔离与停止心跳。"""

import time

import pytest

from app.unified_worker import UnifiedWorker


class RecordingWorker:
    def __init__(self, name, results, log):
        self.name, self.results, self.log = name, list(results), log

    def run_once(self):
        if self.results:
            result = self.results.pop(0)
            if result:
                self.log.append(self.name)
            return result
        return False


class FakeMonitor:
    def __init__(self):
        self.ticks = []

    def tick(self, **values):
        self.ticks.append(values)


class FakeQueue:
    def __init__(self, results):
        self.results = list(results)
        self.stopped_flushes = 0

    def run_once(self):
        return self.results.pop(0) if self.results else False

    def maintenance(self, inflight=0, stopped=False):
        if stopped:
            self.stopped_flushes += 1


def test_priority_order_and_budgets_per_pass():
    log = []
    assistant = RecordingWorker("assistant", [True], log)
    queue = RecordingWorker("queue", [True, True, True], log)
    migration = RecordingWorker("migration", [True], log)
    worker = UnifiedWorker(
        assistant_worker=assistant, queue_worker=queue, migration_worker=migration
    )
    assert worker.run_once() is True
    # 高优助手先跑；单张票据每轮最多 2 件；迁移最后且每轮 1 件。
    assert log == ["assistant", "queue", "queue", "migration"]
    log.clear()
    assistant.results.append(True)
    queue.results.extend([True, True])
    migration.results.append(True)
    assert worker.run_once() is True
    assert log == ["assistant", "queue", "queue", "migration"]


def test_idle_worker_breaks_its_budget_without_blocking_others():
    log = []
    assistant = RecordingWorker("assistant", [], log)
    queue = RecordingWorker("queue", [True], log)
    worker = UnifiedWorker(
        assistant_worker=assistant,
        queue_worker=queue,
        queue_budget=3,
        migration_worker=None,
    )
    assert worker.run_once() is True
    assert log == ["queue"]
    assert worker.run_once() is False


def test_failing_queue_is_isolated_and_later_queues_still_run():
    log = []

    class Exploding:
        def run_once(self):
            log.append("assistant")
            raise RuntimeError("provider down")

    queue = RecordingWorker("queue", [True], log)
    worker = UnifiedWorker(assistant_worker=Exploding(), queue_worker=queue)
    assert worker.run_once() is True
    assert log == ["assistant", "queue"]


def test_constructor_requires_a_queue_and_bounded_budgets():
    with pytest.raises(ValueError, match="至少需要一类队列"):
        UnifiedWorker()
    with pytest.raises(ValueError, match="预算"):
        UnifiedWorker(queue_worker=RecordingWorker("queue", [], []), queue_budget=0)
    with pytest.raises(ValueError, match="空闲轮询"):
        UnifiedWorker(queue_worker=RecordingWorker("queue", [], []), idle_seconds=60)


def test_stop_flushes_queue_maintenance_and_assistant_monitor():
    queue = FakeQueue([False])
    monitor = FakeMonitor()

    class Assistant:
        def __init__(self):
            self.monitor = monitor

        def run_once(self):
            return False

    worker = UnifiedWorker(assistant_worker=Assistant(), queue_worker=queue)
    worker.stop()
    assert worker.wait_for_idle(2)
    assert queue.stopped_flushes == 1
    assert monitor.ticks[-1] == {"inflight": 0, "state": "stopped"}


def test_loop_polls_until_stopped():
    log = []
    worker = UnifiedWorker(
        queue_worker=RecordingWorker("queue", [True] * 200, log), idle_seconds=0.05
    )
    worker.start()
    deadline = time.monotonic() + 2
    while len(log) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    worker.stop()
    assert worker.wait_for_idle(2)
    assert len(log) >= 3
    settled = len(log)
    time.sleep(0.15)
    assert len(log) == settled


def test_slow_background_preparation_does_not_block_the_assistant_lane():
    import threading

    background_started = threading.Event()
    release = threading.Event()
    assistant_ran = threading.Event()

    class Slow:
        def run_once(self):
            background_started.set()
            release.wait(3)
            return True

    class Fast:
        def run_once(self):
            assistant_ran.set()
            return True

    worker = UnifiedWorker(
        assistant_worker=Fast(), queue_worker=Slow(), idle_seconds=0.05
    )
    worker.start()
    try:
        assert background_started.wait(2)
        assert assistant_ran.wait(2)
    finally:
        release.set()
        worker.stop()
        worker.wait_for_idle(3)
