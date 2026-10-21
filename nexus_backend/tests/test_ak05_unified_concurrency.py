import threading
import time

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

