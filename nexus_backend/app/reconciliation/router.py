from typing import Literal
from fastapi import APIRouter, Depends, Query, Request
from .models import ConfirmRequest


def build_reconciliation_router(actor_dependency):
    router = APIRouter(prefix="/api/reconciliation", tags=["业务与银行流水核对"])

    def service(request: Request):
        return request.app.state.reconciliation

    @router.get("/books/{book_id}/events")
    def events(book_id: str, status: Literal["unmatched", "matched", "all"] = "unmatched", limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), actor=Depends(actor_dependency), reconciliation=Depends(service)):
        return reconciliation.events(actor, book_id, limit, offset, status)

    @router.get("/books/{book_id}/events/{event_id}/candidates")
    def candidates(book_id: str, event_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), actor=Depends(actor_dependency), reconciliation=Depends(service)):
        return reconciliation.candidates(actor, book_id, event_id, limit, offset)

    @router.post("/confirm")
    def confirm(payload: ConfirmRequest, actor=Depends(actor_dependency), reconciliation=Depends(service)):
        return reconciliation.confirm(actor, payload)

    return router
