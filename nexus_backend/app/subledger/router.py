from fastapi import APIRouter, Depends, Query, Request

from .models import (
    AllocationCreate,
    AllocationReverse,
    BankAdjustment,
    BankLineCreate,
    BankMatch,
    OpenItemCreate,
)


def build_subledger_router(actor_dependency):
    router = APIRouter(prefix="/api/subledger", tags=["B04子账"])

    def service(request: Request):
        return request.app.state.subledger

    @router.post("/items", status_code=201)
    def create_item(
        payload: OpenItemCreate, current=Depends(actor_dependency), subledger=Depends(service)
    ):
        return subledger.create_item(current, payload)

    @router.get("/items")
    def items(
        book_id: str = Query(min_length=1, max_length=100),
        kind: str | None = Query(default=None),
        as_of: str | None = Query(default=None),
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return {"items": subledger.list_items(current, book_id, kind, as_of)}

    @router.post("/allocations", status_code=201)
    def allocate(
        payload: AllocationCreate, current=Depends(actor_dependency), subledger=Depends(service)
    ):
        return subledger.allocate(current, payload)

    @router.post("/allocations/{allocation_id}/reverse", status_code=201)
    def reverse(
        allocation_id: str,
        payload: AllocationReverse,
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return subledger.reverse_allocation(current, allocation_id, payload)

    @router.get("/aging")
    def aging(
        book_id: str = Query(min_length=1, max_length=100),
        as_of: str = Query(min_length=10, max_length=10),
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return subledger.aging(current, book_id, as_of)

    @router.get("/control-reconciliation")
    def control(
        book_id: str = Query(min_length=1, max_length=100),
        as_of: str | None = Query(default=None),
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return subledger.control_reconciliation(current, book_id, as_of)

    @router.post("/bank-lines", status_code=201)
    def bank_line(
        payload: BankLineCreate, current=Depends(actor_dependency), subledger=Depends(service)
    ):
        return subledger.add_bank_line(current, payload)

    @router.post("/bank-lines/{line_id}/match")
    def bank_match(
        line_id: str,
        payload: BankMatch,
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return subledger.match_bank_line(current, line_id, payload)

    @router.post("/bank-adjustments", status_code=201)
    def bank_adjustment(
        payload: BankAdjustment,
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return subledger.add_bank_adjustment(current, payload)

    @router.get("/bank-reconciliation")
    def bank_reconciliation(
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        subledger=Depends(service),
    ):
        return subledger.bank_reconciliation(current, book_id)

    return router
