"""A shared HTTP guard for both the new console and existing grant endpoints."""

import re


def requires_admin_mfa(path: str, method: str) -> bool:
    path = path.rstrip("/")
    if method == "OPTIONS":
        return False
    # A recipient has not yet joined a company. Acceptance validates their
    # verified identity and the pending request, not administrative authority.
    if method == "POST" and re.fullmatch(r"/api/admin/account-requests/[^/]+/accept", path):
        return False
    if path == "/api/admin" or path.startswith("/api/admin/"):
        return True
    if method in {"GET", "HEAD"}:
        return False
    return bool(
        # Legacy book creation can also initialize a company when no ID is supplied.
        path in {"/api/firms", "/api/companies", "/api/books"}
        or re.fullmatch(r"/api/firm/members(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/books/[^/]+/members(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/books/[^/]+/ledger/managers", path)
        or re.fullmatch(r"/api/books/[^/]+/legacy-migrations/[^/]+/access(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/companies/[^/]+/(?:members|departments)(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/companies/[^/]+/departments/[^/]+/approvers(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/companies/[^/]+/intelligence/settings", path)
        or re.fullmatch(r"/api/operations/companies/[^/]+/policy", path)
    )


def allow_firm_signup(mode: str, environ) -> bool:
    value = environ.get("ACCOUNTING_ALLOW_FIRM_SIGNUP", "1" if mode == "local" else "0")
    if value not in {"0", "1"}:
        raise RuntimeError("ACCOUNTING_ALLOW_FIRM_SIGNUP 只能为 0 或 1")
    return value == "1"
