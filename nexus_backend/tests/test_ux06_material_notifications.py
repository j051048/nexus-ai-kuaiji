import pytest

from app.accounting.domain import AccountingError
from app.firm_tasks.service import FirmTasksService
from test_business import business  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import month_case  # noqa: F401
from test_stage25_service_plans import plans, generated, no_activity, review  # noqa: F401


def test_material_notice_targets_source_and_rechecks_authority(plans):
    c = plans
    _, item = generated(c)
    service = FirmTasksService(c.db, c.accounting)
    messages = service.messages(c.employee)["messages"]
    assert len(messages) == 1
    target = service.message_target(c.employee, messages[0]["id"])
    assert target["material_id"] == item["id"] and target["book_id"] == c.book
    assert target["period"] == "2026-10"
    c.plans.submit(c.employee, item["id"], no_activity())
    c.plans.review(c.reviewer, item["id"], review(action="return"))
    messages = service.messages(c.employee)["messages"]
    assert len(messages) == 2 and any(m["kind"] == "return" for m in messages)
    with pytest.raises(AccountingError):
        service.message_target(c.finance, messages[0]["id"])
    with c.db.transaction(actor=c.employee) as conn:
        assert conn.execute("SELECT count(*) FROM delivery_notifications").fetchone()[0] == 0
