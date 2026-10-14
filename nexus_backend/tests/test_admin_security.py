"""Administrative assurance also covers legacy permission mutation routes."""

import pytest

from app.auth.admin_security import allow_firm_signup, requires_admin_mfa


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/admin/companies"),
    ("POST", "/api/admin/account-requests"),
    ("POST", "/api/admin/account-requests/request/retry"),
    ("PATCH", "/api/admin/companies/company"),
    ("POST", "/api/firms"),
    ("POST", "/api/companies/"),
    ("POST", "/api/books"),
    ("PUT", "/api/firm/members"),
    ("DELETE", "/api/firm/members/user"),
    ("PUT", "/api/companies/company/members"),
    ("DELETE", "/api/companies/company/members/user"),
    ("POST", "/api/companies/company/departments"),
    ("PUT", "/api/companies/company/departments/department/approvers"),
    ("DELETE", "/api/companies/company/departments/department/approvers/user"),
    ("PUT", "/api/books/book/members/user"),
    ("POST", "/api/books/book/ledger/managers"),
    ("POST", "/api/books/book/legacy-migrations/project/access"),
    ("DELETE", "/api/books/book/legacy-migrations/project/access/user"),
])
def test_both_management_entrypoints_require_assurance(method, path):
    assert requires_admin_mfa(path, method)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/admin/account-requests/request/accept"),
    ("GET", "/api/me"),
    ("GET", "/api/companies"),
    ("POST", "/api/business/events"),
    ("POST", "/api/books/book/drafts/draft/approve"),
    ("POST", "/api/books/book/exports"),
    ("OPTIONS", "/api/admin/account-requests"),
])
def test_recipient_and_normal_accounting_flows_keep_existing_authorization(method, path):
    assert not requires_admin_mfa(path, method)


def test_real_environment_is_invitation_only_by_default():
    assert not allow_firm_signup("supabase", {})
    assert allow_firm_signup("local", {})
    assert allow_firm_signup("supabase", {"ACCOUNTING_ALLOW_FIRM_SIGNUP": "1"})
    with pytest.raises(RuntimeError):
        allow_firm_signup("supabase", {"ACCOUNTING_ALLOW_FIRM_SIGNUP": "yes"})
