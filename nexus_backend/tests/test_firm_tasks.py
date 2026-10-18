from app.firm_tasks.models import AssignTasks, ClaimTask, RemindTasks, TaskRef
from app.firm_tasks.service import FirmTasksService
from test_business import business


def test_firm_inbox_assignment_claim_and_client_message(business):
    c = business
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "INSERT INTO workspace_task_projections(firm_id,company_id,kind,ref,book_id,event_id,"
            "draft_id,batch_id,applicant_id,department_id,assignee_id,recipient_id,created_by,"
            "submission_mode,ref_kind,ref_detail,source_open,state,title,due_on,updated_at,amount_cents) "
            "VALUES(?,?, 'review','draft-1',?,NULL,'draft-1',NULL,NULL,NULL,NULL,NULL,?,"
            "'','review','',0,'draft','待复核凭证','2026-12-01',?,100)",
            (
                c.manager.firm_id,
                c.company["id"],
                c.book["id"],
                c.actors["accountant"].user_id,
                "2026-10-18T00:00:00+00:00",
            ),
        )
    service = FirmTasksService(c.db, c.service)
    inbox = service.inbox(c.manager)
    assert inbox["counts"]["review"] >= 1
    assert inbox["assignees"]
    ref = TaskRef(kind="review", ref="draft-1")
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
        c.actors["accountant"], ClaimTask(kind="review", ref="draft-1", note="已领取")
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
