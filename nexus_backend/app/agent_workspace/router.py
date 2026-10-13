from fastapi import APIRouter, Depends, Query, Request

from .models import ExplanationRequest


def build_agent_workspace_router(actor_dependency):
    router = APIRouter(prefix="/api/agent-workspace", tags=["会计 Agent 工作空间"])

    def service(request: Request):
        return request.app.state.agent_workspace

    @router.get("/runs")
    def runs(state: str = Query("active", max_length=20), company_id: str | None = Query(None, min_length=1, max_length=100),
             q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=100),
             offset: int = Query(0, ge=0, le=1_000_000), current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.runs(current, state, company_id, q, limit, offset)

    @router.get("/summary")
    def summary(current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.summary(current)

    @router.get("/companies")
    def companies(q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=100),
                  offset: int = Query(0, ge=0, le=1_000_000), current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.companies(current, q, limit, offset)

    @router.get("/context/books/{book_id}/drafts/{draft_id}")
    def context(book_id: str, draft_id: str, current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.context(current, book_id, draft_id)

    @router.post("/context/books/{book_id}/drafts/{draft_id}/explain")
    def explain(book_id: str, draft_id: str, body: ExplanationRequest,
                current=Depends(actor_dependency), workspace=Depends(service)):
        return workspace.explain(current, book_id, draft_id, body.question)

    return router
