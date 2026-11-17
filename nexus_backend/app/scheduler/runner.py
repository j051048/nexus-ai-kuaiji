"""Small scheduler loop, deliberately separate from request processing."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class SchedulerLoop:
    def __init__(self, service, interval_seconds: float = 30.0):
        if not 1 <= float(interval_seconds) <= 300:
            raise ValueError("scheduler interval must be 1..300 seconds")
        self.service = service
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._thread = None

    def run_once(self):
        return self.service.tick()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(self.interval_seconds):
                try:
                    self.run_once()
                except Exception:
                    logger.exception("scheduler tick failed; next tick will retry")

        self._thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
