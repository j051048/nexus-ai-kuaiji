"""Restricted-role dynamic authorization tests, including native SQL."""

from uuid import uuid4

import pytest

from app.accounting.access_policy import AccessPolicyService, StaffAssignment, ContactAccess
from app.accounting.domain import Actor, AccountingError
from app.delivery.monthly_models import MonthlyPackageAcknowledge
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from test_business_postgres import ADMIN_URL, business_postgres, business_case  # noqa: F401
from test_monthly_delivery_postgres import p0_pg, p0_case, package_case  # noqa: F401


@pytest.mark.parametrize("window", ["active", "expired", "future"])
def test_pg_scope_and_clock_apply_to_service_and_native_sql(business_case, window):
    c = business_case
    actor = c.person(firm_role="viewer")
    policy = AccessPolicyService(c.db)
    kwargs = (
        {"valid_to": "2000-01-01T00:00:00Z"}
        if window == "expired"
        else {"valid_from": "2099-01-01T00:00:00Z"}
        if window == "future"
        else {}
    )
    grant = policy.assign(
        c.owner,
        StaffAssignment(
            user_id=actor.user_id,
            template="auditor",
            company_id=c.company,
            reason="本机部门与客户范围验证",
            **kwargs,
        ),
    )
    with c.db.transaction(actor=actor) as conn:
        assert bool(conn.execute("SELECT accounting.book_allowed(?)", (c.book,)).fetchone()[0]) == (
            window == "active"
        )
        assert conn.execute("SELECT id FROM books WHERE id=?", (c.other_book,)).fetchall() == []
        assert not conn.execute("SELECT accounting.book_review_allowed(?)", (c.book,)).fetchone()[0]
        rows = conn.execute("SELECT id FROM books WHERE id=?", (c.book,)).fetchall()
        assert len(rows) == (1 if window == "active" else 0)
    policy.revoke(c.owner, grant["id"], "当前管理员显式撤销授权")
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute("SELECT id FROM books WHERE id=?", (c.book,)).fetchall() == []


def test_pg_native_client_cannot_create_formal_reviewer(business_case):
    c = business_case
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "UPDATE book_access SET role='reviewer' WHERE book_id=? AND user_id=?",
                (c.book, c.finance.user_id),
            )


def test_pg_native_assignment_cannot_forge_identity_or_promote_viewer(business_case):
    c = business_case
    actor = c.person(firm_role="viewer")
    for principal, template, creator in (
        (actor, "auditor", actor.user_id),
        (c.owner, "reviewer", c.owner.user_id),
        (c.owner, "auditor", actor.user_id),
    ):
        with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
            with c.db.transaction(write=True, actor=principal) as conn:
                conn.execute(
                    "INSERT INTO staff_access_assignments(id,firm_id,user_id,template,state,reason,created_by,created_at) VALUES(?,?,?,?,'active','不能原生SQL绕过授权',?,'2026-10-31T00:00:00Z')",
                    (uuid4().hex, c.owner.firm_id, actor.user_id, template, creator),
                )


def test_pg_revoked_assignment_cannot_be_reactivated(business_case):
    c = business_case
    actor = c.person(firm_role="viewer")
    policy = AccessPolicyService(c.db)
    grant = policy.assign(
        c.owner,
        StaffAssignment(
            user_id=actor.user_id,
            template="auditor",
            scope="firm",
            reason="原生SQL不得复活撤销授权",
        ),
    )
    policy.revoke(c.owner, grant["id"], "审计结束撤销原授权")
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "UPDATE staff_access_assignments SET state='active' WHERE id=?", (grant["id"],)
            )


def test_pg_concurrent_same_grant_has_one_revocable_identity(business_case):
    from concurrent.futures import ThreadPoolExecutor

    c = business_case
    actor = c.person(firm_role="viewer")
    policy = AccessPolicyService(c.db)
    request = StaffAssignment(
        user_id=actor.user_id,
        template="auditor",
        company_id=c.company,
        reason="两个请求同时配置同一授权",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda _: policy.assign(c.owner, request), range(2)))
    assert rows[0]["id"] == rows[1]["id"]
    policy.revoke(c.owner, rows[0]["id"], "撤销后不残留重复授权")
    with c.db.transaction(actor=actor) as conn:
        assert not conn.execute("SELECT accounting.book_allowed(?)", (c.book,)).fetchone()[0]


