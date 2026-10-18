from fastapi import APIRouter, Depends, Query, Request

from .models import CloseCommand, ClosePreflight


def build_close_router(actor_dependency):
    router = APIRouter(prefix="/api/b04-close", tags=["B04月结"])

    def service(request: Request):
        return request.app.state.b04_close

    @router.post("/preflight", status_code=201)
    def preflight(
        payload: ClosePreflight, current=Depends(actor_dependency), close=Depends(service)
    ):
        return close.preflight(current, payload)

    @router.post("/{run_id}/review")
    def review(
        run_id: str,
        payload: CloseCommand,
        current=Depends(actor_dependency),
        close=Depends(service),
    ):
        return close.review(current, run_id, payload)

    @router.post("/{run_id}/lock")
    def lock(
        run_id: str,
        payload: CloseCommand,
        current=Depends(actor_dependency),
        close=Depends(service),
    ):
        return close.lock(current, run_id, payload)

    @router.post("/{run_id}/publish")
    def publish(
        run_id: str,
        payload: CloseCommand,
        current=Depends(actor_dependency),
        close=Depends(service),
    ):
        return close.publish(current, run_id, payload)

    @router.post("/{run_id}/reopen")
    def reopen(
        run_id: str,
        payload: CloseCommand,
        current=Depends(actor_dependency),
        close=Depends(service),
    ):
        return close.reopen(current, run_id, payload)

    @router.get("/{run_id}")
    def get(run_id: str, current=Depends(actor_dependency), close=Depends(service)):
        return close.get(current, run_id)

    @router.get("")
    def list_runs(
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        close=Depends(service),
    ):
        return {"runs": close.list(current, book_id)}

    return router
