from fastapi import APIRouter, Depends, Request

from .review_policy import ReviewPolicyService
from .review_policy_models import (
    ReviewPolicyChange,
    PreparationClaim,
    ManualReviewApproval,
    ManualReviewPost,
    ManualReviewWithdrawal,
)
from ..posting.manual_review import ManualReviewService
from ..posting.models import ManualVoucher


def build_review_policy_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["人工复核政策与版本责任"])

    def policy(request: Request):
        return ReviewPolicyService(request.app.state.service.db)

    def manual(service=Depends(policy)):
        return ManualReviewService(service.db)

    @router.get("/books/{book_id}/review-policy")
    def get_policy(book_id: str, actor=Depends(actor_dependency), service=Depends(policy)):
        return service.get(actor, book_id)

    @router.post("/books/{book_id}/review-policy")
    def set_policy(
        book_id: str,
        payload: ReviewPolicyChange,
        actor=Depends(actor_dependency),
        service=Depends(policy),
    ):
        return service.configure(actor, book_id, payload)

    @router.post("/books/{book_id}/drafts/{draft_id}/claim-preparation")
    def claim(
        book_id: str,
        draft_id: str,
        payload: PreparationClaim,
        actor=Depends(actor_dependency),
        service=Depends(policy),
    ):
        return service.claim(actor, book_id, draft_id, payload)

    @router.get("/books/{book_id}/manual-review-requests")
    def list_manual(book_id: str, actor=Depends(actor_dependency), service=Depends(manual)):
        return service.list_requests(actor, book_id)

    @router.post("/posting/manual-requests", status_code=201)
    def prepare(payload: ManualVoucher, actor=Depends(actor_dependency), service=Depends(manual)):
        return service.prepare(actor, payload)

    @router.post("/posting/manual-requests/{identifier}/approve")
    def approve(
        identifier: str,
        payload: ManualReviewApproval,
        actor=Depends(actor_dependency),
        service=Depends(manual),
    ):
        return service.approve(actor, identifier, payload)

    @router.post("/posting/manual-requests/{identifier}/post", status_code=201)
    def post(
        identifier: str,
        payload: ManualReviewPost,
        actor=Depends(actor_dependency),
        service=Depends(manual),
    ):
        return service.post(actor, identifier, payload)

    @router.post("/posting/manual-requests/{identifier}/withdraw")
    def withdraw(
        identifier: str,
        payload: ManualReviewWithdrawal,
        actor=Depends(actor_dependency),
        service=Depends(manual),
    ):
        return service.withdraw(actor, identifier, payload)

    return router
