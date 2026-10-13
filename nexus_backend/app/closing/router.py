from fastapi import APIRouter, Depends, Request
from .models import CloseRequest, PreflightRequest, SnapshotReview


def build_closing_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/closing", tags=["月结预检与复核"])

    def service(request: Request):
        return request.app.state.closing

    @router.get("")
    def overview(book_id: str, actor=Depends(actor_dependency), closing=Depends(service)):
        return closing.overview(actor, book_id)

    @router.post("/preflights", status_code=201)
    def preflight(
        book_id: str,
        payload: PreflightRequest,
        actor=Depends(actor_dependency),
        closing=Depends(service),
    ):
        return closing.preflight(actor, book_id, payload)

    @router.get("/snapshots/{snapshot_id}")
    def snapshot(
        book_id: str, snapshot_id: str, actor=Depends(actor_dependency), closing=Depends(service)
    ):
        return closing.get_snapshot(actor, book_id, snapshot_id)

    @router.post("/snapshots/{snapshot_id}/review")
    def review(
        book_id: str,
        snapshot_id: str,
        payload: SnapshotReview,
        actor=Depends(actor_dependency),
        closing=Depends(service),
    ):
        return closing.review(actor, book_id, snapshot_id, payload)

    @router.post("/close")
    def close(
        book_id: str,
        payload: CloseRequest,
        actor=Depends(actor_dependency),
        closing=Depends(service),
    ):
        return closing.close(actor, book_id, payload)

    return router
