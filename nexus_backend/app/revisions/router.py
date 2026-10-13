from fastapi import APIRouter, Depends, Request, Query
from .models import RevisionSubmit, RevisionReview


def build_revisions_router(actor_dependency):
    router = APIRouter(prefix="/api/fact-revisions", tags=["事实修订"])

    @router.get("/context/{event_id}")
    def context(event_id: str, request: Request, actor=Depends(actor_dependency)):
        return request.app.state.revisions.context(actor, event_id)

    @router.get("")
    def list_revisions(
        request: Request,
        event_id: str | None = None,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
        actor=Depends(actor_dependency),
    ):
        return request.app.state.revisions.list(actor, event_id, limit, offset)

    @router.post("/events/{event_id}")
    def submit(
        event_id: str, body: RevisionSubmit, request: Request, actor=Depends(actor_dependency)
    ):
        return request.app.state.revisions.submit(actor, event_id, body)

    @router.get("/{revision_id}")
    def get(revision_id: str, request: Request, actor=Depends(actor_dependency)):
        return request.app.state.revisions.get(actor, revision_id)

    @router.post("/{revision_id}/review")
    def review(
        revision_id: str, body: RevisionReview, request: Request, actor=Depends(actor_dependency)
    ):
        return request.app.state.revisions.review(actor, revision_id, body)

    return router
