import threading
import time

import pytest

from app.unified_worker import UnifiedWorker


class BlockingWorker:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def run_once(self):
        self.started.set()
        self.release.wait(5)
        return True


class AssistantWorker:
    def __init__(self):
        self.done = threading.Event()

    def run_once(self):
        self.done.set()
        return True


def test_slow_background_does_not_block_assistant():
    background = BlockingWorker()
    assistant = AssistantWorker()
    worker = UnifiedWorker(
        assistant_worker=assistant,
        queue_worker=background,
        idle_seconds=0.05,
    )
    worker.start()
    try:
        assert background.started.wait(2), "后台慢任务未启动"
        assert assistant.done.wait(2), "助手被慢后台任务阻塞"
    finally:
        background.release.set()
        worker.stop()


def test_heartbeat_reports_real_assistant_inflight():
    background = BlockingWorker()
    assistant = BlockingWorker()
    calls = []

    class Monitor:
        def heartbeat(self, inflight=0, state="idle"):
            calls.append((inflight, state))

    worker = UnifiedWorker(
        assistant_worker=assistant,
        queue_worker=background,
        idle_seconds=0.05,
    )
    worker.assistant_worker.monitor = Monitor()
    worker.start()
    try:
        assert background.started.wait(2)
        assert assistant.started.wait(2)
        worker._monitor_tick("active")
        assert calls and calls[-1] == (1, "active")
    finally:
        background.release.set()
        assistant.release.set()
        worker.stop()


def test_inflight_is_bounded_to_two_even_with_many_queues():
    class Blocking:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def run_once(self):
            self.started.set()
            self.release.wait(5)
            return True

    workers = [Blocking() for _ in range(4)]
    unified = UnifiedWorker(
        assistant_worker=workers[0],
        queue_worker=workers[1],
        batch_worker=workers[2],
        migration_worker=workers[3],
        idle_seconds=0.05,
    )
    unified.start()
    try:
        assert workers[0].started.wait(2) and workers[1].started.wait(2)
        assert unified.inflight_count() <= 2
    finally:
        for worker in workers:
            worker.release.set()
        unified.stop()


def test_future_exception_is_consumed_and_next_tick_recovers():
    class Flaky:
        def __init__(self):
            self.calls = 0

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic task failure")
            return True

    flaky = Flaky()
    unified = UnifiedWorker(queue_worker=flaky, idle_seconds=0.05)
    assert unified.run_once() is False or True
    unified.start()
    try:
        deadline = time.monotonic() + 2
        while flaky.calls < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert flaky.calls >= 2
    finally:
        unified.stop()


def test_monitor_failure_does_not_stop_queue_processing():
    class BrokenMonitor:
        def heartbeat(self, inflight=0, state="idle"):
            raise RuntimeError("synthetic monitor db failure")

    class Assistant:
        def __init__(self):
            self.monitor = BrokenMonitor()

        def run_once(self):
            return False

    class Queue:
        def __init__(self):
            self.calls = 0

        def run_once(self):
            self.calls += 1
            return self.calls == 1

    queue = Queue()
    unified = UnifiedWorker(assistant_worker=Assistant(), queue_worker=queue, idle_seconds=0.05)
    unified._monitor_tick("active")
    unified.start()
    try:
        deadline = time.monotonic() + 2
        while queue.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert queue.calls >= 1
    finally:
        unified.stop()


def test_invalid_budget_types_are_rejected():
    for kwargs in (
        {"assistant_budget": True},
        {"queue_budget": -1},
        {"batch_budget": 1.5},
        {"migration_budget": 11},
        {"idle_seconds": True},
    ):
        with pytest.raises(ValueError):
            UnifiedWorker(queue_worker=BlockingWorker(), **kwargs)


def test_clean_stop_can_restart_and_draining_stop_cannot():
    class Idle:
        def run_once(self):
            return False

    unified = UnifiedWorker(queue_worker=Idle(), idle_seconds=0.05)
    unified.start()
    unified.stop()
    assert unified.wait_for_idle(1)
    unified.start()
    unified.stop()

    blocker = BlockingWorker()
    blocked = UnifiedWorker(queue_worker=blocker, idle_seconds=0.05)
    blocked.start()
    assert blocker.started.wait(2)
    blocked.stop()
    try:
        blocked.start()
    except RuntimeError:
        pass
    else:
        raise AssertionError("draining 时不得启动第二套调度")
    blocker.release.set()
    assert blocked.wait_for_idle(2)


def test_stop_does_not_report_stopped_while_task_running():
    background = BlockingWorker()
    assistant = AssistantWorker()
    calls = []

    class Monitor:
        def heartbeat(self, inflight=0, state="idle"):
            calls.append((inflight, state))

    worker = UnifiedWorker(
        assistant_worker=assistant,
        queue_worker=background,
        idle_seconds=0.05,
    )
    worker.assistant_worker.monitor = Monitor()
    worker.start()
    assert background.started.wait(2)
    worker.stop()
    assert not any(state == "stopped" for _count, state in calls)
    background.release.set()