def test_pg_review_authority_cannot_be_manufactured_by_app(business_case):
    c = business_case
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute(
                "INSERT INTO review_authority_snapshots(subject_kind,subject_id,reviewed_version,book_id,firm_id,reviewer_user_id,role_snapshot,proof_kind,recorded_at) VALUES('draft','not-reviewed',1,?,?,?,'owner','review_time','2026-10-31')",
                (c.book, c.owner.firm_id, c.owner.user_id),
            )


def test_pg_departed_member_grant_can_still_be_revoked(business_case):
    c = business_case
    actor = c.person(firm_role="viewer")
    policy = AccessPolicyService(c.db)
    grant = policy.assign(
        c.owner,
        StaffAssignment(
            user_id=actor.user_id,
            template="auditor",
            company_id=c.company,
            reason="离职前的客户只读授权",
        ),
    )
    with c.db.transaction(write=True, actor=c.owner) as conn:
        conn.execute(
            "DELETE FROM memberships WHERE firm_id=? AND user_id=?", (actor.firm_id, actor.user_id)
        )
    assert policy.revoke(c.owner, grant["id"], "人员离职后关闭遗留授权")["state"] == "revoked"


def test_pg_department_projection_does_not_expose_ledger(business_case):
    c = business_case
    result = AccessPolicyService(c.db).department_expenses(
        c.approver, c.company, "2026-10", c.department
    )
    assert result["expense_cents"] == 0 and "lines" not in result
    with c.db.transaction(actor=c.employee) as conn:
        assert (
            conn.execute(
                "SELECT accounting.department_expense_summary(?,?,?)",
                (c.company, c.department, "2026-10"),
            ).fetchone()[0]
            is None
        )
        assert conn.execute("SELECT * FROM voucher_lines").fetchall() == []


def test_pg_ordinary_reviewer_can_grant_only_current_company_contact(business_case):
    c = business_case
    policy = AccessPolicyService(c.db)
    request = ContactAccess(user_id=c.finance.user_id, reason="普通复核员配置客户仅签收权限")
    assert policy.contact_access(c.reviewer, c.book, request)["state"] == "active"
    outsider = c.person(company_role="employee", company=c.other_company)
    with pytest.raises(AccountingError):
        policy.contact_access(
            c.reviewer,
            c.book,
            ContactAccess(user_id=outsider.user_id, reason="不得授权其他公司的联系人"),
        )


def test_pg_contact_only_open_ack_and_revoke_without_ledger(package_case):
    c = package_case
    actor = Actor("contact-only-" + uuid4().hex, c.manager.firm_id)
    with c.psycopg.connect(c.admin) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name) VALUES(%s,'虚构仅签收人')", (actor.user_id,)
        )
        conn.execute(
            "INSERT INTO accounting.company_memberships(company_id,user_id,role) VALUES(%s,%s,'employee')",
            (c.book["company_id"], actor.user_id),
        )
    policy = AccessPolicyService(c.db)
    policy.contact_access(
        c.reviewer,
        c.book["id"],
        ContactAccess(user_id=actor.user_id, reason="只读指定月份的交付包"),
    )
    ServicePlansService(c.db, c.accounting, c.business).save_plan(
        c.manager,
        c.book["id"],
        PlanSave(
            expected_version=1,
            contact_user_id=actor.user_id,
            due_on="2026-11-10",
            reason="指定本月签收联系人",
        ),
    )
    package = c.monthly.create(c.reviewer, c.book["id"], c.request)
    with c.db.transaction(actor=actor) as conn:
        assert conn.execute("SELECT id FROM books WHERE id=?", (c.book["id"],)).fetchall() == []
        assert conn.execute("SELECT * FROM voucher_lines").fetchall() == []
    assert policy.recipient_packages(actor)["packages"][0]["id"] == package["id"]
    assert c.monthly.open(actor, c.book["id"], package["id"])["can_acknowledge"]
    assert c.monthly.download(actor, c.book["id"], package["id"]).startswith(b"PK")
    c.monthly.acknowledge(
        actor,
        c.book["id"],
        package["id"],
        MonthlyPackageAcknowledge(
            manifest_sha256=package["manifest_sha256"], reason="确认收到指定版本资料包"
        ),
    )
    policy.contact_access(
        c.reviewer,
        c.book["id"],
        ContactAccess(user_id=actor.user_id, reason="撤销仅签收人访问授权"),
        revoke=True,
    )
    assert policy.recipient_packages(actor)["packages"] == []
    with pytest.raises(AccountingError):
        c.monthly.download(actor, c.book["id"], package["id"])
