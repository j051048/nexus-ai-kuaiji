from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from .models import ReceiptCreate, ReceiptImport, DeliveryNote


def build_delivery_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/delivery", tags=["人工交付回执与差异核验"])

    def service(request: Request):
        return request.app.state.delivery

    @router.get("")
    def overview(book_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1_000_000), actor=Depends(actor_dependency), delivery=Depends(service)):
        return delivery.overview(actor, book_id, limit, offset)

    @router.get("/exports/{export_id}")
    def detail(book_id: str, export_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1_000_000), actor=Depends(actor_dependency), delivery=Depends(service)):
        return delivery.detail(actor, book_id, export_id, limit, offset)

    @router.get("/exports/{export_id}/template")
    def template(book_id: str, export_id: str, format: Literal["json", "csv"] = "json", actor=Depends(actor_dependency), delivery=Depends(service)):
        content = delivery.template(actor, book_id, export_id, format)
        return Response(content, media_type="application/json" if format == "json" else "text/csv", headers={"Content-Disposition": f'attachment; filename="receipt-{export_id}.{format}"', "X-Content-Type-Options": "nosniff"})

    @router.post("/exports/{export_id}/backfill")
    def backfill(book_id: str, export_id: str, actor=Depends(actor_dependency), delivery=Depends(service)):
        return delivery.backfill_manifest(actor, book_id, export_id)

    @router.post("/exports/{export_id}/receipts", status_code=201)
    def receipt(book_id: str, export_id: str, payload: ReceiptCreate, actor=Depends(actor_dependency), delivery=Depends(service)):
        return delivery.create_receipt(actor, book_id, export_id, payload)

    @router.post("/exports/{export_id}/receipts/import", status_code=201)
    def import_receipt(book_id: str, export_id: str, payload: ReceiptImport, actor=Depends(actor_dependency), delivery=Depends(service)):
        return delivery.import_receipt(actor, book_id, export_id, payload)

    @router.post("/discrepancies/notes", status_code=201)
    def note(book_id: str, payload: DeliveryNote, actor=Depends(actor_dependency), delivery=Depends(service)):
        return delivery.add_note(actor, book_id, payload)

    return router
