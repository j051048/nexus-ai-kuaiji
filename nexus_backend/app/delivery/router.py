from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from .models import ReceiptCreate, ReceiptImport, DeliveryNote
from .monthly_models import MonthlyPackageCreate, MonthlyPackageAcknowledge
from .followup_models import DeliveryGrantRequest, DeliveryRevokeRequest, DeliveryTaskRequest


def build_delivery_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/delivery", tags=["人工交付回执与差异核验"])

    def service(request: Request):
        return request.app.state.delivery

    def packages(request: Request):
        return request.app.state.monthly_packages

    def followups(request: Request):
        return request.app.state.delivery_followups

    @router.get("/monthly-followup")
    def followup_status(
        book_id: str,
        period: str = Query(min_length=7, max_length=7),
        actor=Depends(actor_dependency),
        followup=Depends(followups),
    ):
        return followup.status(actor, book_id, period)

    @router.post("/monthly-followup/recipient", status_code=201)
    def followup_configure(
        book_id: str,
        payload: DeliveryGrantRequest,
        actor=Depends(actor_dependency),
        followup=Depends(followups),
    ):
        return followup.configure(actor, book_id, payload)

    @router.post("/monthly-followup/recipient/revoke")
    def followup_revoke(
        book_id: str,
        payload: DeliveryRevokeRequest,
        actor=Depends(actor_dependency),
        followup=Depends(followups),
    ):
        return followup.revoke(actor, book_id, payload.period, payload)

    @router.post("/monthly-followup/tasks", status_code=201)
    def followup_task(
        book_id: str,
        payload: DeliveryTaskRequest,
        actor=Depends(actor_dependency),
        followup=Depends(followups),
    ):
        return followup.queue(actor, book_id, payload)

    @router.get("/monthly-package")
    def monthly_readiness(book_id: str, actor=Depends(actor_dependency), monthly=Depends(packages)):
        return monthly.readiness(actor, book_id)

    @router.post("/monthly-package", status_code=201)
    def monthly_create(
        book_id: str,
        payload: MonthlyPackageCreate,
        actor=Depends(actor_dependency),
        monthly=Depends(packages),
    ):
        return monthly.create(actor, book_id, payload)

    @router.get("/monthly-package/{package_id}")
    def monthly_detail(
        book_id: str, package_id: str, actor=Depends(actor_dependency), monthly=Depends(packages)
    ):
        return monthly.detail(actor, book_id, package_id)

    @router.post("/monthly-package/{package_id}/open")
    def monthly_open(
        book_id: str, package_id: str, actor=Depends(actor_dependency), monthly=Depends(packages)
    ):
        return monthly.open(actor, book_id, package_id)

    @router.get("/monthly-package/{package_id}/download")
    def monthly_download(
        book_id: str, package_id: str, actor=Depends(actor_dependency), monthly=Depends(packages)
    ):
        content = monthly.download(actor, book_id, package_id)
        # Package IDs are server-generated UUIDs; use a constant attachment name.
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="monthly-delivery.zip"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    @router.post("/monthly-package/{package_id}/acknowledge")
    def monthly_acknowledge(
        book_id: str,
        package_id: str,
        payload: MonthlyPackageAcknowledge,
        actor=Depends(actor_dependency),
        monthly=Depends(packages),
    ):
        return monthly.acknowledge(actor, book_id, package_id, payload)

    @router.get("/notifications")
    def notification_states(
        book_id: str,
        period: str = Query(min_length=7, max_length=7),
        actor=Depends(actor_dependency),
        follow=Depends(followups),
    ):
        return follow.notifications(actor, book_id, period)

    @router.get("")
    def overview(
        book_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        delivery=Depends(service),
    ):
        return delivery.overview(actor, book_id, limit, offset)

    @router.get("/exports/{export_id}")
    def detail(
        book_id: str,
        export_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        delivery=Depends(service),
    ):
        return delivery.detail(actor, book_id, export_id, limit, offset)

    @router.get("/exports/{export_id}/template")
    def template(
        book_id: str,
        export_id: str,
        format: Literal["json", "csv"] = "json",
        actor=Depends(actor_dependency),
        delivery=Depends(service),
    ):
        content = delivery.template(actor, book_id, export_id, format)
        return Response(
            content,
            media_type="application/json" if format == "json" else "text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="receipt-{export_id}.{format}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/exports/{export_id}/backfill")
    def backfill(
        book_id: str, export_id: str, actor=Depends(actor_dependency), delivery=Depends(service)
    ):
        return delivery.backfill_manifest(actor, book_id, export_id)

    @router.post("/exports/{export_id}/receipts", status_code=201)
    def receipt(
        book_id: str,
        export_id: str,
        payload: ReceiptCreate,
        actor=Depends(actor_dependency),
        delivery=Depends(service),
    ):
        return delivery.create_receipt(actor, book_id, export_id, payload)

    @router.post("/exports/{export_id}/receipts/import", status_code=201)
    def import_receipt(
        book_id: str,
        export_id: str,
        payload: ReceiptImport,
        actor=Depends(actor_dependency),
        delivery=Depends(service),
    ):
        return delivery.import_receipt(actor, book_id, export_id, payload)

    @router.post("/discrepancies/notes", status_code=201)
    def note(
        book_id: str,
        payload: DeliveryNote,
        actor=Depends(actor_dependency),
        delivery=Depends(service),
    ):
        return delivery.add_note(actor, book_id, payload)

    return router
