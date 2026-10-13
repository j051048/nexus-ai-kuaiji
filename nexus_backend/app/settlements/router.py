from fastapi import APIRouter, Depends, Query, Request
from .models import (
    OpenItemCreate,
    AllocationPreview,
    AllocationConfirm,
    AllocationReverse,
    SplitRequest,
)
from .service import split_cents
from ..accounting.models import money_to_cents


def build_settlement_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/settlements", tags=["应收应付辅助核销"])

    def service(request: Request):
        return request.app.state.settlements

    @router.get("")
    def overview(
        book_id: str,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        return settlements.overview(current, book_id, limit, offset)

    @router.post("/items")
    def opening(
        book_id: str,
        payload: OpenItemCreate,
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        return settlements.open_item(current, book_id, payload)

    @router.post("/previews")
    def preview(
        book_id: str,
        payload: AllocationPreview,
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        return settlements.preview(current, book_id, payload)

    @router.post("/previews/{preview_id}/confirm")
    def confirm(
        book_id: str,
        preview_id: str,
        payload: AllocationConfirm,
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        return settlements.confirm(current, book_id, preview_id, payload)

    @router.post("/allocations/{allocation_id}/reverse")
    def reverse(
        book_id: str,
        allocation_id: str,
        payload: AllocationReverse,
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        return settlements.reversal(current, book_id, allocation_id, payload)

    @router.get("/banks/{source_id}/clearance")
    def clearance(
        book_id: str,
        source_id: str,
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        return settlements.clearance(current, book_id, source_id)

    @router.post("/split")
    def split(
        book_id: str,
        payload: SplitRequest,
        current=Depends(actor_dependency),
        settlements=Depends(service),
    ):
        with settlements.db.transaction(actor=current) as conn:
            settlements._book(conn, current, book_id)
        return {
            "total": payload.total,
            "parts": split_cents(
                money_to_cents(payload.total), [(part.key, part.weight) for part in payload.parts]
            ),
            "method": "integer_largest_remainder_stable_key",
        }

    return router
