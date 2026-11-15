from fastapi import APIRouter, Depends, Query, Request

from .sync_stub import SyncEventWrite


def build_sync_stub_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/integrations/sync", tags=["本地同步替身"])

    def service(request: Request):
        return request.app.state.sync_stub

    @router.post("/events", status_code=201)
    def push(
        book_id: str,
        payload: SyncEventWrite,
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.push(actor, book_id, payload)

    @router.get("/events")
    def events(
        book_id: str,
        source_id: str = Query(min_length=1, max_length=80),
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        actor=Depends(actor_dependency),
        svc=Depends(service),
    ):
        return svc.list_events(actor, book_id, source_id=source_id, after=after, limit=limit)

    return router
