from fastapi import APIRouter, Depends, Query, Request, Response

from .models import (
    BatchItemReason,
    CreateMonthlyOpenBatch,
    CreateReviewExport,
    RetryBatchRequest,
    ReviewExportPreview,
)


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

    @router.post("/review-export/preview")
    def preview_review_export(
        payload: ReviewExportPreview,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.preview_review_export(current, payload.company_ids, payload.period)

    @router.post("/review-export", status_code=202)
    def create_review_export(
        payload: CreateReviewExport,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.create_review_export(
            current,
            payload.company_ids,
            payload.period,
            payload.confirmed_manifest,
            payload.idempotency_key,
        )

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

    @router.post("/{batch_id}/items/{item_id}/cancel")
    def cancel_item(
        batch_id: str,
        item_id: str,
        payload: BatchItemReason,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.cancel_item(current, batch_id, item_id, payload.reason)

    @router.post("/{batch_id}/items/{item_id}/skip")
    def skip_item(
        batch_id: str,
        item_id: str,
        payload: BatchItemReason,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.skip_item(current, batch_id, item_id, payload.reason)

    @router.get("/{batch_id}/failures.csv")
    def failure_csv(
        batch_id: str,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        body = batches.failure_csv(current, batch_id)
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="batch-{batch_id}-failures.csv"'
            },
        )

    @router.post("/{batch_id}/retry")
    def retry_batch(
        batch_id: str,
        payload: RetryBatchRequest,
        current=Depends(actor_dependency),
        batches=Depends(service),
    ):
        return batches.retry(current, batch_id, payload)

    return router
