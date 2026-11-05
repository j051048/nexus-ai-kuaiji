from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest

from app.accounting.domain import AccountingError
from app.business.models import BusinessEventCreate, ReturnRequest
from app.business.service import BusinessService
from app.firm_tasks.service import FirmTasksService
from test_business import payload
from test_business_postgres import business_case, business_postgres, pytestmark  # noqa: F401


def test_pg_concurrent_return_produces_one_private_notice(business_case):
    c = business_case
    service = BusinessService(c.db)
    event = service.create(
        c.employee,
        BusinessEventCreate(company_id=c.company, template_id="reimbursement", payload=payload()),
    )
    event = service.add_attachment(
        c.employee,
        event["id"],
        event["version"],
        {
            "id": uuid4().hex,
            "storage_key": f"synthetic/{uuid4().hex}/only-local.pdf",
            "filename": "虚构原件.pdf",
            "media_type": "application/pdf",
            "sha256": "a" * 64,
            "size_bytes": 400,
        },
    )
    event = service.submit(c.employee, event["id"], event["version"])

    def return_once(_):
        try:
            service.return_event(
                c.approver,
                event["id"],
                ReturnRequest(expected_version=event["version"], reason="补充虚构用途说明"),
            )
            return 200
        except AccountingError as failure:
            return failure.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(return_once, range(2)))
    assert sorted(results) == [200, 409]
    messages = FirmTasksService(c.db, c.service)
    rows = messages.messages(c.employee)["messages"]
    assert len(rows) == 1
    assert messages.message_target(c.employee, rows[0]["id"])["event_id"] == event["id"]
    with c.db.transaction(actor=c.colleague) as conn:
        assert not conn.execute(
            "SELECT id FROM client_messages WHERE id=?", (rows[0]["id"],)
        ).fetchall()
    with pytest.raises(c.psycopg.Error):
        with c.db.transaction(actor=c.employee) as conn:
            conn.execute("SELECT accounting.client_task_notification()")
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute("SELECT count(*) FROM delivery_notifications").fetchone()[0] == 0


def test_pg_invite_preview_never_grants_and_rechecks_revocation(business_case):
    from app.accounting.admin_identity import AdminIdentityService
    from app.accounting.directory import DirectoryService
    from test_admin_identity import FakeProvider

    c = business_case
    provider = FakeProvider()
    service = AdminIdentityService(c.db, provider)
    item = dict(
        name="虚构待入职员工",
        email="synthetic-new@example.test",
        company_id=c.company,
        company_role="employee",
        department_id=c.department,
        provisioning_mode="activation",
        idempotency_key=uuid4().hex,
    )
    assert service.preview_accounts(c.company_admin, [item])["ready"]
    with c.db.transaction(actor=c.owner) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM admin_account_requests WHERE firm_id=?", (c.owner.firm_id,)
            ).fetchone()[0]
            == 0
        )
    DirectoryService(c.db).revoke_company_member(c.owner, c.company, c.company_admin.user_id)
    assert not service.preview_accounts(c.company_admin, [item])["ready"]
    assert not service.preview_accounts(c.owner, [{**item, "company_role": "owner"}])["ready"]
    assert not provider.calls
