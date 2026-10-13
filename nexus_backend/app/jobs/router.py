"""Queue HTTP surface shares current login and business authorization with OCR."""

from fastapi import APIRouter, Depends, Query, Request

from .requests import BatchRequest, CancelRequest, RetryRequest, ProposalRetryRequest, AutomationPolicyRequest


def build_queue_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["后台票据处理"])

    def queue(request: Request):
        return request.app.state.queue

    @router.get("/intelligence/companies/{company_id}/automation-policy")
    def automation_policy(company_id: str, current=Depends(actor_dependency), service=Depends(queue)):
        return service.automation.policy(current, company_id)

    @router.put("/intelligence/companies/{company_id}/automation-policy")
    def update_automation_policy(company_id: str, payload: AutomationPolicyRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.automation.update_policy(current, company_id, payload)

    @router.get("/intelligence/companies/{company_id}/job-metrics")
    def job_metrics(company_id: str, days: int = Query(7, ge=1, le=90), current=Depends(actor_dependency), service=Depends(queue)):
        return service.automation.metrics(current, company_id, days)

    @router.get("/intelligence/books/{book_id}/resume-events")
    def resume_events(book_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000), current=Depends(actor_dependency), service=Depends(queue)):
        return service.automation.events(current, book_id, limit, offset)

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

    @router.get("/intelligence/jobs/{job_id}")
    def read_job(job_id: str, current=Depends(actor_dependency), service=Depends(queue)):
        return service.get_job(current, job_id)

    @router.post("/intelligence/batches/{batch_id}/retry")
    def retry_batch(batch_id: str, payload: RetryRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.retry_batch(current, batch_id, payload.idempotency_key)

    @router.post("/intelligence/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, payload: CancelRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.cancel_job(current, job_id, payload.reason)

    @router.get("/intelligence/proposal-jobs")
    def proposal_jobs(company_id: str | None = None, book_id: str | None = None,
                      limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
                      current=Depends(actor_dependency), service=Depends(queue)):
        return service.proposals.list(current, company_id=company_id, book_id=book_id, limit=limit, offset=offset)

    @router.get("/intelligence/proposal-jobs/{job_id}")
    def proposal_job(job_id: str, current=Depends(actor_dependency), service=Depends(queue)):
        return service.proposals.get(current, job_id)

    @router.post("/intelligence/proposal-jobs/{job_id}/retry")
    def retry_proposal(job_id: str, payload: ProposalRetryRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.proposals.retry(current, job_id, payload.idempotency_key, payload.reason)

    @router.post("/intelligence/proposal-jobs/{job_id}/cancel")
    def cancel_proposal(job_id: str, payload: CancelRequest, current=Depends(actor_dependency), service=Depends(queue)):
        return service.proposals.cancel(current, job_id, payload.reason)

    return router
