"""Minimal Prometheus exposition without pulling in a client library.

The repo already ships a hand-written CSV renderer and a hand-written audit
chain; a metrics registry is the same kind of thing — a dict, a lock, and a text
format. Adding prometheus_client would mean a new dependency for ~80 lines.
"""

from __future__ import annotations

import math
import threading
from typing import Iterable

_LOCK = threading.Lock()
_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}
_HELP: dict[str, tuple[str, str]] = {}

# Latency buckets in seconds; the P50/P95/P99 the task asks for are read off
# these buckets by the scrape side, so the bucket edges are the contract.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def describe(name: str, kind: str, help_text: str) -> None:
    _HELP.setdefault(name, (kind, help_text))


def _key(name: str, labels: dict[str, str] | None):
    if not labels:
        return (name, ())
    return (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))


def increment(name: str, amount: float = 1.0, **labels: str) -> None:
    key = _key(name, labels)
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0.0) + float(amount)


def observe(name: str, value: float, **labels: str) -> None:
    key = _key(name, labels)
    with _LOCK:
        _HISTOGRAMS.setdefault(key, []).append(float(value))


def reset() -> None:
    """Tests only: a global registry would otherwise leak between cases."""
    with _LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(pairs: Iterable[tuple[str, str]]) -> str:
    # Sorted so a scrape diff is stable: same sample, same byte order.
    pairs = sorted(pairs)
    if not pairs:
        return ""
    body = ",".join(f'{name}="{_escape(value)}"' for name, value in pairs)
    return "{" + body + "}"


def _format_value(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value):
        return str(int(value))
    return repr(value)


def render() -> str:
    lines: list[str] = []
    with _LOCK:
        counters = sorted(_COUNTERS.items())
        histograms = sorted(_HISTOGRAMS.items())
    emitted: set[str] = set()
    for (name, pairs), value in counters:
        kind, help_text = _HELP.get(name, ("counter", name))
        if name not in emitted:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            emitted.add(name)
        lines.append(f"{name}{_labels(pairs)} {_format_value(value)}")
    for (name, pairs), values in histograms:
        kind, help_text = _HELP.get(name, ("histogram", name))
        if name not in emitted:
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            emitted.add(name)
        for edge in BUCKETS:
            count = sum(1 for value in values if value <= edge)
            lines.append(f"{name}_bucket{_labels([*pairs, ('le', _format_value(edge))])} {count}")
        lines.append(f"{name}_bucket{_labels([*pairs, ('le', '+Inf')])} {len(values)}")
        lines.append(f"{name}_sum{_labels(pairs)} {_format_value(sum(values))}")
        lines.append(f"{name}_count{_labels(pairs)} {len(values)}")
    return "\n".join(lines) + ("\n" if lines else "")


describe("http_requests_total", "counter", "HTTP requests by method, route and status")
describe(
    "http_request_duration_seconds",
    "histogram",
    "HTTP request latency in seconds",
)


def record_http(method: str, route: str, status: int, seconds: float) -> None:
    # Route is the *template* (``/api/books/{book_id}``), never the raw path with
    # customer IDs in it — a label with a UUID per book would explode cardinality.
    increment("http_requests_total", method=method, route=route, status=str(status))
    observe(
        "http_request_duration_seconds",
        seconds,
        method=method,
        route=route,
    )


describe("accounting_vouchers_posted_total", "counter", "Formal vouchers posted")
describe("tax_workpapers_generated_total", "counter", "Tax workpapers generated")
describe("payment_orders_state_total", "counter", "Payment orders by resulting state")


def voucher_posted() -> None:
    increment("accounting_vouchers_posted_total")


def tax_workpaper_generated() -> None:
    increment("tax_workpapers_generated_total")


def payment_state(state: str) -> None:
    # One counter with a state label beats nine counters: the state list is the
    # contract in app/treasury/payments.py, and a label keeps the scrape honest
    # when a new state is added there.
    increment("payment_orders_state_total", state=state)
