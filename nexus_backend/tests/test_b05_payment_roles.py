"""出纳（cashier）与资金审批人（treasury_approver）岗位：授权、隔离与撤权即时生效。

三条不能退让的性质：
1. 岗位只加「付钱」能力，不加账套编辑权；没有账套授权的人连书都看不到。
2. 出纳能制单不能放款；资金审批人能放款不能制单；两者都必须与对方是不同的人。
3. 撤销不删记录，且下一次请求立刻失效（不靠缓存、不靠重新登录）。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.access import AccessControl
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookMemberGrant, FirmMemberGrant
from app.treasury.roles import PaymentRoleService
from app.treasury.service import PaymentService
from test_business import business  # noqa: F401  (pytest fixture)


def _user(case, *, firm_role="reviewer", book_role="viewer", name="岗位测试人"):
    """Real identity + firm membership (+ optional book grant). No privilege forging."""
    identity = SimpleNamespace(
        user_id=f"pay-role-{uuid4().hex[:10]}",
        display_name=name,
        email=f"pay-role-{uuid4().hex[:10]}@example.test",
    )
    case.directory.register_identity(identity)
    case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=identity.email, role=firm_role)
    )
    if book_role:
        case.directory.grant_book_member(
            case.manager, case.book["id"], identity.user_id, BookMemberGrant(role=book_role)
        )
    return Actor(identity.user_id, case.manager.firm_id)


def _roles(case):
    return PaymentRoleService(case.db)


def _order(case, actor, *, key="role-order-1"):
    return PaymentService(case.db).create(
        actor,
        case.book["id"],
        payee="供应商甲",
        payee_account="6222 0000 0000 0001",
        amount_cents=120_000,
        idempotency_key=key,
        period="2026-10",
    )


def test_a_payment_role_never_widens_ledger_rights(business):
    case = business
    cashier = _user(case, book_role="viewer")
    granted = _roles(case).grant(
        case.manager, case.book["id"], cashier.user_id, role="cashier", reason="出纳岗位授权"
    )
    assert granted["role"] == "cashier" and granted["state"] == "active"
    with case.db.transaction(actor=case.manager) as conn:
        permissions = PaymentService(case.db).access.book(conn, cashier, case.book["id"])
    assert permissions["permissions"]["pay"] is True
    assert permissions["permissions"]["edit"] is False
    assert permissions["permissions"]["review"] is False
    assert permissions["permissions"]["approve_payment"] is False
    # 出纳能制单，但不能审批同一笔（既没有审批能力，也不会是「另一个人」）。
    order = _order(case, cashier)
    PaymentService(case.db).submit(cashier, order["id"])
    with pytest.raises(AccountingError) as refused:
        PaymentService(case.db).approve(cashier, order["id"])
    assert refused.value.status in (403,)


def test_cashier_and_treasury_approver_are_two_different_jobs(business):
    case = business
    cashier = _user(case, book_role="viewer")
    approver = _user(case, book_role="viewer")
    _roles(case).grant(
        case.manager, case.book["id"], cashier.user_id, role="cashier", reason="出纳岗位授权"
    )
    _roles(case).grant(
        case.manager,
        case.book["id"],
        approver.user_id,
        role="treasury_approver",
        reason="资金审批人授权",
    )
    service = PaymentService(case.db)
    order = _order(case, cashier)
    service.submit(cashier, order["id"])
    # 资金审批人不能制单。
    with pytest.raises(AccountingError):
        _order(case, approver, key="role-order-approver")
    approved = service.approve(approver, order["id"])
    assert approved["status"] == "approved"
    assert approved["approver_user_id"] == approver.user_id
    # 出纳也不能推进成「已放款」——推进同样是付钱动作，但审批已完成，
    # 这里验证的是审批人不能把别人的单再批一次。
    with pytest.raises(AccountingError):
        service.approve(approver, order["id"])


def test_management_review_separation_cannot_be_bypassed_by_a_treasury_role():
    """管理者分离政策开启时，事务所管理者也不能靠岗位把自己变成审批人。

    这条是纯函数级的守门断言：企业级策略开关一旦打开，``treasury_approver``
    只对「非管理者复核人」生效，不会因为管理者身份自动解锁。
    """
    owner_with_role = AccessControl.permissions(
        "owner", "viewer", None, payment_roles={"treasury_approver"}
    )
    assert owner_with_role["approve_payment"] is True  # 管理者本来就有复核权
    separated = AccessControl.permissions(
        "owner",
        "viewer",
        None,
        management_review_separation=True,
        payment_roles={"treasury_approver"},
    )
    assert separated["approve_payment"] is False
    # 非管理者复核人在分离政策下仍可凭岗位放款。
    reviewer = AccessControl.permissions(
        "reviewer",
        "viewer",
        None,
        management_review_separation=True,
        payment_roles={"treasury_approver"},
    )
    assert reviewer["approve_payment"] is True
    assert reviewer["edit"] is False


def test_revoking_a_payment_role_takes_effect_on_the_next_request(business):
    case = business
    cashier = _user(case, book_role="viewer")
    roles = _roles(case)
    granted = roles.grant(
        case.manager, case.book["id"], cashier.user_id, role="cashier", reason="出纳岗位授权"
    )
    service = PaymentService(case.db)
    order = _order(case, cashier)
    revoked = roles.revoke(
        case.manager, case.book["id"], granted["id"], reason="岗位调整，收回制单权"
    )
    assert revoked["state"] == "revoked"
    with pytest.raises(AccountingError):
        service.submit(cashier, order["id"])
    # 撤销不删记录：历史授权还在，能查到谁撤的、为什么撤。
    listed = roles.list(case.manager, case.book["id"])
    assert [(item["role"], item["state"]) for item in listed["roles"]] == [("cashier", "revoked")]
    assert listed["roles"][0]["revoked_by"] == case.manager.user_id
    # 再次授权是「恢复同一条」，不会出现两条互相矛盾的记录。
    again = roles.grant(
        case.manager, case.book["id"], cashier.user_id, role="cashier", reason="恢复出纳岗位"
    )
    assert again["id"] == granted["id"] and again["state"] == "active"
    assert len(roles.list(case.manager, case.book["id"])["roles"]) == 1


def test_only_managers_can_hand_out_payment_duties(business):
    case = business
    cashier = _user(case, book_role="viewer")
    roles = _roles(case)
    with pytest.raises(AccountingError) as refused:
        roles.grant(
            cashier,
            case.book["id"],
            cashier.user_id,
            role="treasury_approver",
            reason="出纳给自己发审批权",
        )
    assert refused.value.status == 403
    # 有编辑权但不是管理者也不行。
    editor = _user(case, firm_role="operator", book_role="editor")
    with pytest.raises(AccountingError):
        roles.grant(
            editor, case.book["id"], cashier.user_id, role="cashier", reason="编辑者代授岗位"
        )
    with case.db.transaction(actor=case.manager) as conn:
        assert conn.execute("SELECT COUNT(*) FROM book_payment_roles").fetchone()[0] == 0


def test_a_book_without_any_grant_cannot_be_worked_from_a_payment_role(business):
    """岗位不是后门：没有账套授权的人，即使被授予出纳，也看不到这本书。"""
    case = business
    orphan = _user(case, book_role=None)
    with pytest.raises(AccountingError):
        _roles(case).grant(
            case.manager, case.book["id"], orphan.user_id, role="cashier", reason="无账套授权者"
        )
    # 授权接口自身要求管理者先有账套访问，因此这里直接写库确认岗位单独不够用。
    with case.db.transaction(write=True, actor=case.manager) as conn:
        conn.execute(
            "INSERT INTO book_payment_roles(id,firm_id,company_id,book_id,user_id,role,state,reason,"
            "granted_by,granted_at) VALUES(?,?,?,?,?,'cashier','active','手工写入验证',?,?)",
            (
                uuid4().hex,
                case.manager.firm_id,
                case.book["company_id"],
                case.book["id"],
                orphan.user_id,
                case.manager.user_id,
                "2026-11-01T00:00:00+00:00",
            ),
        )
    with pytest.raises(AccountingError):
        _order(case, orphan, key="role-order-orphan")


def _client(case, actor):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from app.treasury.router import build_treasury_router

    app = FastAPI()
    app.state.service = SimpleNamespace(db=case.db)
    app.state.payments = PaymentService(case.db)
    app.include_router(build_treasury_router(lambda: actor))

    @app.exception_handler(AccountingError)
    def _accounting_error(_request, error: AccountingError):
        return JSONResponse(status_code=error.status, content={"detail": error.detail})

    return TestClient(app)


def test_http_grant_desk_and_revoke_roundtrip(business):
    """HTTP 口径：管理者能发岗位、出纳台能看到自己的能力和指令、撤销即时生效。"""
    case = business
    manager_client = _client(case, case.manager)
    cashier = _user(case, book_role="viewer")
    granted = manager_client.put(
        f"/api/treasury/payment-roles/{cashier.user_id}",
        params={"book_id": case.book["id"]},
        json={"role": "cashier", "reason": "本地虚构场景授权出纳"},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["role"] == "cashier"

    cashier_client = _client(case, cashier)
    desk = cashier_client.get("/api/treasury/payment-desk", params={"book_id": case.book["id"]})
    assert desk.status_code == 200, desk.text
    body = desk.json()
    assert body["roles"] == ["cashier"]
    assert body["permissions"] == {"pay": True, "approve_payment": False}
    assert "制单人与资金审批人必须是不同的人" in body["separation"]

    created = cashier_client.post(
        "/api/treasury/payment-orders",
        json={
            "book_id": case.book["id"],
            "period": "2026-10",
            "payee": "供应商乙",
            "payee_account": "6222 0000 0000 0002",
            "amount_cents": 88_000,
            "idempotency_key": "role-http-1",
        },
    )
    assert created.status_code == 200, created.text
    # 出纳不能自己放款。
    assert (
        cashier_client.post(
            f"/api/treasury/payment-orders/{created.json()['id']}/approve", json={}
        ).status_code
        == 403
    )
    revoked = manager_client.post(
        f"/api/treasury/payment-roles/{granted.json()['id']}/revoke",
        params={"book_id": case.book["id"]},
        json={"reason": "岗位调整，收回付款权"},
    )
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    # 下一次请求立刻没有付钱能力。
    assert (
        cashier_client.get(
            "/api/treasury/payment-desk", params={"book_id": case.book["id"]}
        ).json()["permissions"]["pay"]
        is False
    )
    assert (
        cashier_client.post(
            "/api/treasury/payment-orders",
            json={
                "book_id": case.book["id"],
                "period": "2026-10",
                "payee": "供应商丙",
                "payee_account": "6222 0000 0000 0003",
                "amount_cents": 1_000,
                "idempotency_key": "role-http-2",
            },
        ).status_code
        == 403
    )
    # 非管理者不能发岗位。
    assert (
        cashier_client.put(
            f"/api/treasury/payment-roles/{cashier.user_id}",
            params={"book_id": case.book["id"]},
            json={"role": "treasury_approver", "reason": "出纳给自己发审批权"},
        ).status_code
        == 403
    )
