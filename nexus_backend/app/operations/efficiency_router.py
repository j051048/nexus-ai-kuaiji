from fastapi import APIRouter, Depends, Query, Request
from .efficiency_models import SessionStart, SessionCommand


def build_efficiency_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["人工效率测量"])

    def service(request: Request):
        return request.app.state.efficiency

    @router.get("/efficiency/sessions")
    def own(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0), current=Depends(actor_dependency), efficiency=Depends(service)):
        return efficiency.own(current, limit, offset)

    @router.post("/efficiency/sessions")
    def start(payload: SessionStart, current=Depends(actor_dependency), efficiency=Depends(service)):
        return efficiency.start(current, payload)

    @router.post("/efficiency/sessions/{session_id}/commands")
    def command(session_id: str, payload: SessionCommand, current=Depends(actor_dependency), efficiency=Depends(service)):
        return efficiency.command(current, session_id, payload)

    @router.get("/books/{book_id}/efficiency")
    def overview(book_id: str, current=Depends(actor_dependency), efficiency=Depends(service)):
        return efficiency.overview(current, book_id)

    return router
