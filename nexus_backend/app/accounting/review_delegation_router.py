"""OPT06-C review delegation endpoints."""

from fastapi import APIRouter, Depends, Request

from .review_delegations import (
    ReviewDelegationRevoke,
    ReviewDelegationService,
    ReviewDelegationWrite,
)


def build_review_delegation_router(actor):
    router = APIRouter()

    def service(request: Request) -> ReviewDelegationService:
        return request.app.state.review_delegations

    @router.get("/api/books/{book_id}/review-delegations")
    def list_delegations(book_id: str, current=Depends(actor), svc=Depends(service)):
        return svc.list(current, book_id)

    @router.post("/api/books/{book_id}/review-delegations", status_code=201)
    def create_delegation(
        book_id: str,
        payload: ReviewDelegationWrite,
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.create(current, book_id, payload)

    @router.post("/api/books/{book_id}/review-delegations/{delegation_id}/approve")
    def approve_delegation(
        book_id: str,
        delegation_id: str,
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.approve(current, book_id, delegation_id)

    @router.post("/api/books/{book_id}/review-delegations/{delegation_id}/revoke")
    def revoke_delegation(
        book_id: str,
        delegation_id: str,
        payload: ReviewDelegationRevoke,
        current=Depends(actor),
        svc=Depends(service),
    ):
        return svc.revoke(current, book_id, delegation_id, payload)

    @router.post("/api/books/{book_id}/review-delegations/expire")
    def expire_delegations(book_id: str, current=Depends(actor), svc=Depends(service)):
        return svc.expire_due(current)

    return router
