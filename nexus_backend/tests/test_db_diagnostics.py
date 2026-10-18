"""Database diagnostics keep SQL, evidence and client-supplied identifiers out."""

import asyncio
import json
import logging
import sqlite3
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.routing import Route

from app.accounting.db_diagnostics import (
    FUNCTION_NAMES,
    MAX_FUNCTIONS,
    database_diagnostic,
    log_database_unavailable,
)

SECRET = "sensitive-client-secret-and-evidence-73159"


class ContextError(psycopg.OperationalError):
    sqlstate = "55000"

    def __init__(self, context):
        super().__init__("DSN password SQL evidence: " + SECRET)
        self.context = context

    @property
    def diag(self):
        return SimpleNamespace(context=self.context)


def request():
    async def endpoint():
        return None

    app = FastAPI()
    route = Route("/api/cases/{case_id}/review-response", endpoint, methods=["POST"])
    app.router.routes.append(route)
    return Request(
        {
            "type": "http",
            "app": app,
            "router": app.router,
            "route": route,
            "method": "POST",
            "scheme": "https",
            "path": "/api/cases/" + SECRET + "/review-response",
            "query_string": ("token=" + SECRET).encode(),
            "headers": [(b"authorization", SECRET.encode()), (b"x-request-id", SECRET.encode())],
        }
    )


def test_log_uses_only_known_frames_and_registered_template(caplog):
    error = ContextError(
        "PL/pgSQL function lock_legacy_write() line 4 at RAISE\n"
        f'SQL statement "SELECT \'{SECRET}\'"\n'
        "PL/pgSQL function accounting.case_accept_evidence(text,integer) line 15 at PERFORM"
    )
    with caplog.at_level(logging.WARNING, logger="app.accounting.db_diagnostics"):
        log_database_unavailable(request(), error)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert SECRET not in repr(record.__dict__)
    assert "case_accept_evidence" in record.getMessage()
    assert "55000" in record.getMessage()
    assert "/api/cases/{case_id}/review-response" in record.getMessage()
    assert "SELECT" not in record.getMessage()
    assert record.exc_info is None and record.stack_info is None


def test_forged_frames_in_sql_and_unknown_identifiers_are_omitted():
    error = ContextError(
        f'SQL statement "SELECT \'{SECRET}\n'
        "PL/pgSQL function lock_legacy_write() line 4 at RAISE\n"
        "still a SQL literal'\"\n"
        f"PL/pgSQL function {SECRET.replace('-', '_')}() line 4 at RAISE\n"
        "PL/pgSQL function public.lock_company_write(text,text) line 4 at PERFORM\n"
        "PL/pgSQL function case_command_write_guard() line 12 at SQL statement"
    )
    details = database_diagnostic(request(), error)
    assert details["functions"] == ("case_command_write_guard",)
    assert SECRET not in repr(details)


def test_error_class_and_sqlstate_cannot_be_supplied_as_sensitive_text():
    injected_class = type(SECRET, (ContextError,), {"sqlstate": SECRET})
    details = database_diagnostic(request(), injected_class(SECRET))
    assert details["error_class"] == "OperationalError"
    assert details["sqlstate"] is None and details["functions"] == ()
    assert SECRET not in repr(details)
    actual = database_diagnostic(
        request(), psycopg.errors.ObjectNotInPrerequisiteState(SECRET)
    )
    assert actual["error_class"] == "psycopg.ObjectNotInPrerequisiteState"
    assert actual["sqlstate"] == "55000"


@pytest.mark.parametrize(
    "context", [None, 123, {"secret": SECRET}, SECRET * 20_000],
    ids=["missing", "number", "mapping", "oversized"],
)
def test_unknown_context_is_never_recorded(context):
    details = database_diagnostic(request(), ContextError(context))
    assert details["functions"] == () and SECRET not in repr(details)


def test_function_list_is_deduplicated_and_bounded():
    names = sorted(FUNCTION_NAMES)
    context = "\n".join(
        f"PL/pgSQL function {name}() line 1 at SQL statement"
        for name in names + names
    )
    details = database_diagnostic(request(), ContextError(context))
    assert details["functions"] == tuple(names[:MAX_FUNCTIONS])


@pytest.mark.parametrize("kind", ["attribute_spoof", "unregistered", "bad_template", "missing"])
def test_route_never_falls_back_to_actual_url(kind):
    req = request()
    if kind == "attribute_spoof":
        req.scope["route"] = SimpleNamespace(path="/api/" + SECRET)
    elif kind == "unregistered":
        req.scope["route"] = Route("/api/" + SECRET, lambda: None)
    elif kind == "bad_template":
        route = Route("/api/" + SECRET + "\n", lambda: None)
        req.scope["router"].routes.append(route)
        req.scope["route"] = route
    else:
        req.scope.pop("route")
    details = database_diagnostic(req, ContextError(SECRET))
    assert details["route_template"] == "unmatched"
    assert SECRET not in repr(details)


def test_main_operational_handler_keeps_generic_503(tmp_path, caplog):
    from app.main import create_app

    app = create_app(
        tmp_path / "diagnostic.db",
        seed=False,
        attachment_root=tmp_path / "private",
        queue_worker_enabled=False,
    )
    handler = app.exception_handlers[sqlite3.OperationalError]
    with caplog.at_level(logging.WARNING, logger="app.accounting.db_diagnostics"):
        response = asyncio.run(handler(request(), sqlite3.OperationalError(SECRET)))
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "detail": "账务数据服务暂时不可用，请稍后重试；未完成的操作会整体回滚"
    }
    assert SECRET not in response.body.decode()
    assert len(caplog.records) == 1 and SECRET not in repr(caplog.records[0].__dict__)
