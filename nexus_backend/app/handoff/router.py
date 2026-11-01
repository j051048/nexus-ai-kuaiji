from fastapi import APIRouter, Depends, Request

from .models import HandoffAssign


def build_handoff_router(actor_dependency):
    router = APIRouter(prefix="/api/handoff", tags=["人工接手"])

    def service(request: Request):
        return request.app.state.handoff

    @router.get("")
    def queue(current=Depends(actor_dependency), handoff=Depends(service)):
        return handoff.queue(current)

    @router.get("/candidates")
    def candidates(
        book_id: str,
        role: str = "reviewer",
        current=Depends(actor_dependency),
        handoff=Depends(service),
    ):
        return handoff.candidates(current, book_id, role)

    @router.post("/tax-workpapers/{workpaper_id}/assign")
    def assign_tax_workpaper(
        workpaper_id: str,
        payload: HandoffAssign,
        current=Depends(actor_dependency),
        handoff=Depends(service),
    ):
        return handoff.assign(
            current,
            workpaper_id,
            payload.expected_version,
            payload.assignee_id,
            payload.due_on,
            payload.reason,
        )

    return router
