from fastapi import APIRouter, Depends, Request, Query

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

    @router.get("/sources/{kind}/{source_id}")
    def source(
        kind: str,
        source_id: str,
        expected_version: int = Query(ge=1),
        expected_fingerprint: str = Query(pattern=r"^[a-f0-9]{64}$"),
        current=Depends(actor_dependency),
        handoff=Depends(service),
    ):
        return handoff.source(current, kind, source_id, expected_version, expected_fingerprint)

    @router.post("/sources/{kind}/{source_id}/assign")
    def assign_source(
        kind: str,
        source_id: str,
        payload: HandoffAssign,
        current=Depends(actor_dependency),
        handoff=Depends(service),
    ):
        return handoff.assign(
            current,
            source_id,
            payload.expected_version,
            payload.assignee_id,
            payload.due_on,
            payload.reason,
            kind=kind,
            expected_fingerprint=payload.expected_fingerprint,
            expected_assignment_id=payload.expected_assignment_id,
        )

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
            expected_fingerprint=payload.expected_fingerprint,
            expected_assignment_id=payload.expected_assignment_id,
        )

    return router
