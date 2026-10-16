"""第一批企业写锁：关键写路径必须显式声明 company scope，且公司必须正确。"""

from app.business.models import ReturnRequest
from app.cases.models import CaseComment
from test_business import business, create, submitted  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_intelligence_workflow import ai_case  # noqa: F401
from test_invoice_imports import csv_bytes, invoices, row, upload  # noqa: F401
from test_stage8_cases import request, setup
from test_stage12_intake import attach_intake, confirmed, create_intake, intake
from test_stage17_monthly_workbench import month_case  # noqa: F401
from test_stage25_service_plans import plan, plans  # noqa: F401


def tap(db):
    """Record transaction kwargs while delegating to the real transaction."""
    calls = []
    original = db.transaction

    def wrapper(*args, **kwargs):
        calls.append(dict(kwargs))
        return original(*args, **kwargs)

    db.transaction = wrapper
    return calls


def writes(calls):
    return [call for call in calls if call.get("write")]


def assert_scoped(calls, company_id):
    scoped = writes(calls)
    assert scoped, "关键写路径至少应有一个写事务"
    assert all(call.get("write_scope") == "company" for call in scoped)
    assert all(call.get("company_id") == company_id for call in scoped)


def test_intake_writes_are_company_scoped(ai_case):
    c = ai_case
    service = intake(c)
    calls = tap(c.db)
    draft = create_intake(c, service)
    attached = attach_intake(c, service, draft)
    result = service.complete(c.actors["employee"], draft["id"], confirmed(attached))
    assert result["intake_id"] == draft["id"]
    assert_scoped(calls, c.company["id"])


def test_business_writes_are_company_scoped(business):
    c = business
    calls = tap(c.db)
    event = submitted(c)
    event = c.service.approve_department(c.actors["approver"], event["id"], event["version"])
    c.service.return_event(
        c.actors["finance"],
        event["id"],
        ReturnRequest(expected_version=event["version"], reason="金额与原件不符，请修改后重提"),
    )
    assert_scoped(calls, c.company["id"])


def test_case_commands_are_company_scoped(business):
    c = business
    cases, _event, _mapping, task = setup(c)
    calls = tap(c.db)
    cases.command(
        c.actors["accountant"],
        task["id"],
        "comment",
        request(c, task, CaseComment, audience="internal"),
    )
    assert_scoped(calls, c.company["id"])


def test_service_plan_writes_are_company_scoped(plans):
    c = plans
    calls = tap(c.db)
    c.plans.save_plan(c.owner, c.book, plan(c))
    assert_scoped(calls, c.company if isinstance(c.company, str) else c.company["id"])


def test_invoice_import_commit_is_company_scoped(invoices):
    c = invoices
    actor = c.actors["accountant"]
    created = upload(c, actor, csv_bytes([row("25442000000000000091")]))
    calls = tap(c.db)
    batch = c.invoices.create(actor, c.book["id"], created["id"])
    assert batch["row_count"] == 1
    scoped = [
        call
        for call in writes(calls)
        if call.get("write_scope") == "company" and call.get("company_id") == c.company["id"]
    ]
    assert scoped, "发票导入批次提交必须是企业范围写事务"
