from app.firm_tasks.models import AssignTasks, ClaimTask, RemindTasks, TaskRef
from app.firm_tasks.service import FirmTasksService
from test_business import business


def test_firm_inbox_assignment_claim_and_client_message(business):
    c = business
    from test_stage12_workspace import bank, workspace

    draft = bank(c)
    assert workspace(c).rebuild_projections(c.manager)["failed"] == 0
    service = FirmTasksService(c.db, c.service)
    inbox = service.inbox(c.manager)
    assert inbox["counts"]["review"] >= 1
    assert inbox["assignees"]
    ref = TaskRef(kind="review", ref=draft["id"])
    assigned = service.assign(
        c.manager,
        AssignTasks(
            tasks=[ref],
            assignee_id=c.actors["accountant"].user_id,
            due_on="2026-10-18",
            note="优先完成",
        ),
    )
    assert assigned["updated"] == 1
    claimed = service.claim(
        c.actors["accountant"], ClaimTask(kind="review", ref=draft["id"], note="已领取")
    )
    assert claimed["assignee_id"] == c.actors["accountant"].user_id
    reminded = service.remind(
        c.manager,
        RemindTasks(tasks=[ref], message="请在今天完成这张凭证的复核。"),
    )
    assert reminded["created"] == 1
    messages = service.messages(c.actors["accountant"])
    assert messages["messages"][0]["kind"] == "reminder"
    service.mark_message_read(c.actors["accountant"], messages["messages"][0]["id"])
