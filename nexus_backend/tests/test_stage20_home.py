"""Home capability reads stay bounded and match live domain grants."""

from itertools import product
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.operations.service import OperationsService
from test_business import business
from test_stage12_workspace import workspace
from test_stage19_request_reads import count_statements


def previous_profile(service, actor):
    """The pre-optimization path is an independent compatibility oracle."""
    context = service.business.context(actor)
    summary = service.accounting.workbench(actor, limit=1)["stats"]
    with service.db.transaction(actor=actor) as conn:
        scope, params, role = service.review._review_scope(conn, actor)
        can_review = bool(
            conn.execute("SELECT 1 FROM (" + scope + ") v LIMIT 1", params).fetchone()
        )
    companies = context["companies"]
    finance = any(c["can_finance"] for c in companies)
    approve = any(c["approver_department_ids"] for c in companies)
    submit = any(c["can_submit"] for c in companies)
    manage = role in {"owner", "admin"} or any(c["can_manage_approvers"] for c in companies)
    primary = (
        "accountant"
        if role in {"owner", "admin", "operator", "reviewer"}
        else "finance"
        if finance
        else "approver"
        if approve
        else "employee"
        if submit
        else "manager"
        if manage
        else "accountant"
        if can_review
        else "viewer"
    )
    return {
        "role": primary,
        "can_submit": submit,
        "can_approve": approve,
        "can_finance": finance,
        "can_review": can_review,
        "can_accounting": summary["book_count"] > 0,
        "can_manage": manage,
        "companies": [{"id": c["id"], "name": c["name"]} for c in companies],
        "book_count": summary["book_count"],
        "can_operations": manage
        or bool(OperationsService(service.db, None).companies(actor)["companies"]),
        "can_firm_operations": role in {"owner", "admin"},
    }


def test_profile_matches_domain_grants_without_loading_accounting_facts(business):
    c, service = business, workspace(business)
    actor = c.actors["accountant"]
    for firm_role, company_role, book_role in product(
        [None, "admin", "operator", "reviewer", "viewer"],
        [None, "owner", "admin", "finance", "employee"],
        [None, "editor", "reviewer", "viewer"],
    ):
        with c.db.transaction(write=True) as conn:
            conn.execute("DELETE FROM memberships WHERE user_id=?", (actor.user_id,))
            conn.execute("DELETE FROM company_memberships WHERE user_id=?", (actor.user_id,))
            conn.execute("DELETE FROM book_access WHERE user_id=?", (actor.user_id,))
            if firm_role:
                conn.execute(
                    "INSERT INTO memberships VALUES(?,?,?)",
                    (actor.firm_id, actor.user_id, firm_role),
                )
            if company_role:
                conn.execute(
                    "INSERT INTO company_memberships(company_id,user_id,role,department_id) VALUES(?,?,?,?)",
                    (c.company["id"], actor.user_id, company_role, c.department["id"]),
                )
            if book_role:
                conn.execute(
                    "INSERT INTO book_access VALUES(?,?,?,?)",
                    (c.book["id"], actor.firm_id, actor.user_id, book_role),
                )
        assert service.profile(actor) == previous_profile(service, actor), (
            firm_role,
            company_role,
            book_role,
        )
    for actor in [c.manager, *c.actors.values()]:
        assert service.profile(actor) == previous_profile(service, actor)


@pytest.mark.parametrize("extra_companies", [0, 100])
def test_profile_uses_one_bounded_read_transaction_without_voucher_health(
    business, monkeypatch, extra_companies
):
    c, service = business, workspace(business)
    with c.db.transaction(write=True) as conn:
        for i in range(extra_companies):
            company = uuid4().hex
            conn.execute(
                "INSERT INTO companies(id,name) VALUES(?,?)", (company, f"能力查询企业{i}")
            )
            conn.execute(
                "INSERT INTO firm_clients(firm_id,company_id) VALUES(?,?)",
                (c.manager.firm_id, company),
            )

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "home capabilities must not load business directories or voucher health"
        )

    monkeypatch.setattr(service.business, "context", forbidden)
    monkeypatch.setattr(service.accounting, "workbench", forbidden)
    with count_statements(c.db, monkeypatch) as statements:
        result = service.profile(c.manager)
    assert len(statements) == 1
    assert len(result["companies"]) == extra_companies + 2
    assert result["book_count"] == 2
    assert not any("draft_lines" in query or "FROM drafts" in query for query in statements)


@pytest.mark.parametrize("change", ["membership", "book", "client", "company", "user"])
def test_profile_revocation_suspension_and_archive_are_live(business, change):
    c, service = business, workspace(business)
    actor = c.actors["finance"] if change != "book" else c.actors["accountant"]
    assert service.profile(actor)["can_accounting"]
    with c.db.transaction(write=True) as conn:
        if change == "membership":
            conn.execute("DELETE FROM company_memberships WHERE user_id=?", (actor.user_id,))
        elif change == "book":
            conn.execute("DELETE FROM book_access WHERE user_id=?", (actor.user_id,))
        elif change == "client":
            conn.execute(
                "UPDATE firm_clients SET status='suspended' WHERE company_id=?", (c.company["id"],)
            )
        elif change == "company":
            conn.execute(
                "UPDATE companies SET status='archived',version=version+1 WHERE id=?",
                (c.company["id"],),
            )
        else:
            conn.execute("UPDATE users SET active=0 WHERE id=?", (actor.user_id,))
    if change == "user":
        with pytest.raises(AccountingError, match="停用"):
            service.profile(actor)
    else:
        result = service.profile(actor)
        assert result["companies"] == []
        assert not result["can_accounting"] and not result["can_operations"]
        assert not result["can_review"] and not result["can_submit"]


@pytest.mark.parametrize("mode", ["department", "direct"])
def test_profile_submission_policy_and_approver_membership_remain_current(business, mode):
    c, service = business, workspace(business)
    with c.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO business_submission_policies(firm_id,company_id,mode,version,reason,updated_at,updated_by) VALUES(?,?,?,1,'合成权限测试','2026-10-15',?)",
            (c.manager.firm_id, c.company["id"], mode, c.manager.user_id),
        )
    for actor in c.actors.values():
        assert service.profile(actor) == previous_profile(service, actor)
    with c.db.transaction(write=True) as conn:
        conn.execute(
            "DELETE FROM company_memberships WHERE user_id=?", (c.actors["approver"].user_id,)
        )
    assert not service.profile(c.actors["approver"])["can_approve"]
