from fastapi import APIRouter, Depends, Query, Request

from .models import AssignTasks, ClaimTask, RemindTasks


def build_firm_tasks_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["事务所任务运营"])

    def service(request: Request):
        return request.app.state.firm_tasks

    @router.get("/firm/inbox")
    def inbox(
        bucket: str = Query("all", pattern=r"^(all|today|overdue|blocked|review|failed)$"),
        limit: int = Query(100, ge=1, le=200),
        current=Depends(actor_dependency),
        tasks=Depends(service),
    ):
        return tasks.inbox(current, bucket, limit)

    @router.post("/firm/tasks/assign")
    def assign(
        payload: AssignTasks,
        current=Depends(actor_dependency),
        tasks=Depends(service),
    ):
        return tasks.assign(current, payload)

    @router.post("/firm/tasks/claim")
    def claim(
        payload: ClaimTask,
        current=Depends(actor_dependency),
        tasks=Depends(service),
    ):
        return tasks.claim(current, payload)

    @router.post("/firm/tasks/remind")
    def remind(
        payload: RemindTasks,
        current=Depends(actor_dependency),
        tasks=Depends(service),
    ):
        return tasks.remind(current, payload)

    @router.get("/client/messages")
    def messages(
        limit: int = Query(50, ge=1, le=100),
        unread_only: bool = False,
        current=Depends(actor_dependency),
        tasks=Depends(service),
    ):
        return tasks.messages(current, limit, unread_only)

    @router.post("/client/messages/{message_id}/read")
    def read_message(
        message_id: str,
        current=Depends(actor_dependency),
        tasks=Depends(service),
    ):
        return tasks.mark_message_read(current, message_id)

    return router
