"""A shared HTTP guard for both the new console and existing grant endpoints."""

import re


# Business actions that move money, freeze a period, publish a formal report,
# hand review authority to someone else, or pull every original out in one go.
# They are not "admin" endpoints, but they carry the same blast radius, so they
# sit behind the same AAL2 gate: a stolen aal1 session must not be enough.
HIGH_RISK_ACTIONS = (
    r"/api/treasury/payment-orders/[^/]+/approve",
    r"/api/b04-close/[^/]+/lock",
    r"/api/reports/[^/]+/publish",
    r"/api/books/[^/]+/review-delegations",
    r"/api/firm/batch-operations/review-export",
)


MFA_MODES = ("off", "warn", "enforce")


def mfa_enforcement(environ) -> str:
    """How hard the AAL2 gate bites.

    ``enforce`` rejects an aal1 session; ``warn`` lets it through and logs, so a
    deploy cannot silently lock every user who never enrolled a second factor;
    ``off`` disables the gate entirely (local/CI). The default is deliberately
    ``warn``: turning enforcement on is an operations decision that needs the
    firm's users to have MFA available first.
    """
    value = environ.get("ACCOUNTING_MFA_ENFORCE", "warn").strip().lower()
    if value not in MFA_MODES:
        raise RuntimeError("ACCOUNTING_MFA_ENFORCE 只能为 off、warn 或 enforce")
    return value


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
    if any(re.fullmatch(pattern, path) for pattern in HIGH_RISK_ACTIONS):
        return True
    return bool(
        # Legacy book creation can also initialize a company when no ID is supplied.
        path in {"/api/firms", "/api/companies", "/api/books"}
        or re.fullmatch(r"/api/firm/members(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/firm/members/[^/]+/handover", path)
        or re.fullmatch(r"/api/firm/access-assignments(?:/[^/]+/revoke)?", path)
        or re.fullmatch(r"/api/books/[^/]+/contact-access(?:/revoke)?", path)
        or re.fullmatch(r"/api/books/[^/]+/members(?:/[^/]+)?", path)
        or re.fullmatch(r"/api/books/[^/]+/ledger/managers", path)
        or re.fullmatch(r"/api/books/[^/]+/review-policy", path)
        or path == "/api/firm/review-control"
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
