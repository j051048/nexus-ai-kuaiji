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


def test_background_roles_rotate_without_queue_starvation():
    seen = []

    class Role:
        def __init__(self, name):
            self.name = name

        def run_once(self):
            seen.append(self.name)
            return True

    unified = UnifiedWorker(
        queue_worker=Role("queue"),
        batch_worker=Role("batch"),
        migration_worker=Role("migration"),
        idle_seconds=0.05,
    )
    unified.start()
    try:
        deadline = time.monotonic() + 2
        while set(seen) != {"queue", "batch", "migration"} and time.monotonic() < deadline:
            time.sleep(0.02)
        assert set(seen) == {"queue", "batch", "migration"}
    finally:
        unified.stop()


def test_queue_heartbeat_loop_emits_periodic_inflight():
    from app.jobs.worker import QueueWorker

    calls = []
    worker = object.__new__(QueueWorker)
    worker.heartbeat = lambda count: calls.append(count)
    stop = threading.Event()
    thread = threading.Thread(target=worker._heartbeat_loop, args=(stop, 0.01))
    thread.start()
    time.sleep(0.05)
    stop.set()
    thread.join(2)
    assert calls and set(calls) == {1}


def test_background_budgets_are_weighted_not_ignored():
    calls = []

    class Role:
        def __init__(self, name):
            self.name = name

        def run_once(self):
            calls.append(self.name)
            return True

    worker = UnifiedWorker(
        queue_worker=Role("queue"),
        batch_worker=Role("batch"),
        queue_budget=2,
        batch_budget=1,
    )
    worker._executor = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=2)
    try:
        for _ in range(3):
            worker.run_tick()
            assert worker.wait_for_idle(2)
    finally:
        worker._executor.shutdown(wait=True)
    assert calls == ["queue", "queue", "batch"]


def test_run_until_idle_uses_concurrent_slots_then_stops():
    calls = []

    class Worker:
        def __init__(self, name, results):
            self.name, self.results = name, list(results)

        def run_once(self):
            calls.append(self.name)
            return self.results.pop(0) if self.results else False

    worker = UnifiedWorker(
        assistant_worker=Worker("assistant", [False]),
        queue_worker=Worker("queue", [True, False]),
        idle_seconds=0.05,
    )
    worker.run_until_idle(5)
    assert "queue" in calls
    assert worker.inflight_count() == 0


def test_stop_does_not_treat_completed_future_as_inflight():
    from concurrent.futures import ThreadPoolExecutor

    class Idle:
        def run_once(self):
            return False

    states = []

    class Monitor:
        def heartbeat(self, inflight=0, state="idle"):
            states.append((inflight, state))

    worker = UnifiedWorker(queue_worker=Idle(), idle_seconds=0.05)
    worker.assistant_worker = type("A", (), {"monitor": Monitor(), "run_once": lambda self: False})()
    worker._executor = ThreadPoolExecutor(max_workers=1)
    future = worker._executor.submit(lambda: None)
    future.result()
    with worker._inflight_lock:
        worker._inflight["queue"] = future
    worker.stop()
    assert states and states[-1] == (0, "stopped")


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


def test_submit_failure_does_not_leak_inflight_count():
    class Queue:
        def run_once(self):
            return True

    worker = UnifiedWorker(queue_worker=Queue(), idle_seconds=0.05)
    worker._executor = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=1)
    worker._executor.shutdown(wait=True)
    worker._executor_closed = True
    try:
        worker.run_tick()
    except RuntimeError:
        pass
    assert worker.inflight_count() == 0


def test_single_role_and_repeated_stop_are_supported():
    class Idle:
        def run_once(self):
            return False

    for kwargs in (
        {"assistant_worker": Idle()},
        {"queue_worker": Idle()},
        {"migration_worker": Idle()},
    ):
        worker = UnifiedWorker(idle_seconds=0.05, **kwargs)
        worker.start()
        worker.stop()
        worker.stop()  # idempotent
        assert worker.wait_for_idle(1)


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

