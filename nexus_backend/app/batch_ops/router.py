from fastapi import APIRouter, Depends, Query, Request

from .models import CreateMonthlyOpenBatch, RetryBatchRequest


def build_batch_ops_router(actor_dependency):
    router = APIRouter(prefix="/api/firm/batch-operations", tags=["批量运营任务"])

    def service(request: Request):
        return request.app.state.batch_operations

    @router.post("/monthly-open/preview")
    def preview_monthly_open(
        payload: CreateMonthlyOpenBatch,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.preview_monthly_open(current, payload)

    @router.post("/monthly-open", status_code=202)
    def create_monthly_open(
        payload: CreateMonthlyOpenBatch,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.create_monthly_open(current, payload)

    @router.get("")
    def list_batches(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100_000),
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.list(current, limit, offset)

    @router.get("/{batch_id}")
    def get_batch(
        batch_id: str,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.get(current, batch_id)

    @router.post("/{batch_id}/retry")
    def retry_batch(
        batch_id: str,
        payload: RetryBatchRequest,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.retry(current, batch_id, payload)

    return router
