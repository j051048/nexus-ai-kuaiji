"""Queue HTTP surface shares current login and business authorization with OCR."""

from fastapi import APIRouter, Depends, Query, Request

from .requests import BatchRequest, CancelRequest, RetryRequest


def build_queue_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["后台票据处理"])

    def queue(request: Request):
        return request.app.state.queue

    @router.post("/intelligence/batches", status_code=202)
    def enqueue(payload: BatchRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.enqueue(current, payload)

    @router.get("/intelligence/batches")
    def batches(company_id: str | None = None, event_id: str | None = None,
                limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
                current=Depends(actor_dependency), service=Depends(queue)):
        return service.list_batches(current, company_id=company_id, event_id=event_id, limit=limit, offset=offset)

    @router.get("/intelligence/batches/{batch_id}")
    def batch(batch_id: str, current=Depends(actor_dependency), service=Depends(queue)):
        return service.get_batch(current, batch_id)

    @router.post("/intelligence/jobs/{job_id}/retry")
    def retry_job(job_id: str, payload: RetryRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.retry_job(current, job_id, payload.idempotency_key)

    @router.post("/intelligence/batches/{batch_id}/retry")
    def retry_batch(batch_id: str, payload: RetryRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.retry_batch(current, batch_id, payload.idempotency_key)

    @router.post("/intelligence/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, payload: CancelRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.cancel_job(current, job_id, payload.reason)

    return router
