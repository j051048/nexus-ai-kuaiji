"""Structured failure records for unexpected request errors.

The old log line carried a class name and nothing else:
``class=DatatypeMismatch``. That is enough to know something broke and not
enough to find out what, so an operator had to reproduce the request first. This
module builds one JSON record answering "where, who, which route, which SQLSTATE"
while keeping the same rule the response body already follows: never echo driver
or provider text, because it can contain row values.
"""

from __future__ import annotations

import traceback
from typing import Any

from ..security.redaction import redact_sensitive

# Bounded, non-identifying location: file base name plus line, never arguments.
_MAX_LOCATION = 160


def _sqlstate(error: BaseException) -> str:
    """Best-effort SQLSTATE without importing any driver.

    psycopg exposes ``sqlstate`` on the exception or on its ``diag``; sqlite3 uses
    ``sqlite_errorcode``/``sqlite_errorname``. We only ever emit a short code.
    """
    for attribute in ("sqlstate", "pgcode"):
        value = getattr(error, attribute, None)
        if isinstance(value, str) and value.isalnum() and len(value) <= 8:
            return value
    diag = getattr(error, "diag", None)
    value = getattr(diag, "sqlstate", None) if diag is not None else None
    if isinstance(value, str) and value.isalnum() and len(value) <= 8:
        return value
    name = getattr(error, "sqlite_errorname", None)
    if isinstance(name, str) and name.startswith("SQLITE_"):
        return name[:40]
    return ""


def _safe_location(error: BaseException) -> str:
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return ""
    last = frames[-1]
    file_name = last.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    location = f"{file_name}:{last.lineno}"
    return location[:_MAX_LOCATION]


def failure_record(
    *,
    trace_id: str,
    method: str,
    route: str,
    error: BaseException,
    firm_id: str = "",
) -> dict[str, Any]:
    """One loggable dict; every value passes through the redactor."""
    record = {
        "event": "request.failed",
        "request_id": trace_id,
        "trace_id": trace_id,
        "firm_id": firm_id,
        "method": method,
        "route": route,
        "error_class": type(error).__name__,
        "sqlstate": _sqlstate(error),
        "safe_location": _safe_location(error),
    }
    return {key: redact_sensitive(value) for key, value in record.items()}


def firm_of(request) -> str:
    identity = getattr(request.state, "identity", None)
    return getattr(identity, "firm_id", "") or ""
