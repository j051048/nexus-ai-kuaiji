"""Missing monthly documents become private, actionable tasks before any event exists."""

import pytest

from app.accounting.directory import DirectoryService
from app.accounting.domain import AccountingError
from app.accounting.review import ReviewService
from app.cases.service import CaseService
from app.workspace.service import WorkspaceService
from test_business import business  # noqa: F401
from test_business_postgres import business_case, business_postgres  # noqa: F401
from test_stage17_monthly_workbench import month_case  # noqa: F401
from test_stage25_service_plans import generated, no_activity, plans, review  # noqa: F401


def workspace(c):
    return WorkspaceService(c.db, c.accounting, c.business,
                            ReviewService(c.db, c.accounting), CaseService(c.db, c.business))


def test_missing_material_task_changes_owner_after_submission_and_acceptance(plans):
    c = plans
    _, material = generated(c)
    tasks = workspace(c)
    own = tasks.list(c.employee, kind="material")
    assert own["pagination"]["total"] == 1
    assert own["tasks"][0]["ref"] == material["id"]
    assert own["tasks"][0]["book_id"] is None
    assert own["tasks"][0]["event_id"] is None
    assert own["tasks"][0]["due_on"] == material["due_on"]
    assert tasks.list(c.reviewer, kind="material")["pagination"]["total"] == 0
    c.plans.submit(c.employee, material["id"], no_activity())
    assert tasks.list(c.employee, kind="material")["pagination"]["total"] == 0
    waiting = tasks.list(c.employee, bucket="waiting", kind="material")
    assert waiting["tasks"][0]["state"] == "submitted"
    reviewing = tasks.list(c.reviewer, kind="material")
    assert reviewing["tasks"][0]["ref"] == material["id"]
    assert reviewing["tasks"][0]["book_id"] == c.book
    c.plans.review(c.reviewer, material["id"], review())
    assert tasks.list(c.employee, bucket="all", kind="material")["pagination"]["total"] == 0
    with pytest.raises(AccountingError):
        tasks.get(c.employee, "material", material["id"])


def test_material_scope_precedes_search_counts_pagination_and_revocation(plans):
    c = plans
    _, material = generated(c, count=3)
    tasks = workspace(c)
    page = tasks.list(c.employee, kind="material", limit=1, offset=1)
    assert page["pagination"]["total"] == 3 and len(page["tasks"]) == 1
    assert tasks.list(c.employee, kind="material", company_id=c.other_company)["pagination"]["total"] == 0
    DirectoryService(c.db).revoke_company_member(c.owner, c.company, c.employee.user_id)
    assert tasks.list(c.employee, bucket="all", kind="material")["pagination"]["total"] == 0
    with pytest.raises(AccountingError):
        tasks.get(c.employee, "material", material["id"])
