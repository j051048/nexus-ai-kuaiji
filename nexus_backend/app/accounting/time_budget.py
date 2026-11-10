"""Opt-in, request-local DB wait ceiling; never changes a shared pool setting."""

from contextlib import contextmanager
from contextvars import ContextVar
import time

_deadline = ContextVar("database_deadline", default=None)


def remaining():
    value = _deadline.get()
    if value is None:
        return None
    seconds = value - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("Local read budget exhausted")
    return seconds


@contextmanager
def database_deadline(deadline):
    prior = _deadline.get()
    token = _deadline.set(min(prior, deadline) if prior is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)
