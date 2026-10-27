"""Bounded, allowlisted database diagnostics without query/evidence content."""

import logging
import re
import sqlite3

import psycopg
from starlette.routing import BaseRoute

logger = logging.getLogger(__name__)

# Only code-owned identifiers may leave diag.context. Unknown/new functions are
# omitted until explicitly reviewed here; a regex alone can leak forged names.
FUNCTION_NAMES = frozenset(
    """lock_legacy_write lock_company_write lock_dispatch_write begin_company_write
    begin_system_company_write company_write_table_guard compatibility_statement_gate
    workspace_projection_bump proposal_resume_watch_write_guard
    case_command_write_guard case_accept_evidence case_collection_update
    capture_resume_watch capture_resume_signal capture_verified_evidence_version
    proposal_wait_dependencies cancel_proposal_job cancel_resumable_proposal
    worker_bind_proposal_run worker_mark_proposal_sending worker_renew_proposal_lease
    worker_finish_proposal_job worker_finish_proposal_rule worker_finish_proposal_company_rule
    worker_resume_proposal worker_claim_resume_candidates worker_refresh_proposal_dependencies
    claim_proposal_job recover_proposal_jobs claim_ocr_job recover_ocr_jobs
    worker_bind_ocr_run worker_mark_ocr_sending worker_finish_ocr_job
    create_closing_snapshot review_closing_snapshot close_with_snapshot capture_delivery_manifest
    reserve_model_cost reserve_intake_model_cost reserve_assistant_model_cost
    mark_model_cost_sent settle_model_cost""".split()
)
MAX_CONTEXT = 16_384
MAX_FUNCTIONS = 8
_FRAME = re.compile(
    r"PL/pgSQL function (?:accounting\.)?([a-z_][a-z0-9_]{0,62})"
    r"\([a-z0-9_.,\[\] ]{0,512}\) line [1-9][0-9]{0,6} at [A-Za-z][A-Za-z ]{0,63}"
)
_ROUTE = re.compile(
    r"(?:/[A-Za-z0-9_.-]+|/\{[A-Za-z_][A-Za-z0-9_]*"
    r"(?::(?:str|int|float|uuid|path))?\})*/?"
)
_PG_ERROR_CLASSES = {
    value: "psycopg." + value.__name__
    for value in vars(psycopg.errors).values()
    if isinstance(value, type) and issubclass(value, psycopg.OperationalError)
}
_SQLSTATES = frozenset(
    value.sqlstate
    for value in vars(psycopg.errors).values()
    if isinstance(value, type)
    and issubclass(value, psycopg.Error)
    and type(getattr(value, "sqlstate", None)) is str
)


def _attribute(value, name):
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _functions(error):
    context = _attribute(_attribute(error, "diag"), "context")
    if type(context) is not str:
        return ()
    found = []
    inside_sql = False
    for line in context[:MAX_CONTEXT].splitlines()[:128]:
        # SQL-statement bodies may themselves contain fake stack-frame lines.
        # Do not collect from a quoted statement block or log its contents.
        if line.startswith('SQL statement "'):
            inside_sql = not line.endswith('"')
            continue
        if inside_sql:
            if line.endswith('"'):
                inside_sql = False
            continue
        match = _FRAME.fullmatch(line)
        if match and match[1] in FUNCTION_NAMES and match[1] not in found:
            found.append(match[1])
            if len(found) == MAX_FUNCTIONS:
                break
    return tuple(found)


def _route_template(request):
    scope = request.scope
    route = scope.get("route")
    router = scope.get("router") or _attribute(scope.get("app"), "router")
    path = _attribute(route, "path")
    if (
        isinstance(route, BaseRoute)
        and route in (_attribute(router, "routes") or ())
        and type(path) is str
        and 0 < len(path) <= 192
        and path.startswith("/")
        and _ROUTE.fullmatch(path)
    ):
        return path
    return "unmatched"


def database_diagnostic(request, error):
    state = _attribute(error, "sqlstate")
    error_class = _PG_ERROR_CLASSES.get(type(error), "OperationalError")
    if isinstance(error, sqlite3.OperationalError):
        error_class = "sqlite3.OperationalError"
    return {
        "error_class": error_class,
        "sqlstate": state
        if type(state) is str and len(state) == 5 and state in _SQLSTATES
        else None,
        "functions": _functions(error),
        "route_template": _route_template(request),
    }


def log_database_unavailable(request, error):
    details = database_diagnostic(request, error)
    logger.warning(
        "Database unavailable class=%s sqlstate=%s functions=%s route=%s",
        details["error_class"],
        details["sqlstate"] or "unknown",
        ",".join(details["functions"]) or "unknown",
        details["route_template"],
    )
